"""AC-13: spans land in the SQLite store with the span names of design/current.md §8.3, follow an incoming trace, and hold
only hashes when content capture is off."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hone_select import (
    Candidate,
    ConfigError,
    Engine,
    PromptScorer,
    current_trace,
    gate,
    generator,
    pairwise,
    scorer,
)
from hone_select._records import read_spans
from hone_select.testing import FakeDecisionClient

pytestmark = pytest.mark.e2e

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"
FAKE_KEY = "sk-test" + "A1b2C3d4E5f6G7h8"


@generator()
def write(task, v):
    text = f"{task} draft {v['index']}"
    return Candidate.of({"lyrics": text}, meta={"model": "gemma-7b", "raw": f"key={FAKE_KEY}"})


@gate()
def short(c):
    return len(c.data["lyrics"]) < 40


@scorer()
def flat(c):
    return 0.5  # every candidate ties, so the tie escalates to pairwise


@pairwise()
def prefer_first(a, b):
    return "a"


CONFIG = """
[generate]
n = 2
[score]
gates = ["short"]
cascade = [{ scorers = ["flat", "judged"] }]
[select]
escalate = "pairwise"
pairwise = "prefer_first"
"""


def engine(config: str = CONFIG) -> Engine:
    judged = PromptScorer("judged", "Is it catchy?", FakeDecisionClient(answers={"judged": 0.5}))
    return Engine(config, registry=[write, short, flat, prefer_first, judged])


def read(db: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM spans")]
    con.close()
    for row in rows:
        row["attributes"] = json.loads(row["attributes"])
    return rows


def test_ac13_spans_in_default_store(hone_home: Path) -> None:
    result = engine().run("song")
    db = hone_home / "select" / "spans.db"
    spans = read(db)

    names = {s["name"] for s in spans}
    assert names == {
        "hone.select.run",
        "hone.select.generate",
        "hone.select.gate",
        "hone.select.score",
        "hone.select.pairwise",
        "hone.select.decision",
    }
    (root,) = [s for s in spans if s["name"] == "hone.select.run"]
    assert root["span_id"] == result.run_id
    assert root["parent_span_id"] is None
    assert {s["trace_id"] for s in spans} == {result.trace_id}
    assert all(s["attributes"]["hone.schema_version"] == "1" for s in spans)
    assert all(s["parent_span_id"] == root["span_id"] for s in spans if s is not root)
    for key in (
        "hone.select.config_hash",
        "hone.select.config",
        "hone.select.task",
        "hone.select.policy",
        "hone.select.n",
        "hone.select.budget.cost_used",
    ):
        assert key in root["attributes"]

    generate = next(s for s in spans if s["name"] == "hone.select.generate")["attributes"]
    assert set(generate["hone.select.candidate"]) == {"id", "meta", "data_preview", "data_sha256"}
    gate_span = next(s for s in spans if s["name"] == "hone.select.gate")["attributes"]
    assert gate_span["hone.select.gate"] == "short"
    assert gate_span["hone.select.gate.passed"] is True
    score = next(s for s in spans if s["attributes"].get("hone.select.scorer") == "judged")["attributes"]
    assert score["hone.select.score.value"] == 0.5
    assert score["hone.candidate_id"] in {s.candidate.id for s in result.ranked}
    for key in ("hone.select.scorer_version", "hone.select.score.confidence", "hone.select.score.reason"):
        assert key in score

    decision = next(s for s in spans if s["name"] == "hone.select.decision")["attributes"]
    assert result.winner is not None
    assert decision["hone.select.winner_id"] == result.winner.candidate.id
    assert [row["id"] for row in decision["hone.select.ranked"]] == [s.candidate.id for s in result.ranked]
    assert decision["hone.select.decision_trace"] == result.decision
    assert decision["hone.select.escalated"] is True
    assert decision["hone.select.fallback_used"] is False

    con = sqlite3.connect(db)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    assert (meta["schema"], meta["schema_version"], meta["package"]) == ("hone-spans", "1", "hone-select")
    assert con.execute("SELECT count(*) FROM changes").fetchone()[0] == len(spans)
    con.close()
    assert FAKE_KEY not in db.read_bytes().decode("utf-8", "replace")


def test_ac13_incoming_trace_is_continued(hone_home: Path) -> None:
    judge = FakeDecisionClient(answers={"judged": 0.5})
    judged = PromptScorer("judged", "Is it catchy?", judge)
    trace = {
        "traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01",
        "hone.run_id": "flow-run-7",
        "hone.lens.finding_id": "F-0001",
    }
    result = Engine(CONFIG, registry=[write, short, flat, prefer_first, judged]).run("song", trace=trace)

    assert result.trace_id == TRACE_ID
    spans = read(hone_home / "select" / "spans.db")
    assert {s["trace_id"] for s in spans} == {TRACE_ID}
    (root,) = [s for s in spans if s["name"] == "hone.select.run"]
    assert root["parent_span_id"] == PARENT_ID
    assert all(s["attributes"]["hone.run_id"] == "flow-run-7" for s in spans)
    assert all(s["attributes"]["hone.lens.finding_id"] == "F-0001" for s in spans)

    # the judge is called inside the score span and receives it as its parent
    score_ids = {s["span_id"] for s in spans if s["attributes"].get("hone.select.scorer") == "judged"}
    passed = judge.calls[0]["trace"]
    assert passed["traceparent"].split("-")[1] == TRACE_ID
    assert passed["traceparent"].split("-")[2] in score_ids
    assert passed["hone.scorer"] == "judged"
    assert passed["hone.run_id"] == "flow-run-7"


def test_ac13_capture_content_off_stores_hashes_only(tmp_path: Path) -> None:
    db = tmp_path / "custom" / "spans.db"
    config = CONFIG + f'[record]\nsink = "sqlite"\npath = "{db}"\ncapture_content = false\n'
    result = engine(config).run("secret-lyric")

    text = db.read_bytes().decode("utf-8", "replace")
    assert "secret-lyric" not in text
    spans = read(db)
    generate = next(s for s in spans if s["name"] == "hone.select.generate")["attributes"]
    record = generate["hone.select.candidate"]
    assert "data_preview" not in record
    assert len(record["data_sha256"]) == 64
    assert set(record["meta"]) == {"sha256", "len"}
    reason = next(s for s in spans if s["name"] == "hone.select.score")["attributes"][
        "hone.select.score.reason"
    ]
    assert set(reason) == {"sha256", "len"}
    assert result.winner is not None  # the Result itself keeps the content


def test_ac13_env_switch_turns_capture_off(hone_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONE_CAPTURE_CONTENT", "0")
    engine().run("private-task")
    assert "private-task" not in (hone_home / "select" / "spans.db").read_bytes().decode("utf-8", "replace")


def test_ac13_capture_off_hides_error_text(tmp_path: Path) -> None:
    @scorer(name="leaky")
    def leaky(c):
        raise ValueError("leak " + c.data["lyrics"])

    @gate(name="leaky_gate")
    def leaky_gate(c):
        raise ValueError("gate leak " + c.data["lyrics"])

    db = tmp_path / "spans.db"
    config = f"""
[generate]
n = 1
[score]
cascade = [{{ scorers = ["leaky"] }}]
[select]
fallback = "best_rejected"
[record]
path = "{db}"
capture_content = false
"""
    result = Engine(config, registry=[write, leaky]).run("secret-lyric")
    assert "secret-lyric" in result.ranked[0].scores["leaky"].error  # the Result keeps it
    gated = config.replace("[score]\n", '[score]\ngates = ["leaky_gate"]\n')
    Engine(gated, registry=[write, leaky, leaky_gate]).run("secret-lyric")
    assert "secret-lyric" not in db.read_bytes().decode("utf-8", "replace")
    con = sqlite3.connect(db)
    messages = {m for (m,) in con.execute("SELECT status_message FROM spans WHERE status_code = 'error'")}
    con.close()
    assert messages == {"ValueError"}


def test_ac13_current_trace_reaches_the_generator(hone_home: Path) -> None:
    seen: list[dict[str, str]] = []

    @generator(name="traced")
    def traced(task, v):
        seen.append(current_trace())
        return "x"

    result = Engine("[generate]\nn = 1", registry=[traced]).run("t")
    spans = read(hone_home / "select" / "spans.db")
    generate = next(s for s in spans if s["name"] == "hone.select.generate")
    assert seen[0]["traceparent"] == f"00-{result.trace_id}-{generate['span_id']}-01"


def test_ac13_default_store_is_dot_hone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HONE_HOME")
    monkeypatch.chdir(tmp_path)
    result = engine().run("song")
    spans = read(tmp_path / ".hone" / "select" / "spans.db")
    root = next(s for s in spans if s["name"] == "hone.select.run")
    assert root["span_id"] == result.run_id
    assert root["attributes"]["hone.select.budget.seconds_used"] >= 0


def test_ac13_unwritable_store_is_a_config_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    with pytest.raises(ConfigError, match="cannot open the span store"):
        Engine(f'[record]\npath = "{blocker}/spans.db"')


def test_ac13_task_preview_is_cut_and_select_records_no_task(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    engine = Engine(f'[record]\npath = "{db}"\n[generate]\nn = 1\n', registry=[write])
    engine.run("x" * 5000)
    root = next(s for s in read_spans(db) if s["name"] == "hone.select.run")["attributes"]
    assert root["hone.select.task"] == "x" * 2000
    engine.select([Candidate.of({"lyrics": "given"})])
    roots = [s["attributes"] for s in read_spans(db) if s["name"] == "hone.select.run"]
    assert "hone.select.task" not in roots[-1]
