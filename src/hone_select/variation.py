"""Variation schedules: which seed and params each generated candidate gets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hone_select.types import Variation


def variations(n: int, vary: Mapping[str, str | list[Any]], seed: int = 0) -> list[Variation]:
    """``n`` variations. ``vary["seed"]`` is ``"increment"`` (``seed + index``, default) or a list of seeds;
    every other key is a list of values that cycles independently.

    >>> [v["params"]["temperature"] for v in variations(4, {"temperature": [0.8, 1.0]})]
    [0.8, 1.0, 0.8, 1.0]
    """
    seeds = vary.get("seed", "increment")
    grids = {key: values for key, values in vary.items() if key != "seed"}
    result: list[Variation] = []
    for index in range(n):
        this_seed = seed + index if isinstance(seeds, str) else int(seeds[index % len(seeds)])
        params = {key: values[index % len(values)] for key, values in grids.items()}
        result.append({"index": index, "seed": this_seed, "params": params})
    return result
