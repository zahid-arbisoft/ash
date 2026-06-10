"""Provider-agnostic LLM client.

One method the agents care about: `generate_structured(...)` returns a validated Pydantic model.
Both providers force structured output via tool/function calling.

- provider="anthropic": native Anthropic Messages API (cloud, or base_url override).
- provider="openai":   any OpenAI-compatible endpoint (LiteLLM/Ollama/vLLM/local gateway) — set
                       LLM_BASE_URL to your local host to cut cost during build/test.

Usage (input/output tokens) is returned alongside the parsed result for the budget guard (Phase 4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from .config import LLMSettings

T = TypeVar("T", bound=BaseModel)

_TOOL_NAME = "emit_result"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StructuredResult:
    parsed: BaseModel
    usage: Usage
    model: str


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        if settings.provider == "anthropic":
            from anthropic import Anthropic

            self._client = Anthropic(api_key=settings.api_key or None, base_url=settings.base_url)
        elif settings.provider == "openai":
            from openai import OpenAI

            self._client = OpenAI(api_key=settings.api_key or "x", base_url=settings.base_url)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER '{settings.provider}' (use anthropic|openai)")

    def generate_structured(
        self,
        *,
        role: str,
        system: str,
        user: str,
        schema: type[T],
    ) -> StructuredResult:
        model = self.settings.model_for(role)
        json_schema = schema.model_json_schema()
        if self.settings.provider == "anthropic":
            return self._anthropic(model, system, user, schema, json_schema)
        return self._openai(model, system, user, schema, json_schema)

    # ── providers ──────────────────────────────────────────────────────────────

    def _anthropic(self, model, system, user, schema: type[T], json_schema) -> StructuredResult:
        resp = self._client.messages.create(
            model=model,
            max_tokens=self.settings.max_tokens,
            temperature=self.settings.temperature,
            system=system,
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Return the result in the required structured form.",
                    "input_schema": json_schema,
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user}],
        )
        tool_input = next(
            (b.input for b in resp.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_input is None:
            raise RuntimeError("Anthropic response contained no tool_use block")
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return StructuredResult(schema.model_validate(tool_input), usage, model)

    def _openai(self, model, system, user, schema: type[T], json_schema) -> StructuredResult:
        # Try native function calling first (reliable on capable models)...
        try:
            resp = self._client.chat.completions.create(
                model=model,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": _TOOL_NAME,
                            "description": "Return the result in the required structured form.",
                            "parameters": json_schema,
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            )
            choice = resp.choices[0].message
            if not choice.tool_calls:
                raise RuntimeError("no tool call in response")
            args = json.loads(choice.tool_calls[0].function.arguments)
            usage = self._usage(resp)
            return StructuredResult(schema.model_validate(args), usage, model)
        except Exception:
            # ...fall back to JSON mode (small/local models often fail strict tool-calling
            # validation even when they emit valid JSON — Groq/llama, etc.).
            return self._openai_json(model, system, user, schema, json_schema)

    def _openai_json(self, model, system, user, schema: type[T], json_schema) -> StructuredResult:
        sys = (
            f"{system}\n\nYou MUST respond with a single JSON object that strictly conforms "
            f"to this JSON Schema:\n{json.dumps(json_schema)}\n\n"
            "Output ONLY the JSON object — no prose, no markdown code fences."
        )
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=self.settings.max_tokens,
            temperature=self.settings.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        args = json.loads(_strip_fences(content))
        return StructuredResult(schema.model_validate(args), self._usage(resp), model)

    @staticmethod
    def _usage(resp) -> Usage:
        u = getattr(resp, "usage", None)
        return Usage(getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)


def _strip_fences(text: str) -> str:
    """Remove optional ```json ... ``` fences some models add despite instructions."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()
