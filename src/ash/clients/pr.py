"""Pull-request operations via the GitHub REST API (token auth, no CLI dependency).

Uses the `GITHUB_TOKEN` (from settings / .env) directly over HTTPS, so it works headless inside a
container / CI where the `gh` CLI is neither installed nor authenticated — the same token we embed
for `git push`.

Fork-internal only for now: head and base both live in the target repo. We never open PRs against
upstream while building/testing (plan decision #5).
"""

from __future__ import annotations

import os
import re

import httpx

GITHUB_API = "https://api.github.com"
_TIMEOUT = 30.0


class GhNotAvailable(RuntimeError):
    """Raised when no GitHub token is configured (kept name for backward compat)."""


def _token() -> str:
    # Prefer settings (reliably loaded from .env by pydantic) and fall back to the process env.
    try:
        from ash.config.settings import get_settings

        token = get_settings().github_token
    except Exception:  # noqa: BLE001 — settings unavailable → fall back to env
        token = ""
    token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token:
        raise GhNotAvailable(
            "no GitHub token configured — set GITHUB_TOKEN in .env to create/manage PRs"
        )
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_pr_url(pr: str) -> tuple[str, str, int]:
    """Parse owner/repo/number from a PR URL or `owner/repo#123` reference."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    m = re.match(r"([^/]+)/([^/#]+)#(\d+)$", pr)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    raise ValueError(f"cannot parse PR reference: {pr!r}")


def _check(resp: httpx.Response, action: str) -> None:
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub API {action} failed ({resp.status_code}): {resp.text[:500]}")


def create_pr(
    *, target_repo: str, base: str, head: str, title: str, body: str, draft: bool = True
) -> str:
    """Open a PR in target_repo (head -> base). Returns the PR URL.

    On a re-run the branch is force-pushed and a PR for head->base may already exist (GitHub 422);
    we then look the open PR up, refresh its body, and return its URL instead of failing.
    """
    owner = target_repo.split("/")[0]
    resp = httpx.post(
        f"{GITHUB_API}/repos/{target_repo}/pulls",
        headers=_headers(),
        json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 422:
        existing = _find_open_pr(target_repo, owner, head, base)
        if existing:
            try:  # best-effort: keep the existing PR body in sync with the latest attempt
                edit_pr_body(pr=existing, body=body)
            except Exception:  # noqa: BLE001
                pass
            return existing
    _check(resp, "create PR")
    return str(resp.json()["html_url"])


def _find_open_pr(target_repo: str, owner: str, head: str, base: str) -> str | None:
    resp = httpx.get(
        f"{GITHUB_API}/repos/{target_repo}/pulls",
        headers=_headers(),
        params={"head": f"{owner}:{head}", "base": base, "state": "open"},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        return None
    items = resp.json()
    return str(items[0]["html_url"]) if items else None


def comment_pr(*, pr: str, body: str) -> str:
    """Post a comment on a PR (identified by URL or number). Returns the comment URL."""
    owner, repo, number = _parse_pr_url(pr)
    resp = httpx.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments",
        headers=_headers(),
        json={"body": body},
        timeout=_TIMEOUT,
    )
    _check(resp, "comment PR")
    return str(resp.json()["html_url"])


def review_pr(*, pr: str, body: str, approve: bool) -> None:
    """Submit a PR review — approve, or a request-changes review.

    GitHub forbids approving/requesting-changes on your OWN PR (422). Since the agent's token
    often owns the PR, we fall back to a plain comment so the review is still recorded.
    """
    owner, repo, number = _parse_pr_url(pr)
    event = "APPROVE" if approve else "REQUEST_CHANGES"
    resp = httpx.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews",
        headers=_headers(),
        json={"body": body, "event": event},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 422:
        # Can't formally review your own PR — record the verdict as a comment instead.
        comment_pr(pr=pr, body=f"**ASH review ({event})**\n\n{body}")
        return
    _check(resp, "review PR")


def edit_pr_body(*, pr: str, body: str) -> None:
    """Replace a PR's description (used by the Fixer to keep it in sync after fixes)."""
    owner, repo, number = _parse_pr_url(pr)
    resp = httpx.patch(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
        headers=_headers(),
        json={"body": body},
        timeout=_TIMEOUT,
    )
    _check(resp, "edit PR body")


def merge_pr(*, pr: str, method: str = "squash") -> None:
    """Merge a PR (squash by default). Only call when policy + approval gate allow it."""
    owner, repo, number = _parse_pr_url(pr)
    resp = httpx.put(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/merge",
        headers=_headers(),
        json={"merge_method": method},
        timeout=_TIMEOUT,
    )
    _check(resp, "merge PR")
