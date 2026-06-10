"""Research / Spike agent — read-only. Produces a grounded ImplementationPlan.

Single-shot and bounded: we orient the model with a shallow repo tree and ripgrep hits for keywords
derived from the issue/spec, rather than a multi-turn tool loop. This keeps it cheap, deterministic,
and friendly to models without robust tool-calling. No writes happen here (cheap to re-run).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..llm import LLMClient
from ..schemas import ImplementationPlan, Spec
from ..tools import code_intel

_SYSTEM = """You are a senior engineer doing a research spike. Given a spec and a real view of the \
repository, produce a concrete, grounded implementation plan. Reference ACTUAL files/paths you see \
in the provided repo overview and search hits. Prefer the smallest change that satisfies the spec. \
Do not invent files that aren't plausible for this codebase. List open questions instead of \
guessing."""

_STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "view",
    "list",
    "show",
    "add",
    "feature",
    "bug",
}


def _keywords(spec: Spec, issue_title: str) -> list[str]:
    text = f"{issue_title} {spec.epic.title} {spec.epic.summary}"
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text.lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:6]


def research(llm: LLMClient, worktree: Path, issue_title: str, spec: Spec) -> ImplementationPlan:
    tree = code_intel.repo_tree(worktree)
    hits: list[str] = []
    for kw in _keywords(spec, issue_title):
        hits += code_intel.search(worktree, kw, max_results=8)
    hits = hits[:40]

    user = (
        f"## Spec\n{spec.model_dump_json(indent=2)}\n\n"
        f"## Repository overview (top levels)\n{tree}\n\n"
        f"## Search hits for issue keywords\n" + ("\n".join(hits) or "(none)") + "\n\n"
        "Produce the implementation plan."
    )
    return llm.generate_structured(
        role="dev", system=_SYSTEM, user=user, schema=ImplementationPlan
    ).parsed
