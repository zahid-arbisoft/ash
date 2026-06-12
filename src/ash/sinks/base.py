"""Task-sink abstraction — where the PM agent pushes generated tickets.

A `TicketSink` turns a `Spec`'s tickets into items in the user's task tool (file board / Jira /
Plane / …) and returns references. Selection is per-run with an admin-managed default (see
`sinks.service.resolve_task_sink`).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ash.schemas import Spec


class TicketRef(BaseModel):
    id: str
    url: str = ""
    sink: str = ""  # sink kind, e.g. "file" | "jira" | "plane"


class TicketSink(Protocol):
    kind: str

    async def publish(self, spec: Spec) -> list[TicketRef]:
        """Create one item per ticket in the spec; return their references."""
        ...
