"""Jira task sink — create one Jira issue per ticket (REST API v3 over httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from ash.schemas import Spec, Ticket
from ash.sinks.base import TicketRef


class JiraTaskSink:
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
            raise ValueError("Jira sink requires base_url (e.g. https://x.atlassian.net)")
        self._email = str(config.get("email", ""))
        self._project_key = str(config.get("project_key", ""))
        # Use a type that actually exists in the target project. "Task" is the safe default;
        # override per project via config {"issue_type": "...", "spike_issue_type": "..."}.
        self._issue_type = str(config.get("issue_type") or "Task")
        self._spike_type = str(config.get("spike_issue_type") or self._issue_type)
        self._token = token
        self._api = base_url.rstrip("/")
        self._http = http

    async def publish(self, spec: Spec) -> list[TicketRef]:
        refs: list[TicketRef] = []
        for t in spec.tickets:
            refs.append(await self._create(t))
        return refs

    async def _create(self, ticket: Ticket) -> TicketRef:
        issue_type = self._spike_type if ticket.needs_research else self._issue_type
        payload = {
            "fields": {
                "project": {"key": self._project_key},
                "summary": ticket.title,
                "issuetype": {"name": issue_type},
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": ticket.description}],
                        }
                    ],
                },
            }
        }
        data = await self._post("/rest/api/3/issue", payload)
        key = str(data.get("key") or data.get("id") or ticket.id)
        return TicketRef(id=key, url=f"{self._api}/browse/{key}", sink=self.kind)

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        headers = {"accept": "application/json"}
        auth = (self._email, self._token)
        if self._http is not None:
            resp = await self._http.post(
                f"{self._api}{path}", headers=headers, auth=auth, json=json
            )
        else:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.post(f"{self._api}{path}", headers=headers, auth=auth, json=json)
        if resp.is_error:
            # surface Jira's actual reason (e.g. invalid issuetype / missing required field)
            raise RuntimeError(f"Jira {resp.status_code} creating issue: {resp.text[:600]}")
        result: dict[str, Any] = resp.json()
        return result
