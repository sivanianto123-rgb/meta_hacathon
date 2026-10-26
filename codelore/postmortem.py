"""Blame-free postmortem writer: incident thread + logs (+ optional call recording) -> postmortem.

Inputs are split into numbered evidence lines (T = thread, L = logs, V = voice transcript).
Muse Spark drafts the postmortem as JSON where every timeline entry, cause and action item must
cite evidence. Uncited items are moved to a "needs review" list instead of being published, and a
blame-language check runs on the result, with one rewrite pass if it finds anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .llm import ChatModel, LLMError, parse_json_reply

MAX_LOG_LINES = 150
MAX_THREAD_LINES = 250

LOG_SIGNAL = re.compile(
    r"\b(WARN(ING)?|ERROR|ERR|FATAL|CRIT(ICAL)?|PANIC|Exception|Traceback|timed? ?out|refused|OOM|killed|5\d\d)\b"
)
THREAD_LINE = re.compile(r"^\s*(?:\[(?P<ts>[^\]]+)\]\s*)?(?P<who>[\w .@'-]{1,40}?):\s+(?P<text>.+)$")

BLAME_PATTERNS = [
    r"human error", r"should have", r"shouldn'?t have", r"failed to", r"careless", r"negligen\w*",
    r"\bfault\b", r"\bblame\w*", r"forgot to", r"didn'?t bother", r"mistake (by|of)", r"\bincompeten\w*",
    r"\bsloppy\b", r"\blazy\b", r"\bdropped the ball\b",
]
BLAME_RE = re.compile("|".join(BLAME_PATTERNS), re.IGNORECASE)


@dataclass
class EvidenceLine:
    id: str
    source: str  # "thread" | "logs" | "voice"
    time: str | None
    who: str | None
    text: str


@dataclass
class Inputs:
    evidence: list[EvidenceLine] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    dropped_log_lines: int = 0


def _slack_ts(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def parse_thread(raw: str) -> list[tuple[str | None, str | None, str]]:
    """Accept a Slack channel export (JSON list) or plain 'name: message' / '[time] name: message' lines."""
    stripped = raw.lstrip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            msgs = []
            for m in data:
                if not isinstance(m, dict) or not m.get("text"):
                    continue
                who = (
                    m.get("user_name")
                    or (m.get("user_profile") or {}).get("real_name")
                    or m.get("user")
                )
                msgs.append((_slack_ts(m.get("ts")), who, " ".join(m["text"].split())))
            msgs.sort(key=lambda x: x[0] or "")
            return msgs
    msgs: list[tuple[str | None, str | None, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = THREAD_LINE.match(line)
        # Once a thread uses timestamps, an untimed "word: text" line is a continuation, not a speaker.
        timestamped = any(ts for ts, _, _ in msgs)
        if m and not (timestamped and not m.group("ts")):
            msgs.append((m.group("ts"), m.group("who").strip(), m.group("text").strip()))
        elif msgs:  # continuation of the previous message
            ts, who, text = msgs[-1]
            msgs[-1] = (ts, who, f"{text} {line.strip()}")
        else:
            msgs.append((None, None, line.strip()))
    return msgs


def select_log_lines(raw: str) -> tuple[list[str], int]:
    """Keep signal lines (warnings, errors, timeouts), collapsing repeats that differ only in numbers."""
    lines = [l.rstrip() for l in raw.splitlines() if l.strip()]
    if len(lines) > MAX_LOG_LINES:
        lines = [l for l in lines if LOG_SIGNAL.search(l)]
    kept: list[str] = []
    counts: dict[str, int] = {}
    index: dict[str, int] = {}
    for l in lines:
        shape = re.sub(r"\d+", "#", l.split(" ", 2)[-1] if l[:4].isdigit() else l)
        if shape in index:
            counts[shape] += 1
            continue
        index[shape] = len(kept)
        counts[shape] = 1
        kept.append(l)
    for shape, i in index.items():
        if counts[shape] > 1:
            kept[i] = f"{kept[i]}  (repeated x{counts[shape]})"
    dropped = max(0, len(kept) - MAX_LOG_LINES)
    return kept[:MAX_LOG_LINES], dropped


LOG_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?|\b\d{2}:\d{2}:\d{2}\b)")


def build_inputs(thread: str, logs: str = "", transcript: str = "") -> Inputs:
    inp = Inputs()
    for i, (ts, who, text) in enumerate(parse_thread(thread)[:MAX_THREAD_LINES], 1):
        inp.evidence.append(EvidenceLine(f"T{i}", "thread", ts, who, text))
        if who and who not in inp.participants:
            inp.participants.append(who)
    log_lines, inp.dropped_log_lines = select_log_lines(logs) if logs.strip() else ([], 0)
    for i, l in enumerate(log_lines, 1):
        m = LOG_TIME.search(l)
        inp.evidence.append(EvidenceLine(f"L{i}", "logs", m.group(1) if m else None, None, l))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", transcript) if s.strip()]
    for i, s in enumerate(sentences, 1):
        inp.evidence.append(EvidenceLine(f"V{i}", "voice", None, None, s))
    return inp


SYSTEM = """You write blameless incident postmortems in the Google SRE style.
You get numbered evidence: incident chat thread lines (T#), log lines (L#), and call transcript lines (V#).
Rules:
- Use ONLY the evidence. Every timeline entry, root cause, contributing factor and action item lists the
  evidence ids that support it in "sources". If nothing supports a point, leave it out.
- Blameless: describe systems, processes and decisions, never individual fault. Do not write "human error",
  "should have", "failed to", "forgot", "careless". Refer to people by role (on-call engineer, DB owner),
  not by name.
- Times: copy them from the evidence; do not invent times. Use "time": null if unknown.
- Action items are concrete and checkable; owner is a team or role, not a person.
Reply with JSON only, in this shape:
{"title": str, "summary": str, "impact": str, "severity": "SEV1"|"SEV2"|"SEV3"|"unknown",
 "detection": {"text": str, "sources": [ids]},
 "timeline": [{"time": str|null, "event": str, "sources": [ids]}],
 "root_cause": {"text": str, "sources": [ids]},
 "contributing_factors": [{"text": str, "sources": [ids]}],
 "what_went_well": [{"text": str, "sources": [ids]}],
 "action_items": [{"action": str, "owner": str, "priority": "P0"|"P1"|"P2", "sources": [ids]}]}"""

REWRITE_SYSTEM = """Rewrite each sentence below in blameless postmortem language: describe the system, process
or missing safeguard instead of a person's failure. Keep every fact and every [ID] citation. Do not add facts.
Reply with JSON only: {"rewrites": {"<key>": "<rewritten sentence>", ...}}"""


def evidence_prompt(inp: Inputs) -> str:
    rows = []
    for e in inp.evidence:
        prefix = " ".join(x for x in (e.time and f"[{e.time}]", e.who and f"{e.who}:") if x)
        rows.append(f"{e.id} {prefix} {e.text}".replace("  ", " "))
    note = f"\n({inp.dropped_log_lines} further log lines omitted)" if inp.dropped_log_lines else ""
    return "Evidence:\n" + "\n".join(rows) + note


def _valid_sources(item: dict, known: set[str]) -> list[str]:
    return [s for s in (item.get("sources") or []) if isinstance(s, str) and s in known]


def validate(draft: dict, known: set[str]) -> tuple[dict, list[dict]]:
    """Drop citations we never supplied; move any item left without support to needs_review."""
    needs_review: list[dict] = []
    for key in ("timeline", "contributing_factors", "what_went_well", "action_items"):
        kept = []
        for item in draft.get(key) or []:
            if not isinstance(item, dict):
                continue
            item["sources"] = _valid_sources(item, known)
            if item["sources"]:
                kept.append(item)
            else:
                needs_review.append({"section": key, **item})
        draft[key] = kept
    for key in ("root_cause", "detection"):
        item = draft.get(key)
        if isinstance(item, dict):
            item["sources"] = _valid_sources(item, known)
            if not item["sources"]:
                needs_review.append({"section": key, **item})
                draft[key] = {"text": "Not established from the evidence provided.", "sources": []}
    return draft, needs_review


def _text_fields(draft: dict):
    """Yield (key, text) for every prose field, with a key that set_text() understands."""
    for key in ("title", "summary", "impact"):
        if isinstance(draft.get(key), str):
            yield key, draft[key]
    for key in ("root_cause", "detection"):
        if isinstance(draft.get(key), dict):
            yield f"{key}.text", draft[key].get("text", "")
    for key, field_name in (("timeline", "event"), ("contributing_factors", "text"),
                            ("what_went_well", "text"), ("action_items", "action")):
        for i, item in enumerate(draft.get(key) or []):
            yield f"{key}.{i}.{field_name}", item.get(field_name, "")


def _set_text(draft: dict, key: str, value: str) -> None:
    parts = key.split(".")
    if len(parts) == 1:
        draft[parts[0]] = value
    elif len(parts) == 2:
        draft[parts[0]][parts[1]] = value
    else:
        draft[parts[0]][int(parts[1])][parts[2]] = value


def blame_findings(draft: dict, participants: list[str]) -> list[dict]:
    findings = []
    names = [p for p in participants if len(p) > 2]
    for key, text in _text_fields(draft):
        hits = [m.group(0) for m in BLAME_RE.finditer(text or "")]
        named = [n for n in names if re.search(rf"\b{re.escape(n)}\b", text or "", re.IGNORECASE)]
        if hits or (named and key != "title"):
            findings.append({"field": key, "text": text, "phrases": hits, "names": named})
    return findings


def write(inp: Inputs, model: ChatModel) -> dict:
    if not inp.evidence:
        raise ValueError("No evidence: provide an incident thread, logs or a transcript")
    known = {e.id for e in inp.evidence}
    draft = parse_json_reply(model.complete(SYSTEM, evidence_prompt(inp)))
    draft, needs_review = validate(draft, known)

    findings = blame_findings(draft, inp.participants)
    rewritten = 0
    if findings:
        request = {f["field"]: f["text"] for f in findings}
        try:
            reply = parse_json_reply(model.complete(REWRITE_SYSTEM, json.dumps(request, indent=1)))
            for key, value in (reply.get("rewrites") or {}).items():
                if key in request and isinstance(value, str) and value.strip():
                    _set_text(draft, key, value.strip())
                    rewritten += 1
        except LLMError:
            pass
        findings = blame_findings(draft, inp.participants)

    return {
        "postmortem": draft,
        "needs_review": needs_review,
        "blame_findings": findings,
        "blame_rewrites": rewritten,
        "evidence": [e.__dict__ for e in inp.evidence],
        "markdown": render_markdown(draft, needs_review, findings, inp),
    }


def _cite(sources: list[str]) -> str:
    return " " + "".join(f"[{s}]" for s in sources) if sources else ""


def render_markdown(draft: dict, needs_review: list[dict], findings: list[dict], inp: Inputs) -> str:
    md = [f"# Postmortem: {draft.get('title', 'Untitled incident')}", ""]
    md += [f"**Severity:** {draft.get('severity', 'unknown')}  ", f"**Status:** Draft — generated by codelore, review before publishing", ""]
    md += ["## Summary", draft.get("summary", ""), "", "## Impact", draft.get("impact", ""), ""]
    det = draft.get("detection") or {}
    if det:
        md += ["## Detection", det.get("text", "") + _cite(det.get("sources", [])), ""]
    md += ["## Timeline", "", "| Time | Event | Sources |", "|---|---|---|"]
    for t in draft.get("timeline", []):
        event = str(t.get("event", "")).replace("|", "\\|")
        md.append(f"| {t.get('time') or '—'} | {event} | {', '.join(t['sources'])} |")
    rc = draft.get("root_cause") or {}
    md += ["", "## Root cause", rc.get("text", "") + _cite(rc.get("sources", [])), ""]
    for title, key in (("Contributing factors", "contributing_factors"), ("What went well", "what_went_well")):
        items = draft.get(key) or []
        if items:
            md += [f"## {title}"] + [f"- {i.get('text', '')}{_cite(i['sources'])}" for i in items] + [""]
    md += ["## Action items", "", "| Priority | Action | Owner | Sources |", "|---|---|---|---|"]
    for a in draft.get("action_items", []):
        md.append(f"| {a.get('priority', '')} | {a.get('action', '')} | {a.get('owner', '')} | {', '.join(a['sources'])} |")
    if needs_review:
        md += ["", "## Needs review (no supporting evidence found)"]
        md += [f"- *{n['section']}*: {n.get('text') or n.get('event') or n.get('action', '')}" for n in needs_review]
    if findings:
        md += ["", "## Blame-language warnings"]
        md += [f"- `{f['field']}`: {', '.join(f['phrases'] + f['names'])}" for f in findings]
    cited = {s for _, text in _text_fields(draft) for s in re.findall(r"[TLV]\d+", text or "")}
    for key in ("timeline", "contributing_factors", "what_went_well", "action_items"):
        for item in draft.get(key) or []:
            cited.update(item.get("sources", []))
    for key in ("root_cause", "detection"):
        cited.update((draft.get(key) or {}).get("sources", []))
    md += ["", "## Evidence"]
    for e in inp.evidence:
        if e.id in cited:
            who = f"{e.who}: " if e.who else ""
            md.append(f"- **{e.id}** ({e.source}{', ' + e.time if e.time else ''}) {who}{e.text}")
    return "\n".join(md) + "\n"
