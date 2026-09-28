"""AC-5: with keep_top = 2 the costly stage-2 scorer runs exactly twice."""

import pytest

from hone_select import Engine, generator, scorer

pytestmark = pytest.mark.e2e

calls: list[str] = []


@generator()
def gen(task, v):
    return f"draft {v['index']}"


@scorer(cost=1)
def cheap(c):
    return int(c.data[-1]) / 10


@scorer(cost=100)
def costly(c):
    calls.append(c.data)
    return 0.5


CONFIG = """
[generate]
n = 5
[score]
cascade = [
  { scorers = ["cheap"], keep_top = 2 },
  { scorers = ["costly"] },
]
"""


def test_ac5_cascade_keep_top() -> None:
    calls.clear()
    result = Engine(CONFIG, registry=[gen, cheap, costly]).run("t")

    assert sorted(calls) == ["draft 3", "draft 4"]
    by_data = {s.candidate.data: s for s in result.ranked}
    assert by_data["draft 4"].stage_reached == 2
    assert by_data["draft 3"].stage_reached == 2
    assert all(by_data[f"draft {i}"].stage_reached == 1 for i in range(3))
    assert all("costly" not in by_data[f"draft {i}"].scores for i in range(3))
    assert result.winner is not None
    assert result.winner.candidate.data == "draft 4"
    assert result.budget["cost_used"] == 5 * 1 + 2 * 100
    stages = [e for e in result.decision if e["event"] == "scored"]
    assert [len(e["kept"]) for e in stages] == [2, 2]


def test_ac5_ties_at_the_cut_are_kept() -> None:
    calls.clear()

    @scorer(name="cheap")
    def flat(c):
        return 0.5

    Engine(CONFIG, registry=[gen, flat, costly]).run("t")
    assert len(calls) == 5


def test_ac5_candidates_cut_early_never_beat_finalists() -> None:
    @generator(name="gen")
    def three(task, v):
        return "abc"[v["index"]]

    @scorer(name="cheap")
    def cheap_score(c):
        return {"a": 0.9, "b": 0.8, "c": 0.7}[c.data]

    @scorer(name="costly")
    def costly_score(c):
        return 0.1

    result = Engine(CONFIG.replace("n = 5", "n = 3"), registry=[three, cheap_score, costly_score]).run("t")
    assert [s.candidate.data for s in result.ranked] == ["a", "b", "c"]  # "c" has 0.7 but was cut
    assert result.winner is not None
    assert result.winner.candidate.data == "a"
