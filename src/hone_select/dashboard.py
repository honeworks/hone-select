"""A local dashboard over a span store and a project's experiments (design changes 0008, 0009).

``list_runs``, ``run_detail`` and ``all_candidates`` turn the spans of a store into plain JSON-ready values;
``make_server`` / ``serve`` answer ``GET /`` (the page), ``/api/runs``, ``/api/runs/<run id>``,
``/api/candidates`` and the experiment endpoints of ``hone_select.experiments.web``. The store is only read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast

from hone_select._dashboard_data import all_candidates, list_runs, run_detail
from hone_select.errors import HoneSelectError
from hone_select.experiments import web
from hone_select.experiments.project import Project

__all__ = ["all_candidates", "list_runs", "make_server", "run_detail", "serve"]


def _page() -> bytes:
    return resources.files("hone_select").joinpath("dashboard.html").read_bytes()


LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")


def _routes(db: Path, project: Path | None) -> Callable[[str], tuple[int, str, bytes]]:
    def answer(path: str) -> tuple[int, str, bytes]:
        if path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", _page()
        if project is not None and (path.startswith(("/api/experiments", "/files/"))):
            return web.get(Project(project), path)
        try:
            if path == "/api/runs":
                data: Any = list_runs(db) if db.exists() else []
            elif path == "/api/candidates":
                data = all_candidates(db) if db.exists() else []
            elif path.startswith("/api/runs/"):
                data = run_detail(db, path.removeprefix("/api/runs/"))
            else:
                return 404, "application/json", b'{"error": "not found"}'
        except HoneSelectError as e:
            return 404, "application/json", json.dumps({"error": str(e)}).encode()
        return 200, "application/json", json.dumps(data, default=str).encode()

    return answer


def _handler(
    answer: Callable[[str], tuple[int, str, bytes]], root: Path | None, host: str
) -> type[BaseHTTPRequestHandler]:
    """The request handler: GET answers the routes, POST the experiment writes (design changes 0008, 0009)."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, reply: tuple[int, str, bytes]) -> None:
            status, kind, body = reply
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self) -> set[str] | None:
            """The Host values a loopback-bound server answers (a DNS-rebinding page sends its own host
            name and is refused); None, any Host, when bound to another address (design/decisions.md)."""
            if host not in LOOPBACK:
                return None
            port = cast(tuple[str, int], self.server.server_address)[1]
            return {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]", host)}

        def do_GET(self) -> None:
            if not web.host_ok(self.headers, self._allowed()):
                self._send((403, "application/json", b'{"error": "unknown Host"}'))
                return
            self._send(answer(self.path.split("?", 1)[0]))

        def do_POST(self) -> None:
            if root is None or not web.same_origin(self.headers, self._allowed()):
                self._send(
                    (403, "application/json", b'{"error": "writes come from the dashboard page only"}')
                )
                return
            length = int(self.headers.get("Content-Length") or 0)
            self._send(web.post(Project(root), self.path.split("?", 1)[0], self.rfile.read(length)))

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def make_server(
    db: str | Path, host: str = "127.0.0.1", port: int = 8788, project: str | Path | None = None
) -> ThreadingHTTPServer:
    """The dashboard server (not started): ``server.serve_forever()`` runs it; port 0 picks a free port.

    ``project`` (a folder with ``experiments/``) adds the Experiments pages (design change 0009); the span
    store may then be missing (no selection has run yet)."""
    store = Path(db)
    root = Path(project).resolve() if project is not None else None
    if not store.exists() and not (root is not None and web.has_experiments(root)):
        raise HoneSelectError(
            f"no span store at {str(store)!r} and no experiments; pass --db or --project, or set HONE_HOME"
        )
    handler = _handler(_routes(store, root), root, host)

    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        raise HoneSelectError(
            f"cannot listen on {host}:{port} ({e}); pass another --port (0 = any free)"
        ) from e


def serve(
    db: str | Path, host: str = "127.0.0.1", port: int = 8788, project: str | Path | None = None
) -> None:
    """Serve the dashboard until interrupted."""
    server = make_server(db, host, port, project)
    try:
        server.serve_forever()
    finally:
        server.server_close()
