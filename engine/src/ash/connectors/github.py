"""Read-only GitHub issue access for the issue source repo (e.g. makeplane/plane).

Phase 0 only reads. We deliberately don't write upstream (we don't own it). Write access to the
fork (branches/PRs/merges via GitPython + PyGithub) arrives in Phase 1.

Uses the REST API via `requests`. A token (GITHUB_TOKEN) lifts the rate limit but isn't required
for public repos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

_API = "https://api.github.com"


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]
    url: str
    state: str


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _to_issue(data: dict) -> Issue:
    return Issue(
        number=data["number"],
        title=data["title"] or "",
        body=data.get("body") or "",
        labels=[lbl["name"] for lbl in data.get("labels", [])],
        url=data["html_url"],
        state=data.get("state", "open"),
    )


def fetch_issue(repo: str, number: int) -> Issue:
    resp = requests.get(f"{_API}/repos/{repo}/issues/{number}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "pull_request" in data:
        raise ValueError(f"{repo}#{number} is a pull request, not an issue")
    return _to_issue(data)


def list_issues(repo: str, filters: dict | None = None, limit: int = 20) -> list[Issue]:
    filters = filters or {}
    params = {
        "state": filters.get("state", "open"),
        "per_page": min(limit, 100),
        "sort": "created",
        "direction": "desc",
    }
    labels = filters.get("labels")
    if labels:
        params["labels"] = ",".join(labels)
    resp = requests.get(
        f"{_API}/repos/{repo}/issues", headers=_headers(), params=params, timeout=30
    )
    resp.raise_for_status()
    # the issues endpoint also returns PRs; filter them out
    return [_to_issue(d) for d in resp.json() if "pull_request" not in d][:limit]
