"""'Why is this here?' — explain a line of code from its git and PR history.

The history is gathered deterministically; Muse Spark only narrates it, and every claim in the
narration must cite an evidence id we supplied. Citations to anything else are stripped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import gitdata, github
from .llm import ChatModel, LLMError, LLMNotConfigured, parse_json_reply, strip_unknown_citations

SYSTEM = """You explain why a line of code exists, for a developer hovering over it in their editor.
You get the code around the line and numbered evidence: commits (C1, C2, ...) and pull requests (P1, ...).
Rules:
- Use ONLY the evidence. If the evidence does not say why, say so plainly; never guess intent.
- Cite evidence ids in square brackets after each claim, e.g. "Added to stop double charges [C2][P1]."
- Tell it as a short story in chronological order: what problem led to this line, what changed since.
- Mention people only as the authors of a change, never to assign blame.
- At most 120 words for "story". "summary" is one sentence under 20 words.
Reply with JSON only: {"summary": "...", "story": "..."}"""


@dataclass
class Evidence:
    id: str
    kind: str  # "commit" | "pr"
    title: str
    author: str
    date: str
    url: str | None
    text: str


def gather(repo: str, path: str, line: int, slug: str | None, fetch_prs: bool = True) -> tuple[int, list[str], list[Evidence]]:
    start, code = gitdata.read_lines(repo, path, line)
    commits = gitdata.line_history(repo, path, line)
    evidence: list[Evidence] = []
    seen_prs: set[int] = set()
    pr_queue: list[int] = []
    for i, c in enumerate(commits, 1):
        evidence.append(
            Evidence(
                id=f"C{i}",
                kind="commit",
                title=c.subject,
                author=c.author,
                date=c.date[:10],
                url=f"https://github.com/{slug}/commit/{c.sha}" if slug else None,
                text=f"{c.short} {c.subject}\n{c.body}".strip(),
            )
        )
        numbers = c.pr_numbers
        if not numbers and slug and fetch_prs and i <= 3:
            numbers = list(github.prs_for_commit(slug, c.sha))
        for n in numbers:
            if n not in seen_prs:
                seen_prs.add(n)
                pr_queue.append(n)
    if slug and fetch_prs:
        for j, n in enumerate(pr_queue[:5], 1):
            pr = github.fetch_pr(slug, n)
            if pr is None:
                continue
            discussion = "\n".join(pr.comments)
            evidence.append(
                Evidence(
                    id=f"P{j}",
                    kind="pr",
                    title=f"#{pr.number} {pr.title}",
                    author=pr.author,
                    date=(pr.merged_at or "")[:10],
                    url=pr.url,
                    text=f"#{pr.number} {pr.title}\n{pr.body}\n{discussion}".strip(),
                )
            )
    return start, code, evidence


def build_prompt(path: str, line: int, start: int, code: list[str], evidence: list[Evidence]) -> str:
    numbered = "\n".join(
        f"{'>>' if start + i == line else '  '} {start + i:5d} | {text}" for i, text in enumerate(code)
    )
    ev = "\n\n".join(f"[{e.id}] {e.kind} by {e.author} on {e.date}\n{e.text}" for e in evidence)
    return f"File: {path}, line {line} (marked >>)\n\n{numbered}\n\nEvidence (newest commit first):\n\n{ev}"


def explain(repo: str, path: str, line: int, model: ChatModel | None, fetch_prs: bool = True) -> dict:
    slug = gitdata.remote_slug(repo)
    start, code, evidence = gather(repo, path, line, slug, fetch_prs)
    result = {
        "path": path,
        "line": line,
        "code_start": start,
        "code": code,
        "evidence": [asdict(e) for e in evidence],
        "summary": None,
        "story": None,
        "removed_citations": [],
        "error": None,
    }
    if not evidence:
        result["error"] = "No committed history for this line (new or uncommitted change)."
        return result
    if model is None:
        result["error"] = "Model not configured; showing raw history only."
        return result
    try:
        reply = parse_json_reply(model.complete(SYSTEM, build_prompt(path, line, start, code, evidence)))
    except (LLMError, LLMNotConfigured) as e:
        result["error"] = str(e)
        return result
    known = {e.id for e in evidence}
    story, removed = strip_unknown_citations(str(reply.get("story", "")), known)
    summary, removed2 = strip_unknown_citations(str(reply.get("summary", "")), known)
    result.update(story=story.strip(), summary=summary.strip(), removed_citations=removed + removed2)
    return result
