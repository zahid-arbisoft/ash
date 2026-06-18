"""Tests for BaseAgent.generate — the two-phase (explore → extract) tool path.

Tool-using agents must NOT send tools + response_format in one request (Groq 400s with
json_validate_failed). Instead they explore free-form, then extract structure tool-free.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from ash.agents.base import _EXTRACT_SYSTEM, BaseAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState


class _Result(BaseModel):
    answer: str


@tool
def _peek(path: str) -> str:
    """Read a fake file."""
    return f"contents of {path}"


class _TwoPhaseFake(GenericFakeChatModel):
    """Phase 1 returns free-text; phase 2 returns the structured tool call.

    The same model instance backs both phases, so messages are consumed in order:
    the explore loop takes the text message, the extract call takes the tool-call message.
    """

    def __init__(self, explore_text: str, result: BaseModel) -> None:
        msgs = [
            AIMessage(content=explore_text),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": type(result).__name__,
                        "args": result.model_dump(),
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
        super().__init__(messages=iter(msgs))

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


class _Agent(BaseAgent):
    name = "research"

    async def run(self, state: WorkflowState) -> dict[str, Any]:  # pragma: no cover
        return {}


async def test_generate_with_tools_runs_two_phases():
    """With tools, generate explores (free-form) then extracts the structured result."""
    fake = _TwoPhaseFake("I explored src/api.py and found the route table.", _Result(answer="42"))
    agent = _Agent(Settings(), model=fake)

    out = await agent.generate(_Result, system="sys", user="do it", tools=[_peek])

    assert isinstance(out, _Result)
    assert out.answer == "42"


class _ToolThenAnswerFake(GenericFakeChatModel):
    """Phase 1 calls a tool, then concludes with text; phase 2 returns the structured result.

    Drives the actual tool-execution branch of the explore loop — the path that 400'd on Groq
    when create_agent sent tool_choice="none".
    """

    def __init__(self, tool_name: str, result: BaseModel) -> None:
        msgs = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": tool_name, "args": {"path": "src"}, "id": "t1", "type": "tool_call"}
                ],
            ),
            AIMessage(content="Explored src; the route table lives in api.py."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": type(result).__name__,
                        "args": result.model_dump(),
                        "id": "call_2",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
        super().__init__(messages=iter(msgs))

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        # Mirror Groq's quirk: never accept a forced/none tool_choice silently — the loop
        # must rely on the default. If a caller passed tool_choice="none", that's a bug.
        assert kwargs.get("tool_choice") != "none"
        return self


async def test_explore_executes_tool_calls_then_extracts():
    """The explore loop runs the tool, feeds the result back, then extracts structure."""
    fake = _ToolThenAnswerFake("_peek", _Result(answer="done"))
    agent = _Agent(Settings(), model=fake)

    out = await agent.generate(_Result, system="sys", user="explore", tools=[_peek])

    assert isinstance(out, _Result)
    assert out.answer == "done"


def test_extract_system_is_tool_free():
    """_EXTRACT_SYSTEM must not mention tool names — that's what triggers Groq json_validate_failed.

    Groq in JSON-schema mode outputs a tool-call structure (e.g. {"name": "list_files", ...})
    when the system prompt describes tool usage. _EXTRACT_SYSTEM is purposely neutral so the
    model outputs the schema instead.
    """
    assert "read_file" not in _EXTRACT_SYSTEM
    assert "list_files" not in _EXTRACT_SYSTEM
    assert "search_code" not in _EXTRACT_SYSTEM
    # Must explicitly forbid tool calls
    assert "Do not call tools" in _EXTRACT_SYSTEM or "do not call tools" in _EXTRACT_SYSTEM.lower()


async def test_generate_without_tools_uses_single_call():
    """No tools → single direct with_structured_output call (no create_agent loop).

    The no-tools path must NOT run create_agent's ReAct graph (which loops on a synthetic
    schema-tool and hangs weak local models). It makes one with_structured_output call with
    the agent's real system prompt.
    """

    class _Single(GenericFakeChatModel):
        def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
            include_raw = kwargs.get("include_raw", False)

            class _S:
                async def ainvoke(self_inner, messages: Any, *a: Any, **k: Any) -> Any:
                    parsed = schema(answer="ok")
                    if include_raw:
                        return {"raw": AIMessage(content=""), "parsed": parsed}
                    return parsed

            return _S()

    agent = _Agent(Settings(), model=_Single(messages=iter([])))
    out = await agent.generate(_Result, system="sys", user="do it")
    assert out.answer == "ok"


async def test_generate_falls_back_on_groq_tool_choice_error():
    """If Groq 400s with 'tool choice is none', generate falls back to tool-free extraction."""

    class _GroqErrorFake(GenericFakeChatModel):
        def __init__(self) -> None:
            super().__init__(messages=iter([]))

        async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
            # Phase 1: _explore succeeds (returns text)
            if any(m.content == "sys" for m in input if hasattr(m, "content")):
                return AIMessage(content="I found something.")
            # Phase 2: _extract fails with the Groq error (when notes are present)
            if any(
                "## Codebase exploration notes" in m.content
                for m in input
                if hasattr(m, "content")
            ):
                raise ValueError(
                    "BadRequestError: 400 - tool choice is none, but model called a tool"
                )
            # Fallback extraction (no notes) succeeds
            return AIMessage(content="fallback success", tool_calls=[
                {"name": "_Result", "args": {"answer": "recovered"}, "id": "c3",
                 "type": "tool_call"}
            ])

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
            class _Structured:
                def __init__(self, parent: Any) -> None:
                    self.parent = parent
                async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
                    ai = await self.parent.ainvoke(*args, **kwargs)
                    # Simulate with_structured_output: it parses tool calls into the schema
                    if ai.tool_calls:
                        return schema(**ai.tool_calls[0]["args"])
                    return ai
            return _Structured(self)

    agent = _Agent(Settings(), model=_GroqErrorFake())
    # We need tools so it enters the two-phase path
    out = await agent.generate(_Result, system="sys", user="do it", tools=[_peek])

    assert isinstance(out, _Result)
    assert out.answer == "recovered"


async def test_generate_captures_exchanges_two_phase():
    """The explore + extract calls are captured into _exchanges with the right phases."""
    fake = _ToolThenAnswerFake("_peek", _Result(answer="done"))
    agent = _Agent(Settings(), model=fake)
    agent.reset_exchanges()

    await agent.generate(_Result, system="sys", user="explore", tools=[_peek])

    phases = [e["phase"] for e in agent._exchanges]
    assert "explore" in phases  # the tool loop step(s)
    assert phases[-1] == "extract"  # the structured extraction
    extract = agent._exchanges[-1]
    assert extract["response"]["parsed"] == {"answer": "done"}
    assert any(m["role"] in ("system", "user") for m in extract["request"])


async def test_capture_disabled_by_setting():
    """persist_llm_exchanges=False → nothing is captured."""
    fake = _TwoPhaseFake("explored", _Result(answer="42"))
    agent = _Agent(Settings(persist_llm_exchanges=False), model=fake)
    agent.reset_exchanges()
    await agent.generate(_Result, system="sys", user="do it", tools=[_peek])
    assert agent._exchanges == []


async def test_extract_strips_tool_instructions():
    """_extract should remove tool-usage hints from the user prompt."""

    class _CaptureFake(GenericFakeChatModel):
        def __init__(self) -> None:
            super().__init__(messages=iter([]))
            object.__setattr__(self, "captured_messages", [])

        async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
            self.captured_messages.append(input)
            return AIMessage(content="ok", tool_calls=[
                {"name": "_Result", "args": {"answer": "done"}, "id": "c4", "type": "tool_call"}
            ])

        def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
            class _Structured:
                def __init__(self, parent: Any) -> None:
                    self.parent = parent
                async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
                    ai = await self.parent.ainvoke(*args, **kwargs)
                    return schema(**ai.tool_calls[0]["args"])
            return _Structured(self)

    fake = _CaptureFake()
    agent = _Agent(Settings(), model=fake)
    user_prompt = (
        "Work brief. Use read_file and list_files to inspect the current code "
        "before making changes."
    )

    await agent._extract(_Result, system="sys", user=user_prompt, notes="found some things")

    human_msg = next(m for m in fake.captured_messages[0] if m.type == "human")
    assert "Use read_file and list_files" not in human_msg.content
    assert "Work brief." in human_msg.content
    assert "found some things" in human_msg.content
