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


SECRET_CONFIG = """
[judges.remote]
client = "never.loaded:factory"
api_key = "planted-api-key-value"
auth_token = "planted-token-value"
[generate]
n = 2
[scorers.clear]
kind = "prompt"
judge = "local"
criteria = "PLANTED-CRITERIA the chorus is clear"
[score]
cascade = [{ scorers = ["clear"] }]
"""  # noqa: S105 - planted fake secrets that the tests prove are never recorded


@pytest.mark.parametrize("capture", ["1", "0"])
def test_ac24_secrets_never_recorded_and_prompt_text_is_content(
    capture: str, hone_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hone_select.testing import FakeDecisionClient

    monkeypatch.setenv("HONE_CAPTURE_CONTENT", capture)
    engine = Engine(SECRET_CONFIG, registry=[write], judges={"local": FakeDecisionClient()})
    run_id = engine.run("a song").run_id
    raw = store(hone_home).read_bytes() + b"".join(
        p.read_bytes() for p in store(hone_home).parent.glob("*-wal")
    )
    assert b"planted-api-key-value" not in raw
    assert b"planted-token-value" not in raw
    config = run_detail(store(hone_home), run_id)["config"]
    assert config["judges"]["remote"]["api_key"] == "***"
    assert config["judges"]["remote"]["auth_token"] == "***"  # noqa: S105 - the redaction marker
    assert config["generate"]["n"] == 2  # structure stays readable
    criteria = config["scorers"]["clear"]["criteria"]
    if capture == "1":
        assert criteria.startswith("PLANTED-CRITERIA")
    else:
        assert set(criteria) == {"sha256", "len"}
        assert b"PLANTED-CRITERIA" not in raw


def test_ac24_secrets_are_not_served(hone_home: Path) -> None:
    from hone_select.testing import FakeDecisionClient

    Engine(SECRET_CONFIG, registry=[write], judges={"local": FakeDecisionClient()}).run("a song")
    srv = make_server(store(hone_home), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        run_id = json.loads(fetch(base + "/api/runs")[2])[0]["run_id"]
        bodies = b"".join(fetch(base + p)[2] for p in ("/api/runs", f"/api/runs/{run_id}", "/api/candidates"))
    finally:
        srv.shutdown()
        srv.server_close()
    assert b"planted-api-key-value" not in bodies
    assert b"planted-token-value" not in bodies


@generator()
def mixed(task, v):
    return ["", "same", "same", "fine answer"][v["index"]]  # empty (gated), a duplicate, a normal one


@gate()
def not_blank(c):
    return bool(c.data)


@scorer()
def picky(c):
    if c.data == "same":
        raise ValueError("cannot judge 'same'")
    return 0.8


def test_ac24_failures_show_as_they_happened(hone_home: Path) -> None:
    cfg = '[generate]\nn = 4\n[score]\ngates = ["not_blank"]\ncascade = [{ scorers = ["picky"] }]\n'
    run_id = Engine(cfg, registry=[mixed, not_blank, picky]).run("t").run_id
    d = run_detail(store(hone_home), run_id)
    by_data = {c["preview"]: c for c in d["candidates"]}
    assert len(d["candidates"]) == 3  # the duplicate "same" is one candidate
    assert any(e["event"] == "dedup" for e in d["decision_trace"])
    blank = by_data[""]
    assert blank["gates"]["not_blank"]["passed"] is False
    assert blank["rejected"] is True
    same = by_data["same"]
    assert same["scores"]["picky"]["value"] is None  # a failed score is None, never 0
    assert "cannot judge" in same["scores"]["picky"]["error"]
    assert same["total"] is None
    assert by_data["fine answer"]["winner"] is True


@generator()
def broken(task, v):
    raise RuntimeError("the model is down")


def test_ac24_a_run_without_candidates(hone_home: Path) -> None:
    run_id = Engine("[generate]\nn = 2\n", registry=[broken]).run("t").run_id
    summary = next(r for r in list_runs(store(hone_home)) if r["run_id"] == run_id)
    assert summary["winner"] is None
    assert summary["candidate_count"] == 0
    d = run_detail(store(hone_home), run_id)
    assert d["candidates"] == []
    assert any(e["event"] == "generate_error" for e in d["decision_trace"])


def test_ac24_query_strings_and_error_bodies(server: str, two_runs: list[str]) -> None:
    status, _, body = fetch(server + "/api/runs?refresh=1")
    assert status == 200
    assert len(json.loads(body)) == 2
    status, kind, body = fetch(server + "/api/runs/nope")
    assert status == 404
    assert kind == "application/json"
    assert "no run 'nope'" in json.loads(body)["error"]
    assert json.loads(fetch(server + "/nothing")[2]) == {"error": "not found"}
