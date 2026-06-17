"""Board sink — where SPECS/TICKETS live for client oversight (plan §4c).

Specs do NOT go in the PR; they go to the Board. Today the Board is local files; later it becomes
Jira / Plane / Trello via the same `publish_spec` interface (plan §8). Selecting the board is a
per-project/per-client config choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..schemas import Spec


class Board(Protocol):
    def publish_spec(self, item_id: str, issue_url: str, spec: Spec) -> str:
        """Publish a spec/tickets to the board. Returns a reference (path/URL/id)."""
        ...


class FileBoard:
    """Default board: writes the spec as JSON + a human-readable Markdown card to runtime/."""

    def __init__(self, board_dir: Path):
        self.board_dir = board_dir
        self.board_dir.mkdir(parents=True, exist_ok=True)

    def publish_spec(self, item_id: str, issue_url: str, spec: Spec) -> str:
        safe = str(item_id).replace("/", "_")
        (self.board_dir / f"issue-{safe}.json").write_text(spec.model_dump_json(indent=2))
        md_path = self.board_dir / f"issue-{safe}.md"
        md_path.write_text(self._render_md(item_id, issue_url, spec))
        return str(md_path)

    @staticmethod
    def _render_md(item_id: str, issue_url: str, spec: Spec) -> str:
        lines = [
            f"# [{spec.epic.title}](#) — item {item_id}",
            "",
            f"Source: {issue_url}",
            "",
            spec.epic.summary,
            "",
            f"**Business goal:** {spec.epic.business_goal}",
            "",
            "## Acceptance criteria",
            *[f"- [ ] {c}" for c in spec.epic.acceptance_criteria],
            "",
            "## Tickets",
        ]
        for t in spec.tickets:
            deps = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
            lines += [f"### {t.id} · {t.type.value} · {t.title}{deps}", t.description, ""]
            if getattr(t, "implementation_notes", ""):
                lines += ["**Implementation notes**", "", t.implementation_notes, ""]
            if getattr(t, "affected_files", None):
                files = ", ".join(f"`{f}`" for f in t.affected_files)
                lines += [f"**Affected files:** {files}", ""]
            if getattr(t, "data_model_changes", None):
                lines += ["**Data model changes**", *[f"- {d}" for d in t.data_model_changes], ""]
            if getattr(t, "api_changes", None):
                lines += ["**API changes**", *[f"- {a}" for a in t.api_changes], ""]
            if t.acceptance_criteria:
                crit = [f"- [ ] {c}" for c in t.acceptance_criteria]
                lines += ["**Acceptance criteria**", *crit, ""]
            if getattr(t, "out_of_scope", ""):
                lines += [f"**Out of scope:** {t.out_of_scope}", ""]
        lines += ["## Risks"]
        lines += [
            f"- **{r.severity.value}** — {r.description} → _{r.mitigation}_"
            for r in spec.risk_assessment
        ]
        return "\n".join(lines) + "\n"


def get_board(board_dir: Path) -> Board:
    """Factory — returns the configured board. Only FileBoard exists today."""
    return FileBoard(board_dir)
