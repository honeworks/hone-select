"""AC-29: a person rates outputs blind (fixed random order, no setup shown) and the ratings join the results;
the dashboard lists experiments, shows one, approves or denies it, takes ratings and serves its files,
and refuses writes that do not come from its own page and paths outside the experiment."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.dashboard import make_server
from hone_select.experiments import definition as d
from hone_select.experiments import ratings, report, start

from .experiment_helpers import PYTHON_EXPERIMENT, approved, project

pytestmark = pytest.mark.e2e

HUMAN = PYTHON_EXPERIMENT.replace('scorers = ["length"]', 'scorers = ["length", "keep_reading"]') + (
    '[scorers.keep_reading]\nkind = "human"\nquestion = "Would you keep reading?"\nscale = [1, 5]\nmin_ratings = 2\n'
)


def test_ac29_blind_ratings_join_the_results(tmp_path: Path) -> None:
    p, folder = approved(tmp_path, HUMAN)
    start(p, "E0001")
    spec = d.load(folder)
    first = ratings.next_output(folder, spec, "keep_reading")
    assert first is not None
    again = ratings.next_output(folder, spec, "keep_reading")
    assert again is not None
    assert "setup" not in first
    assert "params" not in first
    assert first["total"] == 24
    assert again["sample_id"] == first["sample_id"]  # a fixed order
    for _ in range(24):
        o = ratings.next_output(folder, spec, "keep_reading")
        assert o is not None
        value = 5 if "large" in o["sample_id"] else 1
        ratings.add(folder, spec, o["sample_id"], "keep_reading", value)
    assert ratings.next_output(folder, spec, "keep_reading") is None
    res = report(folder, spec, json.loads((folder / "plan.json").read_text()))
    large = res["factors"]["model"]["large"]["human"]["keep_reading"]
    small = res["factors"]["model"]["small"]["human"]["keep_reading"]
    assert large == {"mean": 1.0, "rated": 8, "complete": True}
    assert small["mean"] == 0.0


def test_ac29_bad_ratings_are_refused(tmp_path: Path) -> None:
    p, folder = approved(tmp_path, HUMAN)
    start(p, "E0001")
    spec = d.load(folder)
    first = ratings.next_output(folder, spec, "keep_reading")
    assert first is not None
    sid = first["sample_id"]
    with pytest.raises(ConfigError, match="between 1 and 5"):
        ratings.add(folder, spec, sid, "keep_reading", 9)
    with pytest.raises(ConfigError, match="not a human criterion"):
        ratings.add(folder, spec, sid, "length", 3)
    with pytest.raises(ConfigError, match="no rateable output"):
        ratings.add(folder, spec, "nope", "keep_reading", 3)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    p, _ = approved(tmp_path, HUMAN)
    start(p, "E0001")
    second = p.new("Waiting")
    (second / "experiment.toml").write_text(
        PYTHON_EXPERIMENT.replace('title = "Writers"', 'title = "Waiting"')
    )
    (second / "cases" / "cases.toml").write_text('[[case]]\nid = "keeper"\ntopic = "the keeper"\n')
    p.plan("E0002")
    srv = make_server(tmp_path / "no-store.db", port=0, project=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", tmp_path
    srv.shutdown()
    srv.server_close()


def call(
    url: str, body: dict | None = None, headers: dict | None = None, raw: bool = False
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - a local test server
        url, data=data, headers=headers or {}, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as r:  # noqa: S310 - a local test server
            payload = r.read()
            return r.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


PAGE = {"X-Hone-Dashboard": "1", "Content-Type": "application/json"}


def test_ac29_dashboard_lists_shows_and_reviews(served: tuple[str, Path]) -> None:
    base, _ = served
    status, rows = call(base + "/api/experiments")
    assert status == 200
    assert [(r["eid"], r["status"]) for r in rows] == [("E0001", "completed"), ("E0002", "proposed")]
    status, x = call(base + "/api/experiments/E0001")
    assert x["plan"]["outputs"] == 24
    assert len(x["samples"]) == 24
    assert x["results"]["best"] == d.setup_id({"model": "large", "temperature": 1.0})
    assert "keep_reading" in x["human"]
    assert call(base + "/api/experiments/E0002/review", {"decision": "approve"})[0] == 403  # no page header
    evil = {**PAGE, "Origin": "http://evil.example"}
    assert call(base + "/api/experiments/E0002/review", {"decision": "approve"}, evil)[0] == 403
    status, body = call(base + "/api/experiments/E0002/review", {"decision": "deny"}, PAGE)
    assert (status, body["error"]) == (400, "a denial needs a note that says why")
    status, body = call(base + "/api/experiments/E0002/review", {"decision": "approve", "note": "go"}, PAGE)
    assert (status, body["decision"]) == (200, "approved")
    assert call(base + "/api/experiments/E0002")[1]["status"] == "approved"
    status, body = call(base + "/api/experiments/E0002/review", {"decision": "approve"}, PAGE)
    assert status == 400  # only a proposed plan can be reviewed
    assert call(base + "/api/experiments/E0099")[0] == 404


def test_ac29_dashboard_rates_and_serves_files(served: tuple[str, Path]) -> None:
    base, root = served
    status, o = call(base + "/api/experiments/E0001/rate/keep_reading")
    assert status == 200
    status, _ = call(
        base + "/api/experiments/E0001/rate",
        {"sample_id": o["sample_id"], "criterion": "keep_reading", "value": 4},
        PAGE,
    )
    assert status == 200
    assert call(base + "/api/experiments/E0001/rate/keep_reading")[1]["sample_id"] != o["sample_id"]
    status, _ = call(
        base + "/api/experiments/E0001/rate",
        {"sample_id": o["sample_id"], "criterion": "keep_reading", "value": 0},
        PAGE,
    )
    assert status == 400
    status, content = call(f"{base}/files/E0001/{o['file_base']}/story.txt", raw=True)
    assert status == 200
    assert content.startswith(b"the ")
    for bad in (
        "/files/E0001/experiment.toml",
        "/files/E0001/../../subjects.py",
        "/files/E0001/outputs/nope.txt",
    ):
        req = urllib.request.Request(base + bad)  # noqa: S310 - a local test server
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 - a local test server
        assert err.value.code == 404
    assert (root / "experiments" / "E0001-writers" / "ratings.jsonl").read_text().count("\n") == 1


def test_ac29_dashboard_needs_a_store_or_experiments(tmp_path: Path) -> None:
    from hone_select import HoneSelectError

    with pytest.raises(HoneSelectError, match="no span store"):
        make_server(tmp_path / "none.db", port=0, project=tmp_path)
    project(tmp_path)
    make_server(tmp_path / "none.db", port=0, project=tmp_path).server_close()  # experiments are enough
