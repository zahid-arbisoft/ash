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
import logging
from typing import Any

from pydantic import BaseModel
from langgraph.types import interrupt

from ash.agents.base import BaseAgent
from ash.agents.estimates import repair_spec_estimates, repair_ticket_estimates
from ash.agents.spec_validator import validate_spec
from ash.clients.board import get_board
from ash.config.settings import load_project
from ash.db.base import get_sessionmaker
from ash.db.runs import persist_spec_record, update_spec_ticket_refs
from ash.documents import read_documents
from ash.graph.state import WorkflowState
from ash.schemas import Spec, Ticket
from ash.sinks.service import resolve_task_sink

logger = logging.getLogger(__name__)

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

Ticket quality bar — each ticket MUST be detailed:
- State what needs to be built and why (user/system motivation).
- Name specific files, modules, endpoints, or schema fields involved.
- Describe the implementation approach and key design decisions.
- Provide acceptance criteria (at least 2-3).
- Provide traditional and LLM-assisted estimates (llm_estimate ≈ estimate / 6).
- Call out what is out of scope.
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

# ── per-ticket elaboration (decision #27): give each ticket its own generation budget ──

_SYSTEM_ELABORATE = """\
You are a senior tech lead writing ONE implementation ticket in full detail for a developer who \
will pick it up cold. You are given the full source requirements/spec, the epic, and a short \
outline of the specific ticket to expand.

Expand the outline into a thorough, self-contained ticket. CRITICAL rules:
- PRESERVE specifics from the source spec — copy real model field names, endpoint paths, \
task/queue names, file paths, and acceptance criteria into this ticket. Do NOT summarise it away.
- `description`: a complete narrative of WHAT to build and WHY (several sentences).
- `implementation_notes`: the HOW — concrete, ordered steps referencing real modules/classes/\
functions; key design decisions and constraints. This must be substantial, not one line.
- `affected_files`: the actual files/paths this ticket touches.
- `api_changes` / `data_model_changes`: concrete endpoint and model/field changes from the spec \
that belong to THIS ticket (empty lists if genuinely none).
- `acceptance_criteria`: 2-5 concrete, testable conditions, drawn from the spec where present.
- `out_of_scope`: what this ticket deliberately excludes (to bound it against sibling tickets).
- `estimate`: traditional time estimate without AI tooling (e.g. "S", "1d", "3d", "1w").
- `estimate_days`: the same estimate as a decimal number of person-days (S→0.5, M→2, L→5, \
"3d"→3.0, "1w"→5.0, "8h"→1.0).
- `llm_estimate`: same work with LLM pair-programming — typically 5–8× faster (e.g. if \
estimate is "3d", llm_estimate is "0.4d"). Use the same unit as estimate.
- `llm_estimate_days`: same as llm_estimate but decimal days (e.g. estimate_days=3.0 → \
llm_estimate_days≈0.5).

Stay strictly within the scope implied by the outline; do not absorb other tickets' work. Keep the \
ticket id, title, type, and dependencies exactly as given in the outline.\
"""

_USER_ELABORATE = """\
## Source requirements / spec (authoritative — preserve its detail)
{context}

## Epic
{epic}

## Ticket to expand (keep id/title/type/dependencies unchanged)
- id: {tid}
- title: {title}
- type: {ttype}
- needs_research: {needs_research}
- dependencies: {deps}
- current (thin) description: {desc}
{feedback}
Return the FULLY DETAILED ticket.\
"""

# ── PM workbench (decision #29): feedback the reviewer wants applied on regenerate ──

_USER_FEEDBACK = """\

## Reviewer feedback on your PREVIOUS spec — you MUST address this
The reviewer was not satisfied with the previous spec and asked for these changes. Regenerate the \
WHOLE spec, keeping what was good and applying this feedback directly:

{feedback}

For reference, your previous spec was:
{prev_spec}
--- end previous spec ---\
"""

_TICKET_FEEDBACK_BLOCK = """\

## Reviewer feedback on THIS ticket — you MUST address it
{feedback}\
"""

# ── bulk elaboration ──

_SYSTEM_BULK_ELABORATE = """\
You are a senior tech lead expanding a set of implementation tickets in full detail for a \
developer who will pick them up cold. You are given the full source requirements/spec, the epic, \
and a list of thin tickets to expand.

Expand EVERY ticket in the list into a thorough, self-contained implementation story. CRITICAL rules:
1. PRESERVE specifics from the source spec — copy real model field names, endpoint paths, \
task/queue names, file paths, and acceptance criteria into the tickets. Do NOT summarise it away.
2. `description`: a complete narrative of WHAT to build and WHY (several sentences).
3. `implementation_notes`: the HOW — concrete, ordered steps referencing real modules/classes/\
functions; key design decisions and constraints. This must be substantial, not one line.
4. `affected_files`: the actual files/paths this ticket touches.
5. `api_changes` / `data_model_changes`: concrete endpoint and model/field changes from the spec \
that belong to THIS ticket (empty lists if genuinely none).
6. `acceptance_criteria`: 2-5 concrete, testable conditions, drawn from the spec where present.
7. `out_of_scope`: what this ticket deliberately excludes (to bound it against sibling tickets).
8. `estimate` / `estimate_days` / `llm_estimate` / `llm_estimate_days`: provide traditional and \
LLM-assisted estimates (5-8x speedup). Use decimal days for the _days fields.

Stay strictly within the scope implied by the outlines. Keep the ticket id, title, type, and \
dependencies exactly as given. Return the list of FULLY DETAILED tickets.\
"""

_USER_BULK_ELABORATE = """\
## Source requirements / spec (authoritative — preserve its detail)
{context}

## Epic
{epic}

## Tickets to expand (keep ids/titles/types/dependencies unchanged)
{tickets}

Return the FULLY DETAILED tickets.\
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


class Tickets(BaseModel):
    tickets: list[Ticket]


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

        user_prompt = user.format(context=context)
        # PM workbench regenerate: fold the reviewer's spec-level feedback (plus the
        # previous spec for grounding) into the prompt so the new spec actually addresses it.
        if state.pm.feedback:
            prev = state.pm.spec.model_dump_json(indent=2) if state.pm.spec else "(none)"
            user_prompt += _USER_FEEDBACK.format(
                feedback=state.pm.feedback, prev_spec=prev[: self.settings.pm_detail_context_chars]
            )

        spec = await self.generate(Spec, system=system, user=user_prompt, context=context)
        spec = await self._validate_and_repair(spec, system=system, context=context)

        if getattr(self.settings, "pm_detail_tickets", True):
            spec = await self._elaborate_tickets(spec, context=context)

        # Deterministic estimate repair — ensure all tickets get sane traditional + LLM-assisted numbers.
        spec = repair_spec_estimates(spec, speedup=self.settings.pm_estimate_speedup)

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
                # Carry-forward (PMState has no reducer → wholesale replace): clear the consumed
                # feedback, keep the iteration counter and any prior per-ticket feedback.
                "feedback": None,
                "regeneration_count": state.pm.regeneration_count,
                "ticket_feedback": dict(state.pm.ticket_feedback),
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
        repaired = await self.generate(Spec, system=system, user=correction, context=context)

        remaining = validate_spec(repaired)
        if remaining:
            repaired.open_questions = [
                *repaired.open_questions,
                "Automated validation still flagged issues after one correction round "
                f"(needs human review): {'; '.join(remaining)}",
            ]
        return repaired

    async def _elaborate_tickets(self, spec: Spec, *, context: str) -> Spec:
        """Second pass (decision #27/#32): expand each ticket so it comes out richly detailed
        instead of compressed into the all-in-one spec response.

        Uses bulk elaboration (single LLM call) by default to minimize total calls; falls back
        to sequential elaboration if `pm_bulk_elaborate` is disabled or on bulk failure.
        """
        if not spec.tickets:
            return spec
        cap = getattr(self.settings, "pm_detail_context_chars", 24_000)
        ctx = context[:cap] if cap and len(context) > cap else context
        epic = f"{spec.epic.title}\n{spec.epic.summary}\n{spec.epic.business_goal}"

        if getattr(self.settings, "pm_bulk_elaborate", True):
            try:
                tickets_str = "\n".join(
                    f"- {t.id}: {t.title} ({t.type.value if hasattr(t.type, 'value') else t.type})"
                    for t in spec.tickets
                )
                user = _USER_BULK_ELABORATE.format(context=ctx, epic=epic, tickets=tickets_str)
                res = await self.generate(
                    Tickets, system=_SYSTEM_BULK_ELABORATE, user=user, context=ctx
                )

                # Map back to preserve validated IDs and fill missing details.
                detailed: list[Ticket] = []
                res_map = {t.id: t for t in res.tickets}
                for t in spec.tickets:
                    rich = res_map.get(t.id)
                    if not rich:
                        logger.warning("[pm] bulk elaborate missed ticket %s; keeping thin", t.id)
                        detailed.append(t)
                        continue

                    # Force structural fields back to the validated skeleton.
                    rich.id = t.id
                    rich.type = t.type
                    rich.dependencies = t.dependencies
                    rich.needs_research = t.needs_research
                    rich.title = t.title or rich.title
                    detailed.append(repair_ticket_estimates(rich, speedup=self.settings.pm_estimate_speedup))

                spec.tickets = detailed
                return spec
            except Exception as exc:  # noqa: BLE001 — bulk is best-effort; fallback to sequential
                logger.warning("[pm] bulk elaboration failed (%s); falling back to sequential", exc)

        detailed: list[Ticket] = []
        for t in spec.tickets:
            try:
                detailed.append(await self._elaborate_one(t, epic=epic, ctx=ctx))
            except Exception as exc:  # noqa: BLE001 — detailing is best-effort; keep the original
                logger.warning("[pm] ticket %s detail pass failed (%s); keeping it", t.id, exc)
                detailed.append(t)

        spec.tickets = detailed
        return spec

    async def _elaborate_one(
        self, ticket: Ticket, *, epic: str, ctx: str, feedback: str = ""
    ) -> Ticket:
        """Expand ONE ticket in a focused call (decision #27/#29). Forces structural fields back to
        the skeleton so the validated dependency graph can't drift, then repairs its estimates.
        `feedback` (optional) carries reviewer notes for the per-story refine path."""
        fb = _TICKET_FEEDBACK_BLOCK.format(feedback=feedback) if feedback.strip() else ""
        user = _USER_ELABORATE.format(
            context=ctx,
            epic=epic,
            tid=ticket.id,
            title=ticket.title,
            ttype=getattr(ticket.type, "value", ticket.type),
            needs_research=ticket.needs_research,
            deps=", ".join(ticket.dependencies) or "none",
            desc=ticket.description,
            feedback=fb,
        )
        rich = await self.generate(Ticket, system=_SYSTEM_ELABORATE, user=user, context=ctx)
        # Force structural fields back to the validated skeleton; keep the richer content.
        rich.id = ticket.id
        rich.type = ticket.type
        rich.dependencies = ticket.dependencies
        rich.needs_research = ticket.needs_research
        rich.title = ticket.title or rich.title
        rich.estimate = rich.estimate or ticket.estimate
        if not rich.description.strip():
            rich.description = ticket.description
        if not rich.acceptance_criteria:
            rich.acceptance_criteria = ticket.acceptance_criteria
        return repair_ticket_estimates(rich, speedup=self.settings.pm_estimate_speedup)

    async def refine_ticket(self, spec: Spec, ticket_id: str, feedback: str) -> Ticket | None:
        """PM workbench per-story refine (decision #29): re-elaborate ONE ticket of `spec` with the
        reviewer's feedback. Returns the refined ticket, or None if the id isn't in the spec."""
        self._reset_usage()
        target = next((t for t in spec.tickets if t.id == ticket_id), None)
        if target is None:
            return None
        epic = f"{spec.epic.title}\n{spec.epic.summary}\n{spec.epic.business_goal}"
        cap = getattr(self.settings, "pm_detail_context_chars", 24_000)
        ctx = (spec.technical_spec.approach or "")[:cap]
        return await self._elaborate_one(target, epic=epic, ctx=ctx, feedback=feedback)


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
        next_action = ""
        if isinstance(raw_decision, dict):
            action = str(raw_decision.get("action", "approve"))
            selection = raw_decision.get("stories")
            story_selection = [str(s) for s in selection] if isinstance(selection, list) else None
            # PM workbench (decision #29): the manual follow-up the reviewer chose at the gate.
            nxt = str(raw_decision.get("next", ""))
            next_action = nxt if nxt in ("rfc", "build") else ""
        else:
            action = str(raw_decision)
            story_selection = None

        # Fields every return must carry forward (PMState has no reducer → wholesale replace).
        carry = {
            "regeneration_count": state.pm.regeneration_count,
            "ticket_feedback": dict(state.pm.ticket_feedback),
            "next_action": next_action,
        }

        if action == "reject":
            return {
                "pm": {
                    "spec": spec,
                    "board_ref": state.pm.board_ref,
                    "ticket_refs": [],
                    "note": "ticket push cancelled by reviewer",
                    **carry,
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
                **carry,
            }
        }
