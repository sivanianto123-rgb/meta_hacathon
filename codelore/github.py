"""Fetch pull request context from the GitHub REST API. Optional: works unauthenticated for
public repos (60 requests/hour); set GITHUB_TOKEN for more."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

import requests

API = "https://api.github.com"
MAX_TEXT = 1500
MAX_COMMENTS = 8


@dataclass
class PullRequest:
    number: int
    title: str
    author: str
    url: str
    body: str
    merged_at: str | None
    comments: list[str] = field(default_factory=list)


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _clip(text: str | None, limit: int = MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


@lru_cache(maxsize=512)
def fetch_pr(slug: str, number: int) -> PullRequest | None:
    """Return the PR with its discussion, or None if it is unreachable (missing, rate-limited, offline)."""
    try:
        r = requests.get(f"{API}/repos/{slug}/pulls/{number}", headers=_headers(), timeout=15)
        if r.status_code != 200:
            return None
        pr = r.json()
        comments: list[str] = []
        for kind in ("issues", "pulls"):  # conversation comments, then inline review comments
            c = requests.get(
                f"{API}/repos/{slug}/{kind}/{number}/comments",
                headers=_headers(),
                params={"per_page": MAX_COMMENTS},
                timeout=15,
            )
            if c.status_code == 200:
                for item in c.json():
                    comments.append(f"{item['user']['login']}: {_clip(item.get('body'), 600)}")
    except requests.RequestException:
        return None
    return PullRequest(
        number=number,
        title=pr.get("title", ""),
        author=(pr.get("user") or {}).get("login", ""),
        url=pr.get("html_url", f"https://github.com/{slug}/pull/{number}"),
        body=_clip(pr.get("body")),
        merged_at=pr.get("merged_at"),
        comments=comments[:MAX_COMMENTS],
    )


@lru_cache(maxsize=1024)
def prs_for_commit(slug: str, sha: str) -> tuple[int, ...]:
    """PR numbers GitHub associates with a commit; covers repos that merge without '(#123)' in messages."""
    try:
        r = requests.get(f"{API}/repos/{slug}/commits/{sha}/pulls", headers=_headers(), timeout=15)
    except requests.RequestException:
        return ()
    if r.status_code != 200:
        return ()
    return tuple(pr["number"] for pr in r.json() if isinstance(pr, dict) and "number" in pr)
