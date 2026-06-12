"""Plane provider (REST API over httpx).

Auth is the `X-API-Key` header (the integration secret). `config` needs `workspace_slug` and
`project_id`. `base_url` defaults to Plane Cloud (`https://api.plane.so`); set it for self-hosted.
"""

from __future__ import annotations

from typing import Any

import httpx

from ash.integrations.base import RawIssue

_CLOUD = "https://api.plane.so"


class PlaneIssueProvider:
    kind = "plane"

    def __init__(
        self,
        *,
        token: str,
        config: dict[str, Any],
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._workspace = str(config.get("workspace_slug", ""))
        self._project = str(config.get("project_id", ""))
        self._api = (base_url or _CLOUD).rstrip("/")
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._token, "accept": "application/json"}

    def _base(self) -> str:
        return f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues"

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
        resp = await self._request("GET", f"{self._base()}/{item_id}/")
        return self._to_issue(resp.json())

    async def list_issues(
        self, *, filters: dict[str, object] | None = None, limit: int = 20
    ) -> list[RawIssue]:
        resp = await self._request("GET", f"{self._base()}/", params={"per_page": min(limit, 100)})
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        return [self._to_issue(d) for d in results][:limit]

    async def post_comment(self, item_id: str, body: str) -> str:
        resp = await self._request(
            "POST", f"{self._base()}/{item_id}/comments/", json={"comment_html": f"<p>{body}</p>"}
        )
        return str(resp.json().get("id", item_id))

    async def create_issue(self, title: str, body: str) -> str:
        resp = await self._request(
            "POST", f"{self._base()}/", json={"name": title, "description_stripped": body}
        )
        return str(resp.json().get("id", ""))

    def _to_issue(self, data: dict[str, Any]) -> RawIssue:
        iid = str(data.get("id") or "")
        body = str(data.get("description_stripped") or data.get("description_html") or "")
        return RawIssue(
            id=iid,
            title=str(data.get("name") or ""),
            body=body,
            url=str(data.get("url") or ""),
            labels=[str(x) for x in (data.get("labels") or [])],
            state=str(data.get("state") or "open"),
            source=self.kind,
        )
