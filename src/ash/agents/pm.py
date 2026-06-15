"""PM agent (v2) — requirements → spec → (human review) → tickets pushed to the task tool.

Two-phase design:
- **PMAgent** (`pm` node): ingest requirements, generate Spec, publish to the Board for oversight.
- **PMPublishAgent** (`pm_publish` node): interrupt for human review; on approval push tickets to
  the selected sink (Plane / Jira / file board).

Splitting into two nodes means the spec is checkpointed before the HITL gate, so the LLM call is
never repeated on resume.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from ash.agents.base import BaseAgent
from ash.agents.spec_validator import validate_spec
from ash.clients.board import get_board
from ash.config.settings import load_project
from ash.db.base import get_sessionmaker
from ash.db.runs import persist_spec_record, update_spec_ticket_refs
from ash.documents import read_documents
from ash.graph.state import WorkflowState
from ash.schemas import Spec
from ash.sinks.service import resolve_task_sink

# ── Quality rules shared by both modes (the org's spec-quality standard, enforced in-prompt) ──

_QUALITY_RULES = """\

Hard rules — treat a spec that breaks any of these as wrong:
1. NEVER invent context. Do not assume an existing app, framework, component library, database, or \
codebase unless the requirements explicitly state it. If a technology or architecture choice is \
needed, make it a SPIKE ticket or record it in open_questions — never silently bake an unstated \
stack into the tickets.
2. Honor EVERY explicit signal. If the requirements name a reference product, UX tone, design \
principle, constraint, or guardrail, it MUST appear in at least one acceptance criterion or \
ticket. Named restrictions ("no screen recording", "no external API calls") must appear as \
explicit negative conditions in the epic acceptance criteria — not merely be absent from the spec.
3. Calibrate scope to the ask. If the requirements ask for a prototype, proof-of-concept, or MVP, \
scope tickets to the minimum that proves the concept — do not design a full production system. A \
prototype targets ONE platform unless multi-platform is explicitly required; do not add cross-\
platform or multi-OS adapters speculatively.
4. Flag unknowns; do not guess them. Before finishing, audit every item: Is any external format, \
API, integration, UI framework, or target platform referenced but not defined? Record ALL of these \
in open_questions. On a greenfield project, an empty open_questions is almost always a sign of \
under-auditing — external integrations, undecided stacks, and undefined formats must be listed.
5. Dependencies must form an acyclic graph. A ticket may only depend on tickets it genuinely needs \
completed first, referenced by real ids. Foundational tickets (shared infrastructure, data layer, \
encryption) must have NO dependencies. No cycles, no self-references.
6. Risk assessment must be complete. For any flow that sends data to an external system, consider \
privacy, compliance, legal, and data-residency risks — not only functional ones. Any tool that \
monitors or collects user activity (window titles, keystrokes, location, browsing history) \
requires user consent and disclosure under privacy law (GDPR Art. 13, CCPA, local labor law) \
even for internal tools — include a consent/disclosure risk entry whenever activity monitoring \
is involved.

Before finishing, self-check the spec against all six rules. Verify open_questions is populated \
for any undecided technology, undefined external interface, or named unknown in the requirements.\
"""

# ── story mode: single (default) vs multiple (decision #26 / F1) ──

_STORY_MODE_SINGLE = """\

STORY MODE — SINGLE (important): the client wants this delivered as ONE story/ticket. Produce \
exactly ONE implementation ticket that covers the whole ask end-to-end (you MAY additionally add a \
single SPIKE ticket if investigation is genuinely required first). Do NOT split the work into \
multiple parallel implementation tickets. Keep the epic and technical spec as usual.\
"""

_STORY_MODE_MULTIPLE = """\

STORY MODE — MULTIPLE: break the work into several small, independently shippable implementation \
tickets (stories), each delivered as its own PR. Use dependencies to express ordering.\
"""

# ── raw_to_spec: PM builds a full spec + breaks it into implementation tickets ──

_SYSTEM_RAW_TO_SPEC = """\
You are a senior product/technical lead acting as a Spec Builder. You receive raw requirements \
(issue text and/or uploaded documents) and produce a rigorous, implementable specification.

Your spec must:
- Restate the problem and desired outcome in clear language (don't just echo the input).
- Define concrete, testable acceptance criteria.
- Surface edge cases the author likely didn't consider.
- Propose a sound technical approach and name the areas of the codebase likely affected.
- Break the work into small, independently shippable implementation tickets (stories).
- Mark any ticket that needs investigation before implementation as a SPIKE: set its type to \
"spike" and `needs_research` to true (the Research agent will pick those up).
- Assess risks honestly with severity and mitigations.

Ticket quality bar — each ticket description must:
- State what needs to be built and why (user/system motivation).
- Name the specific files, modules, endpoints, or schema fields involved.
- Describe the implementation approach and any key design constraints.
- Call out what is out of scope for this ticket.
- Note any gotchas, edge cases, or cross-ticket dependencies.
A developer must be able to pick up any ticket cold without asking follow-up questions.

Be specific and grounded. Prefer the smallest change that fully satisfies the requirements.\
"""

_USER_RAW_TO_SPEC = """\
Produce a specification and implementation tickets from the following raw requirements.

{context}

--- end requirements ---\
"""

# ── spec_ready: PM receives a pre-written spec and extracts implementation tickets ──

_SYSTEM_SPEC_READY = """\
You are a senior PM. You receive a pre-written specification document. The spec already describes \
what needs to be built — your job is NOT to create new requirements, but to:
1. Parse and structure the spec into the standard output format (epic summary, technical approach).
2. Break the specified work into small, independently shippable implementation tickets (stories) \
   that a developer can pick up and execute without ambiguity.
3. Mark any ticket that needs investigation or research first as a SPIKE (`needs_research = true`).
4. Identify risks and mitigations that are evident from the spec.

Ticket quality bar — each ticket description must:
- State what needs to be built and why (draw directly from the provided spec).
- Name the specific files, modules, endpoints, or schema fields the spec mentions.
- Describe the implementation approach and any constraints the spec defines.
- Call out what is out of scope for this ticket.
- Note any gotchas, edge cases, or cross-ticket dependencies visible in the spec.
A developer must be able to pick up any ticket cold without re-reading the full spec.

Stay faithful to the provided spec. Do not invent features or requirements not mentioned.\
"""

_USER_SPEC_READY = """\
The following is a pre-written specification. Structure it and extract implementation tickets.

{context}

--- end specification ---\
"""

# ── correction round: feed deterministic validation errors back for one self-fix ──

_USER_CORRECTION = """\
Your previous spec failed automated structural validation. Return the corrected, complete spec — \
keep everything that was already correct and fix ONLY the listed problems.

Validation errors:
{issues}

Your previous spec (JSON):
{prev_spec}

Original requirements (for reference):
{context}

--- end ---\
"""


class PMAgent(BaseAgent):
    """Phase 1: ingest requirements, generate Spec, write to the Board. No ticket push yet."""

    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        project = load_project(state.project)

        context = await self._gather(state)
        if not context.strip():
            return {"pm": {"error": "no requirements: provide an issue or attachments"}}

        if state.intake_mode == "spec_ready":
            system, user = _SYSTEM_SPEC_READY, _USER_SPEC_READY
        else:  # raw_to_spec (raw_to_dev never reaches PM)
            system, user = _SYSTEM_RAW_TO_SPEC, _USER_RAW_TO_SPEC
        system += _QUALITY_RULES
        system += _STORY_MODE_SINGLE if state.story_mode == "single" else _STORY_MODE_MULTIPLE

        spec = await self.generate(Spec, system=system, user=user.format(context=context))
        spec = await self._validate_and_repair(spec, system=system, context=context)

        item_id = state.item_id or "upload"
        board = get_board(project.runtime_dir / "board")
        board_ref = await asyncio.to_thread(board.publish_spec, item_id, state.issue_url, spec)

        # Persist the spec for the searchable PM-runs view (best-effort — never fail the run).
        try:
            await persist_spec_record(
                run_id=state.run_id,
                project=state.project,
                item_id=item_id,
                intake_mode=state.intake_mode,
                spec=spec,
                board_ref=board_ref,
            )
        except Exception:  # noqa: BLE001 — persistence is best-effort
            pass

        spikes = [t.id for t in spec.tickets if t.needs_research]
        mode_label = "Spec extracted" if state.intake_mode == "spec_ready" else "Spec generated"
        note = f"{mode_label} ({len(spec.tickets)} ticket(s)) — awaiting your review"
        if spikes:
            note += f"; spikes: {', '.join(spikes)}"

        return {
            "pm": {
                "spec": spec,
                "board_ref": board_ref,
                "note": note,
                "tokens": dict(self._usage),
            }
        }

    async def _gather(self, state: WorkflowState) -> str:
        parts: list[str] = []
        if state.raw_issue is not None:
            parts.append(f"# {state.raw_issue.title}\n\n{state.raw_issue.body}")
        if state.attachments:
            parts.append(await asyncio.to_thread(read_documents, state.attachments))
        return "\n\n".join(p for p in parts if p.strip())

    async def _validate_and_repair(self, spec: Spec, *, system: str, context: str) -> Spec:
        """Run deterministic validation; on failure, do one self-correction round.

        If problems remain after the correction round, surface them in `open_questions` so a human
        sees them at the review gate — never ship a structurally broken spec silently.
        """
        errors = validate_spec(spec)
        if not errors:
            return spec

        correction = _USER_CORRECTION.format(
            issues="\n".join(f"- {e}" for e in errors),
            prev_spec=spec.model_dump_json(indent=2),
            context=context,
        )
        repaired = await self.generate(Spec, system=system, user=correction)

        remaining = validate_spec(repaired)
        if remaining:
            repaired.open_questions = [
                *repaired.open_questions,
                "Automated validation still flagged issues after one correction round "
                f"(needs human review): {'; '.join(remaining)}",
            ]
        return repaired


class PMPublishAgent(BaseAgent):
    """Phase 2: interrupt for human review; push tickets to the connector on approval."""

    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        spec = state.pm.spec
        if spec is None:
            # PM failed or produced nothing — don't interrupt the user, just propagate the failure.
            return {}

        # Pause — LangGraph checkpoints here; the UI shows Approve / Reject.
        # `interrupt()` returns the value passed to Command(resume=...) on the resume call.
        # The decision is either a plain string ("approve"/"reject") or a dict carrying the
        # human's per-story selection (decision #26 / §4.2): {"action": "approve",
        # "stories": ["T1", "T3"]} — only those tickets become stories.
        raw_decision: Any = interrupt("spec_review")
        if isinstance(raw_decision, dict):
            action = str(raw_decision.get("action", "approve"))
            selection = raw_decision.get("stories")
            story_selection = [str(s) for s in selection] if isinstance(selection, list) else None
        else:
            action = str(raw_decision)
            story_selection = None

        if action == "reject":
            return {
                "pm": {
                    "spec": spec,
                    "board_ref": state.pm.board_ref,
                    "ticket_refs": [],
                    "note": "ticket push cancelled by reviewer",
                }
            }

        project = load_project(state.project)
        board_dir = project.runtime_dir / "board"
        spikes = [t.id for t in spec.tickets if t.needs_research]
        try:
            async with get_sessionmaker()() as session:
                sink = await resolve_task_sink(
                    session, sink_id=state.task_sink_id, board_dir=board_dir
                )
            refs = await sink.publish(spec)
            note = f"{len(refs)} ticket(s) pushed"
            ticket_refs = [r.url or r.id for r in refs]
            try:
                await update_spec_ticket_refs(state.run_id, ticket_refs)
            except Exception:  # noqa: BLE001 — persistence is best-effort
                pass
        except Exception as exc:  # noqa: BLE001 — keep spec; report push failure
            note = f"spec approved, but ticket push failed: {type(exc).__name__}: {exc}"
            ticket_refs = []
        if spikes:
            note += f"; spikes for research: {', '.join(spikes)}"

        return {
            "pm": {
                "spec": spec,
                "board_ref": state.pm.board_ref,
                "ticket_refs": ticket_refs,
                "story_selection": story_selection,
                "note": note,
                "tokens": dict(self._usage),
            }
        }
