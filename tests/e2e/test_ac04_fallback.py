"""AC-4: when every candidate fails a gate, fallback decides: best rejected (flagged) or no winner."""

import pytest

from hone_select import Engine, GateResult, gate, generator, scorer

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    return "x" * (v["index"] + 1)


@gate()
def impossible(c):
    return GateResult(False, probability=0.1, reason="never good enough")


@scorer()
def length(c):
    return len(c.data) / 10


def config(fallback: str) -> str:
    return f"""
[generate]
n = 3
[score]
gates = ["impossible"]
cascade = [{{ scorers = ["length"] }}]
[select]
fallback = "{fallback}"
"""


def test_ac4_best_rejected() -> None:
    result = Engine(config("best_rejected"), registry=[gen, impossible, length]).run("t")
    assert result.winner is not None
    assert result.winner.candidate.data == "xxx"
    assert result.winner.rejected
    assert result.winner.gates["impossible"].reason == "never good enough"
    fallback = [e for e in result.decision if e["event"] == "fallback"]
    assert fallback == [{"event": "fallback", "mode": "best_rejected", "winner": result.winner.candidate.id}]


def test_ac4_first_valid() -> None:
    result = Engine(config("first_valid"), registry=[gen, impossible, length]).run("t")
    assert result.winner is not None
    assert result.winner.candidate.data == "x"
    assert result.winner.rejected


def test_ac4_fallback_none() -> None:
    result = Engine(config("none"), registry=[gen, impossible, length]).run("t")
    assert result.winner is None
    assert all(s.rejected for s in result.ranked)
    assert all(s.scores == {} for s in result.ranked)  # nothing is scored when no fallback needs it
    assert {"event": "fallback", "mode": "none", "winner": None} in result.decision


def test_ac4_gate_error_rejects_visibly() -> None:
    @gate()
    def crashes(c):
        raise ValueError("bad input")

    cfg = config("none").replace('"impossible"', '"crashes"')
    result = Engine(cfg, registry=[gen, crashes, length]).run("t")
    assert all(s.rejected for s in result.ranked)
    assert result.ranked[0].gates["crashes"].reason == "error: ValueError: bad input"
    assert sum(e["event"] == "gate_error" for e in result.decision) == 3
