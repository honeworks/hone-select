import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hone_select import Score
from hone_select.aggregate import total

S = Score


def test_weighted_mean_and_default_weight() -> None:
    assert total({"a": S(1.0), "b": S(0.0)}, {"a": 3}) == pytest.approx(0.75)


def test_none_is_not_zero() -> None:
    scores = {"a": S(0.8), "b": S(None)}
    assert total(scores, {}) == pytest.approx(0.8)
    assert total(scores, {}, missing="zero") == pytest.approx(0.4)
    assert total(scores, {}, missing="reject") == pytest.approx(0.8)  # rejection is the caller's job
    assert total({"a": S(None)}, {}) is None
    assert total({"a": S(None)}, {}, missing="zero") is None
    assert total({}, {}) is None


def test_other_methods() -> None:
    scores = {"a": S(0.25), "b": S(1.0)}
    assert total(scores, {}, "min") == 0.25
    assert total(scores, {}, "geometric") == pytest.approx(0.5)
    assert total({"a": S(0.0)}, {}, "geometric") == pytest.approx(1e-6)
    assert total(scores, {}, "weighted_mean_with_floor", floor=0.3) == pytest.approx(0.3)
    assert total(scores, {}, "weighted_mean_with_floor", floor=0.2) == pytest.approx(0.625)


def test_zero_weights_give_none() -> None:
    assert total({"a": S(0.5)}, {"a": 0}) is None
    assert total({"a": S(0.5)}, {"a": 0}, "geometric") is None


values = st.lists(st.floats(0, 1), min_size=1, max_size=6)


@given(values)
def test_totals_stay_in_range(vs: list[float]) -> None:
    scores = {str(i): S(v) for i, v in enumerate(vs)}
    for method in ("weighted_mean", "min", "geometric", "weighted_mean_with_floor"):
        t = total(scores, {}, method, floor=0.5)
        assert t is not None
        assert min(vs) - 1e-9 <= t or method == "geometric"
        assert t <= max(*vs, 1e-6) + 1e-9  # geometric clamps values to >= 1e-6
        assert not math.isnan(t)


def test_weighted_geometric_min_zero_and_floor_below() -> None:
    assert total({"a": S(0.25), "b": S(1.0)}, {"a": 2}, "geometric") == pytest.approx(0.25 ** (2 / 3))
    assert total({"a": S(0.5), "b": S(None)}, {}, "min", missing="zero") == 0.0
    assert total({"a": S(0.1), "b": S(0.2)}, {}, "weighted_mean_with_floor", floor=0.5) == pytest.approx(0.15)


@given(values)
def test_totals_do_not_depend_on_key_order(vs: list[float]) -> None:
    forward = {str(i): S(v) for i, v in enumerate(vs)}
    backward = dict(reversed(list(forward.items())))
    for method in ("weighted_mean", "min", "geometric", "weighted_mean_with_floor"):
        assert total(forward, {}, method) == pytest.approx(total(backward, {}, method))
