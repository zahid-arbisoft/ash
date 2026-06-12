"""Default task sink — write tickets as Markdown/JSON files to the project's board dir."""

from __future__ import annotations

from pathlib import Path

from ash.schemas import Spec
from ash.sinks.base import TicketRef


class FileBoardSink:
    kind = "file"

    def __init__(self, board_dir: Path) -> None:
        self._dir = board_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    async def publish(self, spec: Spec) -> list[TicketRef]:
        refs: list[TicketRef] = []
        for t in spec.tickets:
            path = self._dir / f"ticket-{t.id}.md"
            spike = " · **SPIKE (needs research)**" if t.needs_research else ""
            body = (
                f"# {t.id} · {t.type.value} · {t.title}{spike}\n\n"
                f"{t.description}\n\n"
                + (
                    "## Acceptance criteria\n"
                    + "\n".join(f"- [ ] {c}" for c in t.acceptance_criteria)
                )
                + (f"\n\n_Depends on: {', '.join(t.dependencies)}_" if t.dependencies else "")
                + "\n"
            )
            path.write_text(body)
            refs.append(TicketRef(id=t.id, url=str(path), sink=self.kind))
        return refs
