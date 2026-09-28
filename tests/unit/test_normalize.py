import pytest
from hypothesis import given
from hypothesis import strategies as st

from hone_select import from_1_5, inverse, linear, sigmoid


def test_linear_and_inverse_clamp() -> None:
    f = linear(10, 20)
    assert (f(5), f(15), f(25)) == (0.0, 0.5, 1.0)
    g = inverse(10, 20)
    assert (g(5), g(15), g(25)) == (1.0, 0.5, 0.0)
    with pytest.raises(ValueError, match="lo != hi"):
        linear(1, 1)


def test_sigmoid_and_1_5() -> None:
    s = sigmoid(3, k=2)
    assert s(3) == 0.5
    assert s(10) > 0.99
    assert sigmoid(3, k=-1)(10) < 0.01
    assert [from_1_5(x) for x in (1, 3, 5, 7)] == [0.0, 0.5, 1.0, 1.0]


def test_sigmoid_never_overflows() -> None:
    assert sigmoid(0, 1)(-1000) == pytest.approx(0.0)
    assert sigmoid(0, 1)(1000) == pytest.approx(1.0)


@given(st.floats(-1e6, 1e6), st.floats(-100, 100))
def test_sigmoid_in_range(x: float, k: float) -> None:
    assert 0.0 <= sigmoid(0, k)(x) <= 1.0
