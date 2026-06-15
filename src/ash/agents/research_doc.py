"""Research-output publishing (plan §10.2, A5).

The Research agent produces an `ImplementationPlan`; this module renders it to Markdown and
publishes it to a configurable destination — a local file (default), or a comment on the source
connector's issue. Separate from the agent so the render/publish path is unit-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ash.schemas import ImplementationPlan

ResearchSink = Literal["file", "comment", "none"]


def render_research_doc(plan: ImplementationPlan, *, title: str = "") -> str:
    """Render an implementation plan as a Markdown research document."""
    lines = [f"# Research — {title}" if title else "# Research", "", plan.summary, ""]
    if plan.relevant_files:
        lines += ["## Relevant files", *(f"- `{f}`" for f in plan.relevant_files), ""]
    if plan.new_files:
        lines += ["## New files", *(f"- `{f}`" for f in plan.new_files), ""]
    if plan.steps:
        lines += ["## Steps", *(f"{i}. {s}" for i, s in enumerate(plan.steps, 1)), ""]
    if plan.open_questions:
        lines += ["## Open questions", *(f"- {q}" for q in plan.open_questions), ""]
    lines.append("_Produced by the ASH Research agent._")
    return "\n".join(lines)


async def publish_research_doc(
    *,
    mode: ResearchSink,
    runtime_dir: Path,
    run_id: str,
    doc: str,
    integration_id: int | None = None,
    item_id: str = "",
) -> str | None:
    """Publish the research doc per `mode`. Returns a reference (path/URL) or None if not published.

    - ``file``    — write `<runtime_dir>/research/<run_id>.md` (always available).
    - ``comment`` — post on the source connector's issue (needs `integration_id` + `item_id`);
                    falls back to a file if no source/item is available.
    - ``none``    — do not publish.
    """
    if mode == "none":
        return None
    if mode == "comment" and integration_id and item_id and item_id not in ("", "upload", "-"):
        from ash.integrations.service import provider_for

        provider = await provider_for(integration_id)
        return await provider.post_comment(item_id, doc)
    # file (default, and the fallback for comment when there's no issue to comment on)
    dest = runtime_dir / "research"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{run_id}.md"
    path.write_text(doc)
    return str(path)
