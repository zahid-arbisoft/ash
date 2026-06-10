"""Dev / Coding agent — produces real file edits from the plan, then applies them in the worktree.

v1 is deliberately conservative: it returns FULL file contents (not diffs — more reliable for
smaller models) for a bounded set of files, and we apply them inside the isolated worktree. The
result is what the PR carries (plan §4c: code → PR). Runs on an agent branch in the fork, as a
draft PR, so blast radius is contained.
"""

from __future__ import annotations

from pathlib import Path

from ..llm import LLMClient
from ..schemas import CodeChange, EditAction, ImplementationPlan, Spec
from ..tools import code_intel

_SYSTEM = """You are a senior engineer implementing a planned change. You are given the spec, the \
implementation plan, and the current contents of the relevant files. Produce the MINIMAL set of \
file edits that implements the plan.

Rules:
- Return the FULL new content for each file you create or modify (never a diff/patch).
- Keep changes small and focused; match the surrounding code style.
- Only touch files that are necessary. Prefer modifying listed files over inventing new ones.
- If you cannot safely implement something, make the smallest correct partial change and note it."""

_MAX_FILES = 4


def code(llm: LLMClient, worktree: Path, spec: Spec, plan: ImplementationPlan) -> CodeChange:
    # gather current contents of the files the plan wants to touch (bounded)
    targets = (plan.relevant_files + plan.new_files)[:_MAX_FILES]
    current = []
    for rel in targets:
        body = code_intel.read_file(worktree, rel)
        current.append(f"### {rel}\n```\n{body or '(file does not exist yet)'}\n```")

    user = (
        f"## Spec\n{spec.model_dump_json(indent=2)}\n\n"
        f"## Implementation plan\n{plan.model_dump_json(indent=2)}\n\n"
        f"## Current file contents\n" + ("\n\n".join(current) or "(none provided)") + "\n\n"
        "Produce the code change (full file contents for each edit)."
    )
    return llm.generate_structured(role="dev", system=_SYSTEM, user=user, schema=CodeChange).parsed


def apply_change(worktree: Path, change: CodeChange) -> list[str]:
    """Write the edits into the worktree. Returns the list of written paths (sandboxed)."""
    written: list[str] = []
    root = worktree.resolve()
    for edit in change.edits:
        target = (worktree / edit.path).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError(f"edit path escapes worktree: {edit.path}")
        if edit.action == EditAction.modify and not target.exists():
            # treat as create if the model mislabeled it
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.content)
        written.append(edit.path)
    return written
