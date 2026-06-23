"""BaseAgent — the agent contract (boilerplate spec §4), running on LangChain `create_agent`.

An agent reads the root `WorkflowState`, does its work, and returns a partial update scoped to its
own namespace. Structured generation goes through `create_agent` (LangChain's maintained agent
runtime: the ReAct tool loop + `response_format` structured output + the middleware hook), so all
agents share one runtime instead of hand-rolled tool loops. The model and tools are injectable for
deterministic, offline tests.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar, cast

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.types import interrupt
from pydantic import BaseModel

from ash.config.settings import AgentPolicy, Settings, load_project
from ash.graph.state import WorkflowState
from ash.llm.factory import get_chat_model

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# System prompt used exclusively in the extraction phase (_extract).
# We intentionally do NOT reuse the agent's operational system prompt here: those prompts
# describe tool usage (read_file, list_files, etc.) which causes Groq in JSON-schema mode to
# output a tool-call structure instead of the schema → json_validate_failed.
_EXTRACT_SYSTEM = (
    "Extract structured data from the provided notes and work brief. "
    "Output ONLY the required JSON object. "
    "Do not call tools, do not output tool names or function-call syntax."
)


class BaseAgent(ABC):
    name: str = "base"
    MAX_EXPLORE_STEPS = 8  # fallback ceiling when settings are unavailable

    def __init__(self, settings: Settings, *, model: BaseChatModel | None = None) -> None:
        self.settings = settings
        self._model = model
        # Accumulated token counts for the current run() call.  Agents reset this at the top of
        # run() (agents are singletons; _usage must be zeroed before each invocation).
        self._usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        # Captured agent↔LLM exchanges for the current run (decision #30). The node wrapper
        # resets this before run() and persists it after; refine paths persist directly.
        self._exchanges: list[dict[str, Any]] = []

    def _reset_usage(self) -> None:
        """Zero token counters — call at the top of every agent's run()."""
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def reset_exchanges(self) -> None:
        """Clear the captured LLM-I/O buffer — called by the node wrapper before each run()."""
        self._exchanges = []

    def _capture_exchange(
        self,
        *,
        phase: str,
        request: Any,
        response: dict[str, Any],
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        step: int = 0,
        context: str | None = None,
        code: str | None = None,
    ) -> None:
        """Append one LLM exchange (messages in + response out) to the capture buffer.

        Guarded by `persist_llm_exchanges` (default True). Best-effort — never raise into the
        agent's hot path."""
        if not getattr(self.settings, "persist_llm_exchanges", True):
            return
        try:
            self._exchanges.append(
                {
                    "phase": phase,
                    "step": step,
                    "model": self.settings.model_for(self.name).model,
                    "request": _messages_to_records(request),
                    "response": response,
                    "context": context,
                    "code": code,
                    "prompt_tokens": int(prompt_tokens),
                    "completion_tokens": int(completion_tokens),
                }
            )
        except Exception:  # noqa: BLE001 — capture must never break a run
            pass

    def _add_usage(self, metadata: Any) -> None:
        """Merge token counts from a LangChain usage_metadata or response_metadata dict."""
        if not metadata:
            return
        # LangChain normalised format (usage_metadata on AIMessage)
        self._usage["prompt_tokens"] += metadata.get("input_tokens", 0)
        self._usage["completion_tokens"] += metadata.get("output_tokens", 0)
        # OpenAI-compat format (response_metadata["token_usage"])
        tu = metadata.get("token_usage", {})
        self._usage["prompt_tokens"] += tu.get("prompt_tokens", 0)
        self._usage["completion_tokens"] += tu.get("completion_tokens", 0)

    def _extra_instructions(self, state: WorkflowState) -> str:
        """Optional custom prompt to fold into this agent's prompt (decision #33).

        Combines the run-wide standing instruction (`state.run_prompt`, set on the new-run form)
        with this agent's one-shot custom prompt (`state.custom_prompts[self.name]`, set by a
        cockpit re-trigger). Returns "" when neither is set. The per-agent entry is cleared by the
        node wrapper after the run (consume-once); `run_prompt` persists for the whole run.
        """
        parts = [state.run_prompt, state.custom_prompts.get(self.name, "")]
        return "\n\n".join(p.strip() for p in parts if p and p.strip())

    @abstractmethod
    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Return a partial state update scoped to this agent's namespace."""

    def get_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model
        llm = self.settings.model_for(self.name)
        logger.info(
            "[agent:%s] model=%s provider=%s base_url=%s",
            self.name, llm.model, llm.provider, llm.base_url or "(provider default)",
        )
        return get_chat_model(llm, api_key=self.settings.effective_api_key(self.name))

    def get_tools(self) -> list[BaseTool]:
        """Tools this agent may call inside its `create_agent` loop (default: none)."""
        return []

    def build_agent(
        self,
        *,
        system_prompt: str,
        response_format: type[BaseModel] | None = None,
        tools: list[BaseTool] | None = None,
    ) -> Any:
        """Construct this agent's `create_agent` runtime (its own compiled LangGraph)."""
        return create_agent(
            model=self.get_model(),
            tools=tools if tools is not None else self.get_tools(),
            system_prompt=system_prompt,
            response_format=response_format,
        )

    async def _resolve_policy(self, state: WorkflowState) -> tuple[Any, AgentPolicy] | None:
        """Return ``(project, policy)`` with the effective policy, or ``None`` when the project
        config can't be loaded.

        Precedence (highest first), so the Agents page always wins and a per-run workflow only
        defaults what the Agents page hasn't pinned (workflow-builder):

            AgentPolicyRecord (Agents page / DB override)
              > run's workflow snapshot step (trigger + enabled)
                > projects/<name>.yaml
                  > code default (manual, enabled)

        Centralised so every enabled/trigger check reads ONE resolved policy.
        """
        try:
            project = load_project(state.project)
        except Exception:  # noqa: BLE001 — missing project config → no policy
            return None
        yaml_policy = project.agent_policy(self.name)
        try:
            from ash.db.base import get_sessionmaker
            from ash.db.tasks import get_policy_override
            from ash.db.workflows import step_policy

            async with get_sessionmaker()() as session:
                db_override = await get_policy_override(session, project.name, self.name)
            if db_override is not None:
                # Agents page set this agent explicitly → it wins over the workflow entirely.
                policy = AgentPolicy(
                    trigger=db_override.trigger,
                    enabled=db_override.enabled,
                    concurrency_limit=db_override.concurrency_limit,
                    daily_quota=db_override.daily_quota,
                    max_retries=db_override.max_retries,
                    schedule_cron=db_override.schedule_cron,
                )
            else:
                # No DB override → the run's workflow defaults trigger/enabled over YAML.
                wf_step = step_policy(state.workflow_snapshot, self.name)
                if wf_step is not None:
                    policy = yaml_policy.model_copy(
                        update={"trigger": wf_step["trigger"], "enabled": wf_step["enabled"]}
                    )
                else:
                    policy = yaml_policy
        except Exception:  # noqa: BLE001 — DB unavailable → fall back to YAML
            policy = yaml_policy
        return project, policy

    async def _trigger_gate(
        self,
        state: WorkflowState,
        *,
        resolved: tuple[Any, AgentPolicy] | None = None,
    ) -> dict[str, Any] | None:
        """Return a skip dict if this agent is disabled or has trigger=manual.

        Reads the DB-resolved policy (DB override > YAML > default) so UI changes
        to enabled/trigger take effect immediately without restarting the server.
        Calls LangGraph `interrupt()` for manual-trigger agents so the graph pauses
        until a human clicks Trigger.  On any other decision the agent is skipped.
        Pass `resolved` to reuse a policy already fetched by the caller.
        """
        if resolved is None:
            resolved = await self._resolve_policy(state)
        if resolved is None:
            return None
        _project, policy = resolved

        if not policy.enabled:
            return {self.name: {"note": f"skipped: agent {self.name!r} is disabled"}}
        if policy.trigger != "manual":
            return None
        decision: Any = interrupt({"reason": "manual_trigger", "agent": self.name})
        if decision == "run":
            return None
        return {self.name: {"note": f"skipped: manual trigger not activated (decision={decision})"}}

    async def generate(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        tools: list[BaseTool] | None = None,
        context: str | None = None,
        code: str | None = None,
    ) -> T:
        """Produce a validated `schema` instance.

        Tool-using agents run in TWO phases, because some providers (notably Groq's
        `gpt-oss-*` via LiteLLM) put the model in JSON-schema mode when `response_format`
        and tools are sent together — so the model's tool call gets validated against the
        *final* schema and the request 400s (`json_validate_failed`). To avoid that:
          1. **explore** — a `create_agent` tool loop with NO `response_format`; the model
             calls tools freely (reads files, greps) and ends with a free-text conclusion.
          2. **extract** — a separate tool-free `with_structured_output` call that turns the
             gathered notes into the typed object.
        Agents with no tools (PM, RFC) make ONE direct `with_structured_output` call with their
        real system prompt — NOT `create_agent`. create_agent's response_format runs a ReAct
        graph that loops on a synthetic schema-tool; weak local models never satisfy it and the
        run hangs. A direct structured call is a single request that always terminates.
        """
        resolved_tools = tools if tools is not None else self.get_tools()
        # Log the EFFECTIVE tuning settings once per generate(). If these don't match your
        # .env (e.g. brief_max=100000 when you set BRIEF_MAX_CHARS=8000), the env never loaded
        # — restart the app (get_settings() is lru_cached) / `docker compose restart api`.
        logger.info(
            "[agent:%s] tuning: brief_max=%d explore_steps=%d tool_chars=%d window=%d "
            "notes_chars=%d max_tokens=%d",
            self.name,
            getattr(self.settings, "brief_max_chars", -1),
            getattr(self.settings, "explore_steps", -1),
            getattr(self.settings, "explore_tool_chars", -1),
            getattr(self.settings, "explore_window", -1),
            getattr(self.settings, "explore_notes_chars", -1),
            self.settings.model_for(self.name).max_tokens,
        )
        try:
            if resolved_tools:
                notes = await self._explore(system, user, resolved_tools, context=context, code=code)
                result = await self._extract(
                    schema, system=system, user=user, notes=notes, context=context, code=code
                )
            else:
                from langchain_core.messages import HumanMessage, SystemMessage

                sys_chars, sys_est = _prompt_size(system)
                usr_chars, usr_est = _prompt_size(user)
                logger.info(
                    "[agent:%s] single-call request: system %d chars + user %d chars = "
                    "~%d tokens (+~2K schema injection, excludes that)",
                    self.name, sys_chars, usr_chars, sys_est + usr_est,
                )
                # Direct single structured call — NOT create_agent. create_agent's
                # response_format forces the schema as a synthetic tool and loops on the ReAct
                # graph; weak local models (e.g. Qwen-7B) never satisfy it, so the graph spins to
                # its recursion limit and the run appears hung. A direct with_structured_output
                # call is one request, no loop. The agent's REAL system prompt is safe here
                # (no-tools agents carry no tool-usage language to confuse the model — unlike
                # _extract, which must stay neutral; see _EXTRACT_SYSTEM).
                messages = [SystemMessage(content=system), HumanMessage(content=user)]
                t0 = time.monotonic()
                result = await self._invoke_structured(
                    schema, messages, phase="single_call", context=context, code=code
                )
                logger.info(
                    "[agent:%s] single-call completed in %.1fs", self.name, time.monotonic() - t0
                )
            logger.info(
                "[agent:%s] tokens prompt=%d completion=%d",
                self.name,
                self._usage["prompt_tokens"],
                self._usage["completion_tokens"],
            )
            return result
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            exc_type = type(exc).__name__
            # LiteLLM / provider guardrails surface as PermissionDeniedError (HTTP 403).
            if "403" in msg or "blocked" in msg.lower() or "guardrail" in msg.lower():
                raise GuardrailBlockedError(
                    f"LLM gateway blocked the request ({type(exc).__name__}): {msg}"
                ) from exc
            # LengthFinishReasonError — model hit max_tokens mid-JSON.  Fall back to a
            # tool-free _extract call which uses a shorter, neutral system prompt and may
            # leave just enough room for the output schema.  If the fallback also truncates,
            # a clear RuntimeError surfaces in the run UI with actionable guidance.
            ml = msg.lower()
            is_length_error = (
                exc_type == "LengthFinishReasonError"
                or "length limit was reached" in ml
                or "lengthfinishreason" in ml
            )
            if is_length_error:
                max_tok = self.settings.model_for(self.name).max_tokens
                logger.warning(
                    "Agent %s: model hit output token limit (%d max_tokens) — "
                    "retrying with _extract (shorter prompt). "
                    "Set LLM_MAX_TOKENS=8192 (or AGENT_%s__MAX_TOKENS=8192) to avoid this.",
                    self.name, max_tok, self.name.upper(),
                )
                try:
                    return await self._extract(schema, system=system, user=user, notes=None)
                except Exception as inner:  # noqa: BLE001 — surface a clean error
                    inner_type = type(inner).__name__
                    inner_msg = str(inner)
                    if (
                        inner_type == "LengthFinishReasonError"
                        or "length limit was reached" in inner_msg.lower()
                    ):
                        raise RuntimeError(
                            f"Model output truncated at {max_tok} tokens even after fallback. "
                            f"Increase LLM_MAX_TOKENS (current: {max_tok}) to at least 8192, "
                            f"or set AGENT_{self.name.upper()}__MAX_TOKENS=8192 for this agent."
                        ) from exc
                    raise
            # Any structured-generation / tool-validation failure → last-ditch tool-free
            # structured extraction from the brief alone (degrade rather than crash).
            if (
                "tool call validation" in ml
                or "not in request.tools" in ml
                or "json_validate_failed" in ml
                or "output_parse_failed" in ml
                or "parsing failed" in ml
                or "could not be parsed" in ml
                or "does not match" in ml
                or ("400" in msg and "tool" in ml)
                or ("tool choice is none" in ml)
            ):
                logger.warning(
                    "Agent %s: structured generation failed — retrying tool-free from the "
                    "brief alone. %s",
                    self.name, msg[:200],
                )
                return await self._extract(schema, system=system, user=user, notes=None)
            raise

    async def _explore(
        self,
        system: str,
        user: str,
        tools: list[BaseTool],
        *,
        context: str | None = None,
        code: str | None = None,
    ) -> str:
        """Phase 1: a hand-rolled tool loop that returns the model's final free text.

        We deliberately do NOT use `create_agent` here. Its loop sets `tool_choice="none"`
        on the final synthesis turn (to force a text answer), but Groq's `gpt-oss-*` ignores
        that and calls a tool anyway → `tool_use_failed: Tool choice is none, but model called
        a tool`. This loop only ever uses the default tool choice (`auto`): bind tools, let the
        model call them, feed results back, and stop when it returns text with no tool calls.

        Sliding window: when `settings.explore_window > 0`, the rolling message history is
        trimmed after each round to keep at most that many tool exchanges beyond the anchor
        [system, user].  This bounds context growth for small-context models (e.g. 4096-token
        7B models) that would otherwise overflow after 2-3 tool calls.
        """
        from langchain_core.messages import (
            AIMessage,
            BaseMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        max_steps: int = getattr(self.settings, "explore_steps", self.MAX_EXPLORE_STEPS)
        tool_chars: int = getattr(self.settings, "explore_tool_chars", 3000)
        window: int = getattr(self.settings, "explore_window", 0)  # 0 = unlimited

        bound = self.get_model().bind_tools(tools, tool_choice="auto")  # explicitly auto for Groq
        by_name = {t.name: t for t in tools}
        # Anchor: always kept in full.  Rolling tail is trimmed when window > 0.
        anchor: list[BaseMessage] = [SystemMessage(content=system), HumanMessage(content=user)]
        rolling: list[BaseMessage] = []
        last_text = ""

        for step in range(max_steps):
            msgs = anchor + rolling
            chars, est = _prompt_size(msgs)
            logger.info(
                "[agent:%s] _explore step %d/%d request: %d msgs, %d chars, ~%d tokens",
                self.name, step + 1, max_steps, len(msgs), chars, est,
            )
            t0 = time.monotonic()
            ai = await bound.ainvoke(msgs)
            logger.info(
                "[agent:%s] _explore step %d completed in %.1fs",
                self.name, step + 1, time.monotonic() - t0,
            )
            if not isinstance(ai, AIMessage):  # pragma: no cover — defensive
                break
            rolling.append(ai)
            # Accumulate token usage (LangChain normalises to usage_metadata on AIMessage).
            um: dict[str, int] = ai.usage_metadata or {}  # type: ignore[assignment]
            self._add_usage(um)
            text = ai.content if isinstance(ai.content, str) else _text_of(ai.content)
            if text.strip():
                last_text = text
            tool_calls = ai.tool_calls or []
            self._capture_exchange(
                phase="explore",
                step=step + 1,
                request=msgs,
                response={
                    "content": text,
                    "tool_calls": [
                        {"name": tc.get("name"), "args": tc.get("args")} for tc in tool_calls
                    ],
                },
                prompt_tokens=um.get("input_tokens", 0),
                completion_tokens=um.get("output_tokens", 0),
                context=context if step == 0 else None,
                code=code if step == 0 else None,
            )
            if not tool_calls:
                break
            for tc in tool_calls:
                tool = by_name.get(tc["name"])
                try:
                    out = (
                        await tool.ainvoke(tc["args"])
                        if tool is not None
                        else f"error: unknown tool {tc['name']!r}"
                    )
                except Exception as exc:  # noqa: BLE001 — tool errors are fed back, not fatal
                    out = f"error executing {tc['name']}: {exc}"
                rolling.append(
                    ToolMessage(content=str(out)[:tool_chars], tool_call_id=tc.get("id") or "")
                )
            # Sliding-window trim: keep last `window` exchanges (each exchange = 1 AI + N
            # ToolMessages).  We approximate "N ToolMessages" as everything non-AI since the
            # last AI message.  Simple heuristic: keep last `window * 2` messages in rolling
            # (1 AI + 1 avg ToolMessage per exchange).  This is conservative — real exchanges
            # may have 2-3 ToolMessages, so the actual window may be slightly smaller.
            if window > 0 and len(rolling) > window * 2:
                rolling = rolling[-(window * 2):]

        return last_text

    async def _extract(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        notes: str | None,
        context: str | None = None,
        code: str | None = None,
    ) -> T:
        """Phase 2 (and the fallback): tool-free structured output via `with_structured_output`.

        Folds any phase-1 exploration notes into the prompt so the structured result stays
        grounded in what the agent actually found in the repo.

        `system` is intentionally ignored here — see _EXTRACT_SYSTEM for why.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        # Clean tool-usage instructions from the user prompt to prevent the model from
        # attempting tool calls in this tool-free phase.
        _TOOL_HINTS = (
            "Use read_file and list_files to inspect the current code before making changes.",
            "Use the tools to explore the codebase, then produce the implementation plan.",
            "explore the codebase and decide the minimal change yourself.",
        )
        clean_user = user
        for hint in _TOOL_HINTS:
            clean_user = clean_user.replace(hint, "")
        clean_user = clean_user.strip()

        if notes:
            human = f"{clean_user}\n\n## Codebase exploration notes\n{notes}"
        else:
            human = clean_user
        messages = [SystemMessage(content=_EXTRACT_SYSTEM), HumanMessage(content=human)]
        chars, est = _prompt_size(messages)
        logger.info(
            "[agent:%s] _extract request: %d chars, ~%d tokens (schema=%s, "
            "+~2K schema injection)",
            self.name, chars, est, schema.__name__,
        )
        t0 = time.monotonic()
        result = await self._invoke_structured(
            schema, messages, phase="extract", context=context, code=code
        )
        logger.info(
            "[agent:%s] _extract completed in %.1fs", self.name, time.monotonic() - t0
        )
        return result

    async def _invoke_structured(
        self,
        schema: type[T],
        messages: list[Any],
        *,
        phase: str = "single_call",
        context: str | None = None,
        code: str | None = None,
    ) -> T:
        """One tool-free structured call via `with_structured_output`, with usage tracking.

        Shared by the no-tools path in generate() and _extract. include_raw=True returns
        {"raw": AIMessage, "parsed": T} so we can read usage_metadata; some providers / test
        fakes ignore include_raw and return T directly — handle both.
        """
        chain = self.get_model().with_structured_output(schema, include_raw=True)
        raw_result: Any = await chain.ainvoke(messages)
        if isinstance(raw_result, dict) and "raw" in raw_result:
            raw_ai = raw_result.get("raw")
            um: dict[str, int] = getattr(raw_ai, "usage_metadata", None) or {}
            tu: dict[str, int] = getattr(raw_ai, "response_metadata", {}).get("token_usage") or {}
            self._add_usage(um)
            self._add_usage(tu)
            parsed = raw_result["parsed"]
            self._capture_exchange(
                phase=phase,
                request=messages,
                response={
                    "content": _text_of(getattr(raw_ai, "content", "") or ""),
                    "parsed": _jsonable(parsed),
                },
                prompt_tokens=um.get("input_tokens", 0) + tu.get("prompt_tokens", 0),
                completion_tokens=um.get("output_tokens", 0) + tu.get("completion_tokens", 0),
                context=context,
                code=code,
            )
            return cast(T, parsed)
        self._capture_exchange(
            phase=phase,
            request=messages,
            response={"parsed": _jsonable(raw_result)},
            context=context,
            code=code,
        )
        return cast(T, raw_result)


def _text_of(content: Any) -> str:
    """Flatten a list-of-blocks message content into plain text."""
    if isinstance(content, str):
        return content
    return "\n".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )


def _prompt_size(messages: Any) -> tuple[int, int]:
    """Return (total_chars, est_tokens) for messages or a raw string.

    est_tokens ≈ chars / 4 — a rough rule of thumb. It does NOT include the JSON
    schema / tool definitions the provider injects for structured output (add ~1.5-2K
    tokens for those), so the real request is somewhat larger than this estimate.
    """
    if isinstance(messages, str):
        chars = len(messages)
        return chars, chars // 4
    chars = 0
    for m in messages:
        content = getattr(m, "content", m)
        chars += len(_text_of(content)) if not isinstance(content, str) else len(content)
    return chars, chars // 4


# ── LLM I/O capture (decision #30) ───────────────────────────────────────────
_ROLE_BY_TYPE = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}


def _messages_to_records(messages: Any) -> list[dict[str, str]]:
    """Convert a LangChain message list into compact [{role, content}] dicts for storage."""
    out: list[dict[str, str]] = []
    for m in messages:
        role = _ROLE_BY_TYPE.get(getattr(m, "type", ""), getattr(m, "type", "") or "user")
        out.append({"role": role, "content": _text_of(getattr(m, "content", m))})
    return out


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe rendering of a structured result for storage."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


class GuardrailBlockedError(RuntimeError):
    """Raised when the LLM provider / gateway rejects a prompt via a content guardrail."""
