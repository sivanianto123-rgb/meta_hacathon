"""Thin wrappers around the git CLI. Everything here is deterministic: no model calls."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Unit/record separators keep commit messages with newlines and pipes parseable.
US = "\x1f"
RS = "\x1e"

PR_PATTERNS = [
    re.compile(r"Merge pull request #(\d+)"),
    re.compile(r"\(#(\d+)\)"),
    re.compile(r"(?:PR|pull request)\s*#(\d+)", re.IGNORECASE),
]


class GitError(RuntimeError):
    pass


def run_git(repo: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    date: str  # ISO 8601
    subject: str
    body: str = ""
    pr_numbers: list[int] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.sha[:8]


def extract_pr_numbers(text: str) -> list[int]:
    found: list[int] = []
    for pattern in PR_PATTERNS:
        for m in pattern.finditer(text):
            n = int(m.group(1))
            if n not in found:
                found.append(n)
    return found


_COMMIT_FMT = US.join(["%H", "%an", "%ae", "%aI", "%s", "%b"]) + RS


def _parse_commits(raw: str) -> list[Commit]:
    commits = []
    for record in raw.split(RS):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(US)
        if len(parts) < 6:
            continue
        sha, author, email, date, subject, body = parts[:6]
        commits.append(
            Commit(
                sha=sha.strip(),
                author=author,
                email=email,
                date=date,
                subject=subject,
                body=body.strip(),
                pr_numbers=extract_pr_numbers(subject + "\n" + body),
            )
        )
    return commits


def line_history(repo: str | Path, path: str, line: int, max_commits: int = 12) -> list[Commit]:
    """Commits that shaped `line` of `path`, newest first (git log -L follows moves within the file)."""
    raw = run_git(
        repo,
        "log",
        f"-L{line},{line}:{path}",
        "-s",
        f"--max-count={max_commits}",
        f"--format={_COMMIT_FMT}",
    )
    return _parse_commits(raw)


def read_lines(repo: str | Path, path: str, center: int, radius: int = 6) -> tuple[int, list[str]]:
    """Return (first_line_number, lines) around `center` from the working tree."""
    text = (Path(repo) / path).read_text(errors="replace").splitlines()
    if not 1 <= center <= len(text):
        raise ValueError(f"{path} has {len(text)} lines; line {center} is out of range")
    start = max(1, center - radius)
    end = min(len(text), center + radius)
    return start, text[start - 1 : end]


@dataclass
class FileChange:
    sha: str
    author: str
    email: str
    date: str
    subject: str
    added: int
    deleted: int


def file_changes(repo: str | Path, since: str | None = None) -> dict[str, list[FileChange]]:
    """Map path -> list of non-merge changes, using one `git log --numstat` pass."""
    args = ["log", "--no-merges", "--no-renames", "--numstat", f"--format={RS}%H{US}%an{US}%ae{US}%aI{US}%s"]
    if since:
        args.append(f"--since={since}")
    raw = run_git(repo, *args)
    out: dict[str, list[FileChange]] = {}
    for record in raw.split(RS):
        if not record.strip():
            continue
        header, _, stats = record.partition("\n")
        parts = header.split(US)
        if len(parts) < 5:
            continue
        sha, author, email, date, subject = parts[:5]
        for row in stats.splitlines():
            cols = row.split("\t")
            if len(cols) != 3:
                continue
            added, deleted, path = cols
            # Binary files report "-" for both counts.
            a = int(added) if added.isdigit() else 0
            d = int(deleted) if deleted.isdigit() else 0
            out.setdefault(path, []).append(FileChange(sha, author, email, date, subject, a, d))
    return out


def tracked_files(repo: str | Path) -> set[str]:
    return set(run_git(repo, "ls-files").splitlines())


def repo_root(path: str | Path) -> Path:
    return Path(run_git(path, "rev-parse", "--show-toplevel").strip())


def remote_slug(repo: str | Path) -> str | None:
    """'owner/name' for a GitHub origin remote, else None."""
    try:
        url = run_git(repo, "remote", "get-url", "origin").strip()
    except GitError:
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None
