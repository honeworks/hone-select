"""AC-15: duplicates are removed (exactly, or by embedding similarity) and recorded."""

import math
from typing import ClassVar

import pytest

from hone_select import Candidate, ConfigError, Engine, generator, scorer
from hone_select.testing import FakeEmbedder

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    return ["same", "same", "hello there", "hello there!", "other"][v["index"]]


scored: list[str] = []


@scorer()
def q(c):
    scored.append(c.data)
    return 0.5


def config(method: str, policy: str = "argmax") -> str:
    return f"""
[generate]
n = 5
[dedup]
method = "{method}"
[score]
cascade = [{{ scorers = ["q"] }}]
[select]
policy = "{policy}"
threshold = 0.99
"""


def test_ac15_exact_dedup() -> None:
    scored.clear()
    result = Engine(config("exact"), registry=[gen, q]).run("t")
    assert len(scored) == 4  # the duplicate is removed before scoring
    assert [s.candidate.data for s in result.ranked] == ["same", "hello there", "hello there!", "other"]
    same = Candidate.of("same").id
    assert {"event": "dedup", "candidate": same, "duplicate_of": same, "method": "exact"} in result.decision


def test_ac15_embedding_dedup() -> None:
    embedder = FakeEmbedder(similar={"hello there!": "hello there"})
    scored.clear()
    result = Engine(config("embedding"), registry=[gen, q], embedder=embedder).run("t")
    assert [s.candidate.data for s in result.ranked] == ["same", "hello there", "other"]
    assert len(scored) == 3
    assert embedder.calls[0] == ["same", "same", "hello there", "hello there!", "other"]
    removed = [(e["candidate"], e["duplicate_of"]) for e in result.decision if e["event"] == "dedup"]
    assert removed == [
        (Candidate.of("same").id, Candidate.of("same").id),
        (Candidate.of("hello there!").id, Candidate.of("hello there").id),
    ]


def test_ac15_dedup_while_streaming_first_above() -> None:
    embedder = FakeEmbedder(similar={"hello there!": "hello there"})
    result = Engine(config("embedding", "first_above"), registry=[gen, q], embedder=embedder).run("t")
    assert [s.candidate.data for s in result.ranked] == ["same", "hello there", "other"]
    dedups = [(e["candidate"], e["duplicate_of"]) for e in result.decision if e["event"] == "dedup"]
    assert dedups[-1] == (Candidate.of("hello there!").id, Candidate.of("hello there").id)


def test_ac15_off_and_missing_embedder() -> None:
    result = Engine(config("off"), registry=[gen, q]).run("t")
    assert len(result.ranked) == 5
    with pytest.raises(ConfigError, match="needs an embedder"):
        Engine(config("embedding"), registry=[gen, q]).run("t")


class AngleEmbedder:
    """2-d unit vectors: cosine to "hello there" is 0.96 for "hello there!" and 0.90 for "other" (on the
    other side, so "other" is far from "hello there!")."""

    model_id = "angles"
    dimensions = 2
    ANGLES: ClassVar[dict[str, float]] = {
        "hello there": 0.0,
        "hello there!": math.acos(0.96),
        "other": -math.acos(0.90),
        "same": math.pi,
    }

    def embed(self, texts, *, trace=None):
        return [[math.cos(a), math.sin(a)] for a in (self.ANGLES[t] for t in texts)]


def test_ac15_embedding_threshold() -> None:
    default = Engine(config("embedding"), registry=[gen, q], embedder=AngleEmbedder()).run("t")
    assert [s.candidate.data for s in default.ranked] == ["same", "hello there", "other"]  # 0.96 >= 0.95
    strict = config("embedding").replace('method = "embedding"', 'method = "embedding"\nthreshold = 0.97')
    kept = Engine(strict, registry=[gen, q], embedder=AngleEmbedder()).run("t")
    assert [s.candidate.data for s in kept.ranked] == ["same", "hello there", "hello there!", "other"]


class BrokenEmbedder(FakeEmbedder):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def embed(self, texts, *, trace=None):
        if self.mode == "raise":
            raise RuntimeError("server down")
        return super().embed(texts)[:1]


@pytest.mark.parametrize(("mode", "reason"), [("raise", "server down"), ("short", "1 vectors for 5 texts")])
def test_ac15_embedder_failure_falls_back_to_exact(mode: str, reason: str) -> None:
    result = Engine(config("embedding"), registry=[gen, q], embedder=BrokenEmbedder(mode)).run("t")
    assert len(result.ranked) == 4  # exact dedup still removed the repeated "same"
    warnings = [e["message"] for e in result.decision if e["event"] == "warning"]
    assert any("embedding dedup failed" in w and reason in w for w in warnings)


def test_ac15_embedder_gets_the_trace() -> None:
    embedder = FakeEmbedder()
    calls: list[dict] = []
    original = embedder.embed

    def spy(texts, *, trace=None):
        calls.append(dict(trace or {}))
        return original(texts, trace=trace)

    embedder.embed = spy  # type: ignore[method-assign]
    result = Engine(config("embedding"), registry=[gen, q], embedder=embedder).run("t")
    assert calls[0]["traceparent"].split("-")[1] == result.trace_id
