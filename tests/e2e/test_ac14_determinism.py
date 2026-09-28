"""AC-14: same config + seed -> identical ranking and totals."""

import random

import pytest

from hone_select import Engine, generator, scorer

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    rng = random.Random(v["seed"])
    return {"text": f"{task}-{rng.randint(0, 10**6)}", "temperature": v["params"]["temperature"]}


@scorer()
def noisy(c):
    return random.Random(c.id).random()


CONFIG = """
[generate]
n = 6
vary = { temperature = [0.7, 1.0] }
[score]
cascade = [{ scorers = ["noisy"], keep_top = 3 }]
"""


def summary(seed: int) -> list[tuple[str, float | None, int]]:
    result = Engine(CONFIG, registry=[gen, noisy]).run("song", seed=seed)
    return [(s.candidate.id, s.total, s.stage_reached) for s in result.ranked]


def test_ac14_same_seed_same_result() -> None:
    assert summary(7) == summary(7)
    assert summary(7) != summary(8)


def test_ac14_variations_reach_the_generator() -> None:
    result = Engine(CONFIG, registry=[gen, noisy]).run("song", seed=100)
    metas = sorted((s.candidate.meta["index"], s.candidate.meta["seed"]) for s in result.ranked)
    assert metas == [(i, 100 + i) for i in range(6)]
    temps = {s.candidate.meta["index"]: s.candidate.data["temperature"] for s in result.ranked}
    assert temps == {0: 0.7, 1: 1.0, 2: 0.7, 3: 1.0, 4: 0.7, 5: 1.0}


def test_ac14_decision_trace_and_scores_are_identical() -> None:
    first = Engine(CONFIG, registry=[gen, noisy]).run("song", seed=3)
    second = Engine(CONFIG, registry=[gen, noisy], cache=None).run("song", seed=3)
    assert first.decision == second.decision
    assert [s.scores for s in first.ranked] == [s.scores for s in second.ranked]
    assert first.run_id != second.run_id
