"""Combine a candidate's scores into one total. ``None`` scores are never treated as 0 unless asked."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

from hone_select.types import Score

Pairs = list[tuple[float, float]]  # (weight, value)


def weighted_mean(pairs: Pairs, floor: float) -> float | None:
    weight = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / weight if weight > 0 else None


def minimum(pairs: Pairs, floor: float) -> float | None:
    return min(v for _, v in pairs)


def geometric(pairs: Pairs, floor: float) -> float | None:
    weight = sum(w for w, _ in pairs)
    if weight <= 0:
        return None
    return math.exp(sum(w * math.log(max(v, 1e-6)) for w, v in pairs) / weight)


def weighted_mean_with_floor(pairs: Pairs, floor: float) -> float | None:
    mean = weighted_mean(pairs, floor)
    if mean is not None and any(v < floor for _, v in pairs):
        return min(mean, floor)
    return mean


AGGREGATORS: dict[str, Callable[[Pairs, float], float | None]] = {
    "weighted_mean": weighted_mean,
    "min": minimum,
    "geometric": geometric,
    "weighted_mean_with_floor": weighted_mean_with_floor,
}


def total(
    scores: Mapping[str, Score],
    weights: Mapping[str, float],
    method: str = "weighted_mean",
    missing: str = "renormalize",
    floor: float = 0.0,
) -> float | None:
    """Aggregate ``scores``; scorers not in ``weights`` get weight 1.

    ``missing="zero"`` counts ``None`` scores as 0; otherwise they are left out (the caller rejects the
    candidate for ``missing="reject"``). A candidate with no values at all has total ``None``.

    >>> total({"a": Score(1.0), "b": Score(None)}, {})
    1.0
    """
    if all(s.value is None for s in scores.values()):
        return None
    pairs: Pairs = []
    for name, score in scores.items():
        if score.value is not None:
            pairs.append((weights.get(name, 1.0), score.value))
        elif missing == "zero":
            pairs.append((weights.get(name, 1.0), 0.0))
    return AGGREGATORS[method](pairs, floor)
