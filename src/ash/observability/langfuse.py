"""Env-gated Langfuse callback factory.

Returns a LangChain CallbackHandler when LANGFUSE_PUBLIC_KEY is set, else None.
The caller attaches the result to the LangGraph run config's `callbacks` list so
tracing propagates automatically to all LangChain calls within the run.
"""

from __future__ import annotations

import os
from typing import Any


def get_langfuse_callback() -> Any | None:
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse.callback import CallbackHandler  # type: ignore[import-untyped]

        return CallbackHandler(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    except ImportError:
        return None
