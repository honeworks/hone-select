"""Turn raw measurements into 0..1 values (higher is better). Each returns a plain function.

>>> linear(0, 10)(5)
0.5
>>> inverse(0, 10)(2)
0.8
"""

from __future__ import annotations

import math
from collections.abc import Callable


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def linear(lo: float, hi: float) -> Callable[[float], float]:
    """``lo`` -> 0, ``hi`` -> 1, clamped."""
    if hi == lo:
        raise ValueError("linear(lo, hi) needs lo != hi")
    return lambda x: _clamp((x - lo) / (hi - lo))


def inverse(lo: float, hi: float) -> Callable[[float], float]:
    """Lower raw values are better: ``lo`` -> 1, ``hi`` -> 0, clamped."""
    scale = linear(lo, hi)
    return lambda x: 1.0 - scale(x)


def sigmoid(mid: float, k: float = 1.0) -> Callable[[float], float]:
    """Smooth step: ``mid`` -> 0.5; larger ``k`` is steeper (negative ``k`` means lower is better)."""

    def curve(x: float) -> float:
        z = -k * (x - mid)
        return 0.0 if z > 700 else 1.0 / (1.0 + math.exp(z))  # exp overflows beyond ~709

    return curve


def from_1_5(x: float) -> float:
    """A 1..5 rating to 0..1: 1 -> 0, 3 -> 0.5, 5 -> 1."""
    return _clamp((x - 1.0) / 4.0)
