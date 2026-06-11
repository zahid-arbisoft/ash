"""Async GitHub Issues client (boundary layer).

Reads issues from the source repo and (optionally) posts comments back. HTTP via `httpx`; a token
lifts the rate limit and is required for private repos / writes. Independently testable with
`httpx.MockTransport`.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

_API = "https://api.github.com"


class Issue(BaseModel):
    number: int
    title: str
    body: str
    labels: list[str] = []
    url: str = ""
    state: str = "open"


class GitHubClient:
    """Minimal GitHub Issues client: read an issue, list issues, post a comment."""

    def __init__(self, *, token: str, repo: str, http: httpx.AsyncClient) -> None:
        self._token = token
        self._repo = repo  # "owner/name"
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        h = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
        }
        if self._token:
            h["authorization"] = f"Bearer {self._token}"
        return h

    async def get_issue(self, item_id: str | int) -> Issue:
        resp = await self._http.get(
            f"{_API}/repos/{self._repo}/issues/{item_id}", headers=self._headers
        )
        resp.raise_for_status()
        data = resp.json()
        if "pull_request" in data:
            raise ValueError(f"{self._repo}#{item_id} is a pull request, not an issue")
        return _to_issue(data)

    async def list_issues(
        self, *, filters: dict[str, object] | None = None, limit: int = 20
    ) -> list[Issue]:
        filters = filters or {}
        params: dict[str, str | int] = {
            "state": str(filters.get("state", "open")),
            "per_page": min(limit, 100),
            "sort": "created",
            "direction": "desc",
        }
        labels = filters.get("labels")
        if isinstance(labels, (list, tuple)):
            params["labels"] = ",".join(str(x) for x in labels)
        resp = await self._http.get(
            f"{_API}/repos/{self._repo}/issues", headers=self._headers, params=params
        )
        resp.raise_for_status()
        return [_to_issue(d) for d in resp.json() if "pull_request" not in d][:limit]

    async def post_comment(self, item_id: str | int, body: str) -> str:
        resp = await self._http.post(
            f"{_API}/repos/{self._repo}/issues/{item_id}/comments",
            headers=self._headers,
            json={"body": body},
        )
        resp.raise_for_status()
        return str(resp.json()["html_url"])


def _to_issue(data: dict[str, Any]) -> Issue:
    labels_raw = data.get("labels") or []
    labels = [str(lbl["name"]) for lbl in labels_raw if isinstance(lbl, dict)]
    return Issue(
        number=int(data["number"]),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        labels=labels,
        url=str(data.get("html_url") or ""),
        state=str(data.get("state") or "open"),
    )
