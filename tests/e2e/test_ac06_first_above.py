"""AC-6: first_above stops generating once a candidate reaches the threshold."""

import pytest

from hone_select import Candidate, Engine, gate, generator, scorer

pytestmark = pytest.mark.e2e

generated: list[int] = []


@generator()
def gen(task, v):
    generated.append(v["index"])
    return f"c{v['index']}"


@scorer()
def quality(c):
    return {"c0": 0.5, "c1": 0.9, "c2": 0.95}.get(c.data, 0.99)


CONFIG = """
[generate]
n = 5
[score]
cascade = [{ scorers = ["quality"] }]
[select]
policy = "first_above"
threshold = 0.8
"""


def test_ac6_first_above_stops_early() -> None:
    generated.clear()
    result = Engine(CONFIG, registry=[gen, quality]).run("t")
    assert generated == [0, 1]
    assert result.winner is not None
    assert result.winner.candidate.data == "c1"
    assert len(result.ranked) == 2
    assert {"event": "stop", "reason": "threshold 0.8 reached", "generated": 2} in result.decision


def test_ac6_threshold_never_met_takes_best() -> None:
    generated.clear()
    result = Engine(CONFIG.replace("0.8", "0.999"), registry=[gen, quality]).run("t")
    assert generated == [0, 1, 2, 3, 4]
    assert result.winner is not None
    assert result.winner.total == pytest.approx(0.99)
    assert any(e["event"] == "threshold_not_met" for e in result.decision)


def test_ac6_score_uses_generation_order() -> None:
    ranked = Engine(CONFIG, registry=[gen, quality]).score([Candidate.of(d) for d in ["c0", "c1", "c2"]])
    assert ranked[0].candidate.data == "c1"  # first above threshold, even though c2 scores higher


def test_ac6_rejected_candidate_above_threshold_does_not_stop() -> None:
    @gate()
    def not_c1(c):
        return c.data != "c1"

    generated.clear()
    config = CONFIG.replace("[score]", '[score]\ngates = ["not_c1"]')
    result = Engine(config, registry=[gen, quality, not_c1]).run("t")
    assert generated == [0, 1, 2]
    assert result.winner is not None
    assert result.winner.candidate.data == "c2"
    assert not result.winner.rejected
