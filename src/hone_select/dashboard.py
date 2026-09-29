"""A read-only dashboard over a span store (design change 0008): the data it shows, and a small server.

``list_runs``, ``run_detail`` and ``all_candidates`` turn the spans of a store into plain JSON-ready values;
``serve`` answers ``GET /`` (the page), ``/api/runs``, ``/api/runs/<run id>`` and ``/api/candidates``.
The store is only read, never written.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast

from hone_select._records import read_spans
from hone_select.errors import HoneSelectError
from hone_select.experiments import web
from hone_select.experiments.project import Project

__all__ = ["all_candidates", "list_runs", "make_server", "run_detail", "serve"]

RUN = "hone.select.run"
CONTEXT_KEYS = ("hone.run_id", "hone.item", "hone.step")
Spans = list[dict[str, Any]]


def _by_run(spans: Spans) -> dict[str, Spans]:
    """Spans grouped under their nearest ``hone.select.run`` ancestor (a trace may hold several runs)."""
    parents = {s["span_id"]: s for s in spans}
    owner: dict[str, str | None] = {}

    def run_of(span: dict[str, Any]) -> str | None:
        seen: list[str] = []
        node: dict[str, Any] | None = span
        while node is not None and node["span_id"] not in owner:
            if node["name"] == RUN:
                owner[node["span_id"]] = node["span_id"]
                break
            seen.append(node["span_id"])
            node = parents.get(node.get("parent_span_id") or "")
        found = owner.get(node["span_id"]) if node is not None else None
        for span_id in seen:
            owner[span_id] = found
        return found

    grouped: dict[str, Spans] = {}
    for s in spans:
        run_id = run_of(s)
        if run_id is not None:
            grouped.setdefault(run_id, []).append(s)
    return grouped


def _decision(spans: Spans) -> dict[str, Any]:
    """The attributes of the run's decision span (empty while a run has not decided)."""
    for s in spans:
        if s["name"] == "hone.select.decision":
            return cast(dict[str, Any], s["attributes"])
    return {}


def _ranked(decision: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], decision.get("hone.select.ranked") or [])


def _row(cid: str, **known: Any) -> dict[str, Any]:
    empty = {"rank": None, "total": None, "rejected": None, "stage_reached": None, "winner": False}
    return {"id": cid, **empty, **known, "params": {}, "meta": {}, "gates": {}, "scores": {}}


def _generated(spans: Spans) -> set[str]:
    """Ids of the candidates generated (failed generations have no candidate id)."""
    return {
        s["attributes"]["hone.candidate_id"]
        for s in spans
        if s["name"] == "hone.select.generate" and "hone.candidate_id" in s["attributes"]
    }


def _summary(run_id: str, spans: Spans) -> dict[str, Any]:
    root = next(s for s in spans if s["span_id"] == run_id)
    attrs = root["attributes"]
    decision = _decision(spans)
    ranked = _ranked(decision)
    winner = decision.get("hone.select.winner_id")
    total = next((r["total"] for r in ranked if r["id"] == winner), None)
    return {
        "run_id": run_id,
        "trace_id": root["trace_id"],
        "start": root["start_time"],
        "end": root["end_time"],
        "status": root["status"]["code"],
        "policy": attrs.get("hone.select.policy"),
        "n": attrs.get("hone.select.n"),
        "candidate_count": max(len(ranked), len(_generated(spans))),
        "rejected": sum(bool(r["rejected"]) for r in ranked),
        "winner": winner,
        "winner_total": total,
        "fallback": decision.get("hone.select.fallback_used"),
        "escalated": decision.get("hone.select.escalated"),
        "config_hash": attrs.get("hone.select.config_hash"),
        "context": {k: attrs[k] for k in CONTEXT_KEYS if k in attrs},
    }


def list_runs(db: str | Path) -> list[dict[str, Any]]:
    """Every run in the store, newest first."""
    runs = [_summary(run_id, spans) for run_id, spans in _by_run(read_spans(db)).items()]
    return sorted(runs, key=lambda r: r["start"], reverse=True)


def _candidates(spans: Spans) -> list[dict[str, Any]]:
    """The candidate table: ranked order first, then candidates that never reached the ranking (dedup)."""
    decision = _decision(spans)
    winner = decision.get("hone.select.winner_id")
    rows: dict[str, dict[str, Any]] = {}
    for place, r in enumerate(_ranked(decision), start=1):
        rows[r["id"]] = _row(
            r["id"],
            rank=place,
            total=r["total"],
            rejected=r["rejected"],
            stage_reached=r["stage_reached"],
            winner=r["id"] == winner,
        )
    for s in spans:
        _add(rows, s)
    return list(rows.values())


def _add(rows: dict[str, dict[str, Any]], span: dict[str, Any]) -> None:
    attrs = span["attributes"]
    cid = attrs.get("hone.candidate_id")
    if cid is None:
        return
    row = rows.setdefault(cid, _row(cid))
    if span["name"] == "hone.select.generate":
        record = cast(dict[str, Any], attrs.get("hone.select.candidate") or {})
        meta = cast(dict[str, Any], record.get("meta")) if isinstance(record.get("meta"), dict) else {}
        row["meta"] = meta
        row["params"] = dict(meta.get("params") or {}) | {k: meta[k] for k in ("seed", "index") if k in meta}
        row["preview"] = record.get("data_preview")
    elif span["name"] == "hone.select.gate":
        row["gates"][attrs.get("hone.select.gate")] = {
            "passed": attrs.get("hone.select.gate.passed"),
            "probability": attrs.get("hone.select.gate.probability"),
            "details": attrs.get("hone.select.gate.details"),
            "error": span["status"]["message"] or None,
        }
    elif span["name"] == "hone.select.score":
        row["scores"][attrs.get("hone.select.scorer")] = {
            "value": attrs.get("hone.select.score.value"),
            "confidence": attrs.get("hone.select.score.confidence"),
            "reason": attrs.get("hone.select.score.reason"),
            "error": attrs.get("hone.select.score.error"),
            "cache_hit": attrs.get("hone.select.cache_hit"),
        }


def run_detail(db: str | Path, run_id: str) -> dict[str, Any]:
    """One run: summary, configuration, task, budget, candidates, pairwise judgements and decision trace."""
    spans = _by_run(read_spans(db)).get(run_id)
    if spans is None:
        raise HoneSelectError(f"no run {run_id!r} in {str(db)!r}")
    root = next(s for s in spans if s["span_id"] == run_id)["attributes"]
    decision = _decision(spans)
    candidates = _candidates(spans)
    pairwise = [
        {
            "a": s["attributes"].get("hone.select.pairwise.a"),
            "b": s["attributes"].get("hone.select.pairwise.b"),
            "choice": s["attributes"].get("hone.select.pairwise.choice"),
            "judge": s["attributes"].get("hone.scorer"),
        }
        for s in spans
        if s["name"] == "hone.select.pairwise"
    ]
    return _summary(run_id, spans) | {
        "config": root.get("hone.select.config"),
        "task": root.get("hone.select.task"),
        "budget": {k.rsplit(".", 1)[-1]: v for k, v in root.items() if k.startswith("hone.select.budget.")},
        "columns": {
            "params": _keys(c["params"] for c in candidates),
            "gates": _keys(c["gates"] for c in candidates),
            "scores": _keys(c["scores"] for c in candidates),
        },
        "candidates": candidates,
        "pairwise": pairwise,
        "decision_trace": decision.get("hone.select.decision_trace") or [],
        "errors": [
            {"span": s["name"], "message": s["status"]["message"]}
            for s in spans
            if s["status"]["code"] == "error"
        ],
    }


def all_candidates(db: str | Path) -> list[dict[str, Any]]:
    """Every candidate of every run, with its run's time and context: for comparing setups across runs."""
    out: list[dict[str, Any]] = []
    for run_id, spans in _by_run(read_spans(db)).items():
        summary = _summary(run_id, spans)
        for c in _candidates(spans):
            scores = {name: s["value"] for name, s in c["scores"].items()}
            out.append(
                {
                    "run_id": run_id,
                    "start": summary["start"],
                    "context": summary["context"],
                    "id": c["id"],
                    "rank": c["rank"],
                    "total": c["total"],
                    "winner": c["winner"],
                    "rejected": c["rejected"],
                    "params": c["params"],
                    "scores": scores,
                }
            )
    return sorted(out, key=lambda c: (c["start"], c["rank"] or 0), reverse=True)


def _keys(dicts: Any) -> list[str]:
    seen: dict[str, None] = {}
    for d in dicts:
        seen |= dict.fromkeys(str(k) for k in d)
    return list(seen)


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
