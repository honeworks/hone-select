"""The public fakes satisfy the contract checkers of design/current.md §7.9."""

from pathlib import Path

from hone_select._records import JsonlSpanSink, SqliteSpanSink, read_spans
from hone_select.testing import FakeDecisionClient, FakeEmbedder, FakeTextClient, MemorySink, contracts


def test_fake_decision_client() -> None:
    contracts.check_decision_client(FakeDecisionClient())
    contracts.check_decision_client(FakeDecisionClient(answers={"q1": "no", "q2": "red", "q3": 0.9}))
    contracts.check_decision_client(FakeDecisionClient(answers={"q1": {"value": None, "error": "refused"}}))


def test_fake_text_client() -> None:
    contracts.check_text_client(FakeTextClient())
    contracts.check_text_client(FakeTextClient(responses=["not json"]))
    fake = FakeTextClient(responses=["one", "two"])
    assert [fake.complete([]).text for _ in range(3)] == ["one", "two", "one"]
    assert fake.complete([], schema={"type": "object"}).error


def test_fake_embedder() -> None:
    contracts.check_embedder(FakeEmbedder())
    e = FakeEmbedder(similar={"b": "a"}, dimensions=64)
    contracts.check_embedder(e)
    a, b, c = e.embed(["a", "b", "c"])
    assert a == b
    assert sum(x * y for x, y in zip(a, c, strict=True)) < 0.95  # distinct texts are not duplicates


def test_sinks(tmp_path: Path) -> None:
    memory = MemorySink()
    contracts.check_record_sink(memory, lambda: memory.spans)
    sqlite = SqliteSpanSink(tmp_path / "spans.db")
    contracts.check_record_sink(sqlite, lambda: read_spans(tmp_path / "spans.db"))
    jsonl = JsonlSpanSink(tmp_path / "spans.jsonl")

    def read_jsonl() -> list[dict]:
        import json

        return [json.loads(line) for line in (tmp_path / "spans.jsonl").read_text().splitlines()]

    contracts.check_record_sink(jsonl, read_jsonl)
