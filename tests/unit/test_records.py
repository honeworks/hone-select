"""Span sinks, trace context and explain-from-store."""

import json
import sqlite3
from pathlib import Path

import pytest

from hone_select import Engine, HoneSelectError, generator, scorer
from hone_select._records import JsonlSpanSink, NullSink, SqliteSpanSink, read_spans, scrub
from hone_select._tracing import current_trace, parse_traceparent, span
from hone_select.explain import explain_run
from hone_select.testing import MemorySink
from hone_select.testing.contracts import example_span


def test_large_attributes_go_to_blobs(tmp_path: Path) -> None:
    sink = SqliteSpanSink(tmp_path / "spans.db")
    record = example_span()
    big = "x" * (70 * 1024)
    record["attributes"] = {**record["attributes"], "hone.select.ranked": big}
    sink.emit(record)
    con = sqlite3.connect(tmp_path / "spans.db")
    stored = json.loads(con.execute("SELECT attributes FROM spans").fetchone()[0])
    assert set(stored["hone.select.ranked"]) == {"$blob"}
    assert con.execute("SELECT count(*) FROM blobs").fetchone()[0] == 1
    con.close()
    assert read_spans(tmp_path / "spans.db")[0]["attributes"]["hone.select.ranked"] == big


def test_secrets_are_scrubbed() -> None:
    value = {"a": ["sk-abcdefghijkl", ("Bearer abc.def",)], "n": 1}
    assert scrub(value) == {"a": ["***", ["***"]], "n": 1}


def test_sinks_never_raise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sqlite = SqliteSpanSink(tmp_path / "spans.db")
    sqlite.close()
    sqlite.emit(example_span())
    sqlite.emit(example_span())
    assert sqlite.failures == 2
    assert capsys.readouterr().err.count("record sink failed") == 1

    folder = tmp_path / "folder"
    jsonl = JsonlSpanSink(folder / "spans.jsonl")
    folder.chmod(0o500)
    try:
        jsonl.emit(example_span())
    finally:
        folder.chmod(0o700)
    assert jsonl.failures == 1
    for sink in (NullSink(), jsonl):
        sink.flush()
        sink.close()


def test_spans_nest_and_record_errors() -> None:
    sink = MemorySink()
    assert current_trace() == {}
    seen: list[tuple[str | None, str | None]] = []

    def fail() -> None:
        with span(sink, "outer"), span(sink, "inner") as inner:
            seen.append(parse_traceparent(current_trace()["traceparent"]))
            seen.append((inner["trace_id"], inner["span_id"]))
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        fail()
    assert seen[0] == seen[1]
    inner_record, outer_record = sink.spans
    assert inner_record["parent_span_id"] == outer_record["span_id"]
    assert inner_record["status"]["code"] == outer_record["status"]["code"] == "error"
    assert current_trace() == {}
    assert parse_traceparent("garbage") == (None, None)


@generator()
def gen(task, v):
    return f"{task} {v['index']}"


@scorer()
def length(c):
    return len(c.data) / 10


def test_explain_from_store_alone(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    config = f'[generate]\nn = 2\n[score]\ncascade = [{{ scorers = ["length"] }}]\n[record]\npath = "{db}"\n'
    engine = Engine(config, registry=[gen, length])
    result = engine.run("hi")
    from_db = explain_run(db, result.run_id)
    assert from_db == engine.explain(result)
    assert result.ranked[0].candidate.id in from_db
    with pytest.raises(HoneSelectError, match="no decision for run 'nope'"):
        explain_run(db, "nope")


def test_malformed_traceparent_starts_a_new_trace(caplog: pytest.LogCaptureFixture) -> None:
    sink = MemorySink()
    with span(sink, "hone.select.run", trace={"traceparent": "not-a-traceparent"}):
        pass
    record = sink.spans[0]
    assert record["parent_span_id"] is None
    assert len(record["trace_id"]) == 32
    assert "malformed traceparent" in caplog.text


def test_resource_has_service_name() -> None:
    sink = MemorySink()
    with span(sink, "hone.select.run"):
        pass
    assert sink.spans[0]["resource"]["service.name"]
