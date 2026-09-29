"""AC-24: the dashboard lists runs, shows a run's configuration, task and candidate table (variation params,
gates, scores, winner), compares candidates across runs, and serves it all read-only over HTTP."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hone_select import Candidate, Engine, HoneSelectError, gate, generator, scorer
from hone_select.cli import app
from hone_select.dashboard import all_candidates, list_runs, make_server, run_detail

pytestmark = pytest.mark.e2e

CONFIG = """
[generate]
n = 4
vary = { model = ["small", "large"], temperature = [0.5, 1.0] }
[score]
gates = ["not_empty"]
cascade = [{ scorers = ["longer"] }]
[select]
policy = "argmax"
"""


@generator()
def write(task, v):
    p = v["params"]
    text = f"{task} by {p['model']} at {p['temperature']} #{v['index']}"
    return Candidate.of(text + "!" * (p["model"] == "large"))


@gate()
def not_empty(c):
    return bool(c.data)


@scorer()
def longer(c):
    return min(1.0, len(c.data) / 40)


def store(hone_home: Path) -> Path:
    return hone_home / "select" / "spans.db"


@pytest.fixture
def two_runs(hone_home: Path) -> list[str]:
    engine = Engine(CONFIG, registry=[write, not_empty, longer])
    trace = {
        "traceparent": f"00-{'4b' * 16}-{'00f067aa' * 2}-01",
        "hone.run_id": "exp-1",
        "hone.item": "song-a",
        "hone.step": "premise",
    }
    first = engine.run("a song", trace=trace).run_id  # both runs in one trace: they must stay separate
    second = engine.run("another song", trace=trace).run_id
    return [first, second]


def test_ac24_runs_are_listed_newest_first_with_context(two_runs: list[str], hone_home: Path) -> None:
    runs = list_runs(store(hone_home))
    assert [r["run_id"] for r in runs] == two_runs[::-1]  # two runs in one trace stay separate
    r = runs[0]
    assert r["policy"] == "argmax"
    assert r["n"] == 4
    assert r["candidate_count"] == 4
    assert r["winner"] is not None
    assert r["winner_total"] is not None
    assert r["context"] == {"hone.run_id": "exp-1", "hone.item": "song-a", "hone.step": "premise"}


def test_ac24_a_run_shows_what_was_tested_and_every_candidate(two_runs: list[str], hone_home: Path) -> None:
    d = run_detail(store(hone_home), two_runs[0])
    assert d["candidate_count"] == 4  # the count and the table are different keys
    assert d["config"]["generate"]["vary"]["model"] == ["small", "large"]
    assert d["task"] == "a song"
    assert d["columns"] == {
        "params": ["model", "temperature", "seed", "index"],
        "gates": ["not_empty"],
        "scores": ["longer"],
    }
    cands = d["candidates"]
    assert len(cands) == 4
    assert [c["rank"] for c in cands] == [1, 2, 3, 4]
    assert cands[0]["winner"]
    assert cands[0]["params"]["model"] == "large"
    assert cands[0]["gates"]["not_empty"]["passed"] is True
    assert cands[0]["scores"]["longer"]["value"] == cands[0]["total"]
    assert cands[0]["preview"].startswith("a song by large")
    assert any(e["event"] == "selected" for e in d["decision_trace"])


def test_ac24_candidates_across_runs_carry_params_for_comparison(
    two_runs: list[str], hone_home: Path
) -> None:
    rows = all_candidates(store(hone_home))
    assert len(rows) == 8
    winners = [r for r in rows if r["winner"]]
    assert {w["params"]["model"] for w in winners} == {"large"}
    assert all("longer" in r["scores"] for r in rows)


def test_ac24_task_is_hashed_when_content_capture_is_off(
    hone_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HONE_CAPTURE_CONTENT", "0")
    run_id = Engine(CONFIG, registry=[write, not_empty, longer]).run("secret task").run_id
    task = run_detail(store(hone_home), run_id)["task"]
    assert set(task) == {"sha256", "len"}


def test_ac24_unknown_run(two_runs: list[str], hone_home: Path) -> None:
    with pytest.raises(HoneSelectError, match="no run 'nope'"):
        run_detail(store(hone_home), "nope")


@pytest.fixture
def server(two_runs: list[str], hone_home: Path) -> Iterator[str]:
    srv = make_server(store(hone_home), port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def fetch(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - a local test server
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_ac24_server_serves_the_page_and_the_api(server: str, two_runs: list[str]) -> None:
    status, kind, body = fetch(server + "/")
    assert status == 200
    assert kind.startswith("text/html")
    assert b"hone-select" in body
    status, _, body = fetch(server + "/api/runs")
    assert [r["run_id"] for r in json.loads(body)] == two_runs[::-1]
    status, _, body = fetch(f"{server}/api/runs/{two_runs[0]}")
    assert json.loads(body)["task"] == "a song"
    assert len(json.loads(fetch(server + "/api/candidates")[2])) == 8
    assert fetch(server + "/api/runs/nope")[0] == 404
    assert fetch(server + "/nothing")[0] == 404


def test_ac24_cli_refuses_a_missing_store(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["dashboard", "--db", str(tmp_path / "none.db")])
    assert result.exit_code != 0
    assert "no span store" in str(result.exception)


def test_ac24_make_server_refuses_a_missing_store(tmp_path: Path) -> None:
    with pytest.raises(HoneSelectError, match="no span store"):
        make_server(tmp_path / "none.db", port=0)


def test_ac24_a_busy_port_is_a_clear_error(server: str, hone_home: Path) -> None:
    port = int(server.rsplit(":", 1)[1])
    with pytest.raises(HoneSelectError, match="cannot listen on 127.0.0.1"):
        make_server(store(hone_home), port=port)
