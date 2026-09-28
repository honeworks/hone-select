"""AC-1: the README quickstart runs; winner is the shortest non-rejected candidate."""

import pytest

from hone_select import Engine, gate, generator, scorer

pytestmark = pytest.mark.e2e


@generator()
def write(task, v):
    return f"{task} #{v['index']}" * (v["index"] + 1)


@gate()
def not_too_long(c):
    return len(c.data) < 80


@scorer(cost=1)
def shorter_is_better(c):
    return 1 - len(c.data) / 100


CONFIG = """
[generate]
n = 5
[score]
gates = ["not_too_long"]
cascade = [{ scorers = ["shorter_is_better"] }]
[select]
policy = "argmax"
"""


def test_ac1_quickstart() -> None:
    engine = Engine(CONFIG, registry=[write, not_too_long, shorter_is_better])
    result = engine.run("hello")

    assert result.winner is not None
    assert result.winner.candidate.data == "hello #0"
    assert result.winner.total == pytest.approx(1 - len("hello #0") / 100)
    assert not result.winner.rejected
    assert len(result.ranked) == 5
    # "hello #4" * 5 is 40 chars (passes); lengths grow with index, so ranking follows index
    assert [s.candidate.data for s in result.ranked][:3] == ["hello #0", "hello #1" * 2, "hello #2" * 3]
    events = [e["event"] for e in result.decision]
    for expected in ("generated", "gated", "scored", "selected"):
        assert expected in events
    assert result.run_id
    assert len(result.trace_id) == 32


def test_ac1_gate_rejects_long_candidates() -> None:
    engine = Engine(CONFIG.replace("n = 5", "n = 12"), registry=[write, not_too_long, shorter_is_better])
    result = engine.run("hello")
    rejected = [s for s in result.ranked if s.rejected]
    assert rejected, "long candidates must fail the gate"
    assert all(len(s.candidate.data) >= 80 for s in rejected)
    assert all(not s.gates["not_too_long"].passed for s in rejected)
    assert result.ranked[-len(rejected) :] == rejected  # rejected rank after non-rejected


def test_ac1_argmax_picks_the_best_not_the_first() -> None:
    @generator(name="write")
    def reverse(task, v):
        return f"{task} #{v['index']}" * (5 - v["index"])  # the shortest comes last

    result = Engine(CONFIG, registry=[reverse, not_too_long, shorter_is_better]).run("hello")
    assert result.winner is not None
    assert result.winner.candidate.data == "hello #4"
    totals = [s.total or 0.0 for s in result.ranked]
    assert totals == sorted(totals, reverse=True)
    assert len(set(totals)) == len(totals)
    selected = next(e for e in result.decision if e["event"] == "selected")
    assert selected["winner"] == result.winner.candidate.id
    assert selected["total"] == result.winner.total
