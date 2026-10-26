import json
import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str, author: str = "Alice", email: str = "alice@example.com", date: str = "2026-01-01T10:00:00Z"):
    env = {
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo),
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo: Path, path: str, content: str, message: str, **who):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    git(repo, "add", path, **who)
    git(repo, "commit", "-q", "-m", message, **who)


RAHUL = {"author": "Rahul", "email": "rahul@example.com"}
BOB = {"author": "Bob", "email": "bob@example.com"}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small history: billing.py is Rahul's and keeps getting fixed; utils.py is shared."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "remote", "add", "origin", "https://github.com/acme/shop.git")

    billing = ["def charge(order):", "    return gateway.charge(order.total)"]
    commit(r, "billing.py", "\n".join(billing) + "\n", "Add billing", date="2026-01-02T10:00:00Z", **RAHUL)
    billing.insert(1, "    if order.charged:  # avoid double charge on retry")
    billing.insert(2, "        return order.receipt")
    commit(r, "billing.py", "\n".join(billing) + "\n",
           "Fix double charge when payment webhook retries (#42)\n\nThe gateway retries webhooks on 5xx.",
           date="2026-02-01T10:00:00Z", **RAHUL)
    for i in range(3):
        billing.append(f"# tweak {i}")
        commit(r, "billing.py", "\n".join(billing) + "\n", f"fix rounding bug {i}",
               date=f"2026-03-0{i + 1}T10:00:00Z", **RAHUL)

    commit(r, "utils.py", "def slug(s):\n    return s.lower()\n", "Add utils", date="2026-01-03T10:00:00Z", **BOB)
    commit(r, "utils.py", "def slug(s):\n    return s.strip().lower()\n", "Strip slugs", date="2026-01-04T10:00:00Z")
    commit(r, "utils.py", "def slug(s):\n    return s.strip().lower().replace(' ', '-')\n", "Hyphenate", date="2026-01-05T10:00:00Z", **BOB)
    commit(r, "package-lock.json", "{}\n", "lock", date="2026-01-05T10:00:00Z", **BOB)
    return r


class FakeModel:
    """Returns queued replies and records prompts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)
