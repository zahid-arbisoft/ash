"""Issue-source provider abstraction.

A `Trigger`/intake connector (plan §8): the PM/intake layer pulls a normalized `RawIssue` from
whatever source the selected integration points at (GitHub / Jira / Plane). Adding a new source =
implement `IssueProvider` + register its `ProviderKind`; no changes to the agents or graph.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RawIssue(BaseModel):
    """A provider-agnostic issue/ticket as pulled from a source."""

    id: str
    title: str
    body: str = ""
    url: str = ""
    labels: list[str] = []
    state: str = "open"
    source: str = ""  # provider kind, e.g. "github" | "jira" | "plane"


@runtime_checkable
class IssueProvider(Protocol):
    kind: str

    async def fetch_issue(self, item_id: str) -> RawIssue: ...

    async def list_issues(
        self, *, filters: dict[str, object] | None = None, limit: int = 20
    ) -> list[RawIssue]: ...

    async def post_comment(self, item_id: str, body: str) -> str: ...

    async def create_issue(self, title: str, body: str) -> str: ...
