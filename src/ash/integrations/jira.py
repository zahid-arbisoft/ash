"""Jira Cloud provider (REST API v3 over httpx).

Auth is HTTP Basic with `email:api_token` (`config.email` + the integration secret). `config` needs
`email` and `project_key`. `base_url` is the site, e.g. `https://your-domain.atlassian.net`.
Descriptions come back as Atlassian Document Format (ADF); we flatten them to plain text.
"""

from __future__ import annotations

from typing import Any

import httpx

from ash.integrations.base import RawIssue


class JiraIssueProvider:
    kind = "jira"

    def __init__(
        self,
        *,
        token: str,
        config: dict[str, Any],
        base_url: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("Jira integration requires base_url (e.g. https://x.atlassian.net)")
        self._email = str(config.get("email", ""))
        self._project_key = str(config.get("project_key", ""))
        self._token = token
        self._api = base_url.rstrip("/")
        self._http = http

    @property
    def _auth(self) -> tuple[str, str]:
        return (self._email, self._token)

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {"accept": "application/json"}
        if self._http is not None:
            resp = await self._http.request(
                method, f"{self._api}{path}", headers=headers, auth=self._auth, **kw
            )
        else:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.request(
                    method, f"{self._api}{path}", headers=headers, auth=self._auth, **kw
                )
        resp.raise_for_status()
        return resp

    async def fetch_issue(self, item_id: str) -> RawIssue:
        resp = await self._request("GET", f"/rest/api/3/issue/{item_id}")
        return self._to_issue(resp.json())

    async def list_issues(
        self, *, filters: dict[str, object] | None = None, limit: int = 20
    ) -> list[RawIssue]:
        filters = filters or {}
        status = filters.get("state")
        jql = f"project = {self._project_key}"
        if status:
            jql += f' AND statusCategory = "{status}"'
        jql += " ORDER BY created DESC"
        resp = await self._request(
            "GET", "/rest/api/3/search", params={"jql": jql, "maxResults": min(limit, 100)}
        )
        return [self._to_issue(d) for d in resp.json().get("issues", [])][:limit]

    async def post_comment(self, item_id: str, body: str) -> str:
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
            }
        }
        resp = await self._request("POST", f"/rest/api/3/issue/{item_id}/comment", json=payload)
        cid = resp.json().get("id", "")
        return f"{self._api}/browse/{item_id}?focusedCommentId={cid}"

    def _to_issue(self, data: dict[str, Any]) -> RawIssue:
        key = str(data.get("key") or data.get("id") or "")
        fields = data.get("fields") or {}
        labels = [str(x) for x in (fields.get("labels") or [])]
        status = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") or "open"
        return RawIssue(
            id=key,
            title=str(fields.get("summary") or ""),
            body=_adf_to_text(fields.get("description")),
            url=f"{self._api}/browse/{key}" if key else "",
            labels=labels,
            state=str(status),
            source=self.kind,
        )


def _adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree (or plain string) to text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        parts = _adf_to_text(node.get("content"))
        if node.get("type") in {"paragraph", "heading"}:
            parts += "\n"
        return parts
    return ""
