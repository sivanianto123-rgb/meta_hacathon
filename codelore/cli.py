"""codelore command line: why / heatmap / postmortem / serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import heatmap, postmortem, why
from .gitdata import GitError, repo_root
from .llm import LLMError, LLMNotConfigured, MetaClient


def _model_or_none():
    try:
        return MetaClient.from_env()
    except LLMNotConfigured as e:
        print(f"note: {e}", file=sys.stderr)
        return None


def cmd_why(args) -> int:
    path, _, line = args.target.rpartition(":")
    if not path or not line.isdigit():
        print("target must look like path/to/file.py:42", file=sys.stderr)
        return 2
    repo = repo_root(args.repo)
    rel = str((Path(args.repo) / path).resolve().relative_to(repo.resolve()))
    result = why.explain(str(repo), rel, int(line), _model_or_none(), fetch_prs=not args.offline)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{rel}:{line}")
    print("    " + result["code"][int(line) - result["code_start"]].strip())
    if result["summary"]:
        print(f"\n{result['summary']}\n\n{result['story']}")
    if result["error"]:
        print(f"\n({result['error']})")
    print("\nEvidence:")
    for e in result["evidence"]:
        print(f"  [{e['id']}] {e['date']} {e['author']}: {e['title']}" + (f"  {e['url']}" if e["url"] else ""))
    return 0


def cmd_heatmap(args) -> int:
    repo = repo_root(args.repo)
    files = heatmap.analyze(str(repo), since=args.since)
    if args.notes:
        model = _model_or_none()
        if model:
            err = heatmap.add_notes(files, model)
            if err:
                print(f"note: {err}", file=sys.stderr)
    if args.json:
        print(json.dumps(heatmap.to_json(files), indent=2))
        return 0
    print(f"{'risk':>5}  {'commits':>7}  {'fixes':>5}  {'bus':>3}  {'top author':<24} path")
    for f in files[: args.top]:
        owner = f"{f.top_author[:16]} {f.top_share:.0%}"
        print(f"{f.risk:5.1f}  {f.commits:7d}  {f.fix_commits:5d}  {f.bus_factor:3d}  {owner:<24} {f.path}")
        for flag in f.flags:
            print(f"{'':42}! {flag}")
        if f.note:
            print(f"{'':42}{f.note}")
    return 0


def cmd_postmortem(args) -> int:
    model = MetaClient.from_env()
    thread = Path(args.thread).read_text(errors="replace") if args.thread else ""
    logs = "\n".join(Path(p).read_text(errors="replace") for p in args.logs)
    transcript = Path(args.transcript).read_text(errors="replace") if args.transcript else ""
    if args.audio:
        transcript = (transcript + "\n" + model.transcribe(args.audio)).strip()
    result = postmortem.write(postmortem.build_inputs(thread, logs, transcript), model)
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.out:
        Path(args.out).write_text(result["markdown"])
        print(f"wrote {args.out}")
        if result["needs_review"]:
            print(f"{len(result['needs_review'])} item(s) had no supporting evidence and need review")
    else:
        print(result["markdown"])
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    serve(repo_root(args.repo), args.host, args.port, args.since, fetch_prs=not args.offline)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="codelore", description="Code history you can ask questions of.")
    p.add_argument("--repo", default=".", help="path inside the git repository (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("why", help="explain why a line exists")
    w.add_argument("target", help="path/to/file:LINE")
    w.add_argument("--json", action="store_true")
    w.add_argument("--offline", action="store_true", help="skip GitHub PR lookups")
    w.set_defaults(fn=cmd_why)

    h = sub.add_parser("heatmap", help="rank files by risk and bus factor")
    h.add_argument("--since", default="12 months ago")
    h.add_argument("--top", type=int, default=25)
    h.add_argument("--notes", action="store_true", help="add model-written notes for the riskiest files")
    h.add_argument("--json", action="store_true")
    h.set_defaults(fn=cmd_heatmap)

    m = sub.add_parser("postmortem", help="write a blameless postmortem from an incident thread and logs")
    m.add_argument("--thread", help="Slack export JSON or 'name: message' text")
    m.add_argument("--logs", nargs="*", default=[], help="log files")
    m.add_argument("--transcript", help="call transcript text file")
    m.add_argument("--audio", help="call recording to transcribe with Muse Voice Transcribe")
    m.add_argument("--out", help="write markdown here")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_postmortem)

    s = sub.add_parser("serve", help="run the API for the VS Code extension and dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--since", default="12 months ago")
    s.add_argument("--offline", action="store_true", help="skip GitHub PR lookups")
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (GitError, LLMNotConfigured, LLMError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
