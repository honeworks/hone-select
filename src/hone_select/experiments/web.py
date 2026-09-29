"""The dashboard's experiment endpoints (design change 0009 §5).

    GET  /api/experiments                         every experiment with its status
    GET  /api/experiments/<eid>                   definition, plan, reviews, samples, results
    POST /api/experiments/<eid>/review            {"decision": "approved" | "denied", "note": ...}
    GET  /api/experiments/<eid>/rate/<criterion>  the next output to rate (blind to the setup)
    POST /api/experiments/<eid>/rate              {"sample_id", "criterion", "value"}
    GET  /files/<eid>/<path>                      a file under the experiment's cases/ or outputs/

The only writes are reviews and ratings, in the experiment folder.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from hone_select.errors import HoneSelectError
from hone_select.experiments import definition as d
from hone_select.experiments import ratings, results
from hone_select.experiments.project import Project, read_json

Reply = tuple[int, str, bytes]
JSON = "application/json"


def _json(value: Any, status: int = 200) -> Reply:
    return status, JSON, json.dumps(value, default=str).encode()


def _error(message: str, status: int = 400) -> Reply:
    return _json({"error": message}, status)


def detail(project: Project, eid: str) -> dict[str, Any]:
    folder = project.path(eid)
    spec = d.load(folder)
    plan = read_json(folder / "plan.json")
    return {
        **project.status(eid),
        "definition": (folder / d.DEFINITION).read_text(),
        "factors": spec.factors,
        "criteria": spec.criteria.model_dump(),
        "human": {n: h.model_dump() for n, h in spec.human_scorers().items()},
        "plan": plan,
        "reviews": read_json(folder / "review.json", []),
        "samples": results.rows(folder, spec),
        "results": read_json(folder / "results" / "results.json"),
    }


def get(project: Project, path: str) -> Reply:
    parts = [p for p in path.split("/") if p]
    try:
        if parts == ["api", "experiments"]:
            return _json(project.list())
        if len(parts) == 3 and parts[:2] == ["api", "experiments"]:
            return _json(detail(project, parts[2]))
        if len(parts) == 5 and parts[3] == "rate":
            folder = project.path(parts[2])
            return _json(ratings.next_output(folder, d.load(folder), parts[4]))
        if parts[:1] == ["files"] and len(parts) >= 3:
            return _file(project, parts[1], "/".join(parts[2:]))
    except HoneSelectError as e:
        return _error(str(e), 404)
    return _error("not found", 404)


def _file(project: Project, eid: str, relative: str) -> Reply:
    folder = project.path(eid).resolve()
    target = (folder / relative).resolve()
    allowed = [folder / "outputs", folder / "cases"]
    if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
        return _error("not found", 404)
    kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return 200, kind, target.read_bytes()


def post(project: Project, path: str, body: bytes) -> Reply:
    parts = [p for p in path.split("/") if p]
    if len(parts) != 4 or parts[:2] != ["api", "experiments"] or parts[3] not in ("review", "rate"):
        return _error("not found", 404)
    try:
        data = json.loads(body or b"{}")
        if parts[3] == "review":
            decision = {"approve": "approved", "deny": "denied"}.get(
                data.get("decision"), data.get("decision")
            )
            if decision == "denied" and not str(data.get("note", "")).strip():
                return _error("a denial needs a note that says why")
            return _json(project.review(parts[2], str(decision), str(data.get("note", "")), data.get("by")))
        folder = project.path(parts[2])
        ratings.add(
            folder,
            d.load(folder),
            str(data["sample_id"]),
            str(data["criterion"]),
            int(data["value"]),
            by=str(data.get("by", "owner")),
        )
        return _json({"ok": True})
    except (HoneSelectError, KeyError, TypeError, ValueError) as e:
        return _error(str(e))


def same_origin(headers: Any, host: str) -> bool:
    """A write must come from the dashboard page itself: its custom header (which a cross-site form cannot
    send without a preflight this server never allows) and, when present, a matching Origin."""
    if headers.get("X-Hone-Dashboard") != "1":
        return False
    origin = headers.get("Origin")
    return origin is None or origin in (f"http://{host}", f"https://{host}")


def has_experiments(root: Path) -> bool:
    return bool(Project(root).eids())
