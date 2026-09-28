import math

import pytest

from hone_select import GateResult, Score
from hone_select.types import to_gate_result, to_score


def test_to_score_conversions() -> None:
    assert to_score(0.5) == Score(0.5)
    assert to_score(1) == Score(1.0)
    assert to_score(None) == Score(None)
    s = Score(0.3, confidence=0.9, reason="r")
    assert to_score(s) is s
    assert to_score({"value": 0.4, "confidence": 0.5, "reason": "x", "details": {"k": 1}}) == Score(
        0.4, 0.5, "x", {"k": 1}
    )

    class Like:
        value = None
        error = "no audio"

    assert to_score(Like()) == Score(None, error="no audio")


@pytest.mark.parametrize("bad", [1.5, -0.1, math.nan])
def test_out_of_range_is_an_error_not_a_clamp(bad: float) -> None:
    score = to_score(bad)
    assert score.value is None
    assert "outside 0..1" in score.error


def test_to_gate_result() -> None:
    assert to_gate_result(True) == GateResult(True)
    g = GateResult(False, 0.2, "why")
    assert to_gate_result(g) is g
    assert to_gate_result({"passed": False, "probability": 0.3, "reason": "r"}) == GateResult(False, 0.3, "r")
    with pytest.raises(TypeError, match="bool or GateResult"):
        to_gate_result(0.7)


@pytest.mark.parametrize("bad", ["0.7", [0.5], {"score": 1}, True, object()])
def test_unusable_scorer_returns_raise(bad: object) -> None:
    with pytest.raises(TypeError, match="a scorer must return"):
        to_score(bad)


@pytest.mark.parametrize("confidence", [7.0, -1.0, math.nan])
def test_confidence_out_of_range_is_an_error(confidence: float) -> None:
    score = to_score(Score(0.5, confidence=confidence))
    assert score.value is None
    assert "confidence" in score.error
