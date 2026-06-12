"""GitHub Issues provider (REST v3 over httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from ash.integrations.base import RawIssue

_API = "https://api.github.com"


class GitHubIssueProvider:
    kind = "github"

    def __init__(
        self,
        *,
        token: str,
        config: dict[str, Any],
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._repo = str(config.get("repo", ""))  # "owner/name"
        self._api = (base_url or _API).rstrip("/")
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
        if self._token:
            h["authorization"] = f"Bearer {self._token}"
        return h

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        if self._http is not None:
            resp = await self._http.request(
                method, f"{self._api}{path}", headers=self._headers, **kw
            )
        else:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.request(method, f"{self._api}{path}", headers=self._headers, **kw)
        resp.raise_for_status()
        return resp

    async def fetch_issue(self, item_id: str) -> RawIssue:
        if not self._repo:
            raise ValueError(
                "GitHub repo is not configured. "
                "Set 'repo' (e.g. 'owner/name') in the connector config at /admin, "
                "or set issues.source_repo in your project YAML."
            )
        resp = await self._request("GET", f"/repos/{self._repo}/issues/{item_id}")
        return self._to_issue(resp.json())

    async def list_issues(
        self, *, filters: dict[str, object] | None = None, limit: int = 20
    ) -> list[RawIssue]:
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
        resp = await self._request("GET", f"/repos/{self._repo}/issues", params=params)
        return [self._to_issue(d) for d in resp.json() if "pull_request" not in d][:limit]

    async def post_comment(self, item_id: str, body: str) -> str:
        resp = await self._request(
            "POST", f"/repos/{self._repo}/issues/{item_id}/comments", json={"body": body}
        )
        return str(resp.json()["html_url"])

    def _to_issue(self, data: dict[str, Any]) -> RawIssue:
        labels = [str(lbl["name"]) for lbl in (data.get("labels") or []) if isinstance(lbl, dict)]
        return RawIssue(
            id=str(data["number"]),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            url=str(data.get("html_url") or ""),
            labels=labels,
            state=str(data.get("state") or "open"),
            source=self.kind,
        )
