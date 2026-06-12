"""Plane task sink — create one Plane issue per ticket (REST API over httpx)."""

from __future__ import annotations

from typing import Any

import httpx

from ash.schemas import Spec, Ticket
from ash.sinks.base import TicketRef

_CLOUD = "https://api.plane.so"


class PlaneTaskSink:
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

    async def publish(self, spec: Spec) -> list[TicketRef]:
        refs: list[TicketRef] = []
        for t in spec.tickets:
            refs.append(await self._create(t))
        return refs

    async def _create(self, ticket: Ticket) -> TicketRef:
        prefix = "[SPIKE] " if ticket.needs_research else ""
        payload = {
            "name": f"{prefix}{ticket.title}",
            "description_html": f"<p>{ticket.description}</p>",
        }
        path = f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues/"
        data = await self._post(path, payload)
        iid = str(data.get("id") or ticket.id)
        return TicketRef(id=iid, url=str(data.get("url") or ""), sink=self.kind)

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        headers = {"x-api-key": self._token, "accept": "application/json"}
        if self._http is not None:
            resp = await self._http.post(f"{self._api}{path}", headers=headers, json=json)
        else:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.post(f"{self._api}{path}", headers=headers, json=json)
        if resp.is_error:
            raise RuntimeError(f"Plane {resp.status_code} creating issue: {resp.text[:600]}")
        result: dict[str, Any] = resp.json()
        return result
