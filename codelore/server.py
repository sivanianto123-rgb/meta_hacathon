"""Local HTTP server for the VS Code extension and the web dashboard (stdlib only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import heatmap, postmortem, why
from .gitdata import GitError
from .llm import LLMError, LLMNotConfigured, MetaClient

STATIC = Path(__file__).parent / "static"
MAX_BODY = 5 * 1024 * 1024


def _model():
    try:
        return MetaClient.from_env()
    except LLMNotConfigured:
        return None


class App:
    def __init__(self, repo: Path, since: str, fetch_prs: bool = True):
        self.repo = repo
        self.since = since
        self.fetch_prs = fetch_prs
        self._heat_cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def heatmap(self, refresh: bool, notes: bool, kinds: tuple[str, ...]) -> dict:
        unknown = set(kinds) - set(heatmap.KINDS)
        if unknown:
            raise ValueError(f"unknown file kinds: {', '.join(sorted(unknown))}")
        with self._lock:
            if kinds not in self._heat_cache or refresh:
                files = heatmap.analyze(str(self.repo), since=self.since, kinds=kinds)
                error = None
                model = _model() if notes else None
                if notes:
                    error = heatmap.add_notes(files, model) if model else "Model not configured; notes skipped."
                self._heat_cache[kinds] = {
                    "repo": self.repo.name,
                    "since": self.since,
                    "files": heatmap.to_json(files),
                    "notes_error": error,
                }
            return self._heat_cache[kinds]

    def why(self, path: str, line: int) -> dict:
        resolved = (self.repo / path).resolve()
        if self.repo.resolve() not in resolved.parents:
            raise ValueError("path must be inside the repository")
        return why.explain(str(self.repo), path, line, _model(), fetch_prs=self.fetch_prs)

    def postmortem(self, body: dict) -> dict:
        model = _model()
        if model is None:
            raise LLMNotConfigured("Set META_API_KEY, META_API_BASE and META_MODEL to write postmortems")
        transcript = body.get("transcript", "")
        inp = postmortem.build_inputs(body.get("thread", ""), body.get("logs", ""), transcript)
        return postmortem.write(inp, model)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            pass

        def _send(self, status: int, payload, content_type="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, fn):
            try:
                self._send(200, fn())
            except KeyError as e:
                self._send(400, {"error": f"missing parameter {e}"})
            except (ValueError, GitError, FileNotFoundError) as e:
                self._send(400, {"error": str(e)})
            except LLMNotConfigured as e:
                self._send(503, {"error": str(e)})
            except LLMError as e:
                self._send(502, {"error": str(e)})

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/why":
                self._handle(lambda: app.why(q["path"], int(q["line"])))
            elif url.path == "/api/heatmap":
                self._handle(lambda: app.heatmap(
                    q.get("refresh") == "1", q.get("notes") == "1", tuple(q.get("kinds", "code").split(","))
                ))
            elif url.path == "/api/health":
                self._send(200, {"repo": str(app.repo), "model": _model() is not None})
            elif url.path.startswith("/static/vendor/") and url.path.endswith(".js"):
                f = STATIC / "vendor" / Path(url.path).name  # .name blocks ../ traversal
                if f.is_file():
                    self._send(200, f.read_bytes(), "text/javascript; charset=utf-8")
                else:
                    self._send(404, {"error": "not found"})
            elif url.path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/postmortem":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "body must be JSON"})
            self._handle(lambda: app.postmortem(body))

    return Handler


def serve(repo: Path, host: str, port: int, since: str, fetch_prs: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(App(repo, since, fetch_prs)))
    print(f"codelore serving {repo} on http://{host}:{port}")
    server.serve_forever()
