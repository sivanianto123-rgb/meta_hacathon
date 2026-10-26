"""Codebase heat map: churn, bug-fix pressure and knowledge concentration per file.

All scores come straight from git history. Muse Spark is used only to phrase a short note for the
riskiest files, from the numbers and commit subjects computed here.
"""

from __future__ import annotations

import fnmatch
import re
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import gitdata
from .llm import ChatModel, LLMError, LLMNotConfigured, parse_json_reply

FIX_RE = re.compile(r"\b(fix(es|ed)?|bug|hotfix|revert|regression|crash|broken|patch|incident)\b", re.IGNORECASE)

DEFAULT_IGNORE = [
    "*.lock", "*lock.json", "*.min.js", "*.min.css", "*.svg", "*.png", "*.jpg", "*.gif", "*.ico",
    "vendor/*", "*/vendor/*", "node_modules/*", "dist/*", "build/*", "*.pb.go", "*_pb2.py",
]

# Bus factor counts authors needed to cover more than this share of a file's changes.
OWNERSHIP_THRESHOLD = 0.5
# A file is "only one person knows this" when its top author did at least this share of the work.
SOLE_OWNER_SHARE = 0.8
MIN_COMMITS_FOR_OWNERSHIP = 3
STALE_OWNER_DAYS = 90


KINDS = ("code", "test", "config", "docs")
DOC_EXT = (".md", ".rst", ".txt", ".adoc")
CONFIG_EXT = (".yml", ".yaml", ".toml", ".json", ".cfg", ".ini", ".lock", ".vim", ".in")
CONFIG_NAMES = {"Dockerfile", "Makefile", ".gitignore", ".pre-commit-config.yaml", "tox.ini", "noxfile.py", "setup.cfg"}


def file_kind(path: str) -> str:
    """Rough bucket so the map can default to source code instead of CI and docs churn."""
    name = path.rsplit("/", 1)[-1]
    parts = path.split("/")
    if parts[0] in (".github", ".circleci", ".gitlab") or name in CONFIG_NAMES or name.endswith(CONFIG_EXT):
        return "config"
    if parts[0] in ("docs", "doc") or name.endswith(DOC_EXT) or name.isupper():
        return "docs"
    if (any(p in ("test", "tests", "testing", "__tests__") for p in parts[:-1])
            or name.startswith("test_") or re.search(r"[._-](test|spec)\.[a-z]+$", name)):
        return "test"
    return "code"


@dataclass
class FileRisk:
    path: str
    kind: str
    commits: int
    churn: int
    fix_commits: int
    authors: int
    top_author: str
    top_share: float
    bus_factor: int
    owner_last_active_days: int | None
    risk: float = 0.0
    flags: list[str] = field(default_factory=list)
    recent_fixes: list[str] = field(default_factory=list)
    note: str | None = None


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def _percentiles(values: list[float]) -> list[float]:
    """Fraction of values strictly below each value (0..1); ties share a rank."""
    ordered = sorted(values)
    n = len(values)
    if n <= 1:
        return [0.0 for _ in values]
    return [bisect_right(ordered, v - 1e-12) / (n - 1) for v in values]


def _days_ago(iso: str, now: datetime) -> int:
    return max(0, (now - datetime.fromisoformat(iso)).days)


def analyze(
    repo: str,
    since: str = "12 months ago",
    ignore: list[str] | None = None,
    kinds: tuple[str, ...] = ("code",),
    now: datetime | None = None,
) -> list[FileRisk]:
    now = now or datetime.now(timezone.utc)
    patterns = DEFAULT_IGNORE + (ignore or [])
    tracked = gitdata.tracked_files(repo)
    changes = gitdata.file_changes(repo, since=since)

    # Last time each person committed anywhere in the window: an owner who has gone quiet is a risk.
    last_active: dict[str, str] = {}
    names: dict[str, str] = {}
    for file_changes in changes.values():
        for ch in file_changes:
            key = ch.email.lower()
            names.setdefault(key, ch.author)
            if ch.date > last_active.get(key, ""):
                last_active[key] = ch.date

    files: list[FileRisk] = []
    for path, file_changes in changes.items():
        if path not in tracked or _ignored(path, patterns) or file_kind(path) not in kinds:
            continue
        by_author: dict[str, int] = {}
        shas: set[str] = set()
        fix_shas: set[str] = set()
        fixes: list[str] = []
        for ch in file_changes:
            weight = max(1, ch.added + ch.deleted)
            by_author[ch.email.lower()] = by_author.get(ch.email.lower(), 0) + weight
            shas.add(ch.sha)
            if FIX_RE.search(ch.subject) and ch.sha not in fix_shas:
                fix_shas.add(ch.sha)
                fixes.append(ch.subject)
        total = sum(by_author.values())
        ranked = sorted(by_author.items(), key=lambda kv: kv[1], reverse=True)
        covered, bus = 0, 0
        for _, w in ranked:
            covered += w
            bus += 1
            if covered / total > OWNERSHIP_THRESHOLD:
                break
        top_key, top_w = ranked[0]
        files.append(
            FileRisk(
                path=path,
                kind=file_kind(path),
                commits=len(shas),
                churn=sum(ch.added + ch.deleted for ch in file_changes),
                fix_commits=len(fix_shas),
                authors=len(by_author),
                top_author=names.get(top_key, top_key),
                top_share=round(top_w / total, 3),
                bus_factor=bus,
                owner_last_active_days=_days_ago(last_active[top_key], now) if top_key in last_active else None,
                recent_fixes=fixes[:5],  # git log is newest first
            )
        )

    churn_pct = _percentiles([f.churn for f in files])
    fix_pct = _percentiles([f.fix_commits for f in files])
    for f, cp, fp in zip(files, churn_pct, fix_pct):
        sole = f.commits >= MIN_COMMITS_FOR_OWNERSHIP and f.top_share >= SOLE_OWNER_SHARE
        concentration = f.top_share if f.commits >= MIN_COMMITS_FOR_OWNERSHIP else 0.0
        f.risk = round(100 * (0.35 * cp + 0.40 * fp + 0.25 * concentration), 1)
        if sole:
            f.flags.append(f"only {f.top_author} knows this")
            if f.owner_last_active_days is not None and f.owner_last_active_days > STALE_OWNER_DAYS:
                f.flags.append(f"{f.top_author} inactive {f.owner_last_active_days}d")
        if f.fix_commits >= 3 and fp >= 0.9:
            f.flags.append("bug magnet")
    files.sort(key=lambda f: f.risk, reverse=True)
    return files


NOTES_SYSTEM = """You write one-line risk notes for a codebase heat map, for engineering leads.
For each file you get git-derived metrics and recent bug-fix commit subjects.
Rules: use only the data given; under 25 words per note; be concrete (what keeps breaking, who holds the knowledge);
no blame, no speculation about code quality you cannot see.
Reply with JSON only: {"notes": {"<path>": "<note>", ...}}"""


def add_notes(files: list[FileRisk], model: ChatModel, top: int = 10) -> str | None:
    """Annotate the riskiest files in place. Returns an error string instead of raising."""
    subset = files[:top]
    if not subset:
        return None
    lines = []
    for f in subset:
        fixes = "; ".join(f.recent_fixes) or "none"
        lines.append(
            f"- {f.path}: risk {f.risk}, {f.commits} commits, churn {f.churn}, {f.fix_commits} fix commits, "
            f"{f.authors} authors, top author {f.top_author} ({f.top_share:.0%}), bus factor {f.bus_factor}, "
            f"flags [{', '.join(f.flags)}], recent fixes: {fixes}"
        )
    try:
        reply = parse_json_reply(model.complete(NOTES_SYSTEM, "\n".join(lines)))
    except (LLMError, LLMNotConfigured) as e:
        return str(e)
    notes = reply.get("notes") or {}
    for f in subset:
        if isinstance(notes.get(f.path), str):
            f.note = notes[f.path].strip()
    return None


def to_json(files: list[FileRisk]) -> list[dict]:
    return [asdict(f) for f in files]
