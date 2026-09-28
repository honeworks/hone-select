"""AC-11: when max_cost runs out mid-generation, generation stops and selection uses what exists."""

import pytest

from hone_select import BudgetExceeded, Candidate, Engine, generator, scorer

pytestmark = pytest.mark.e2e


@generator(cost=10)
def gen(task, v):
    return f"c{v['index']}"


@scorer(cost=0)
def quality(c):
    return int(c.data[1:]) / 10


CONFIG = """
[generate]
n = 8
[score]
cascade = [{ scorers = ["quality"] }]
[budget]
max_cost = 25
"""


def test_ac11_budget_stops_generation() -> None:
    result = Engine(CONFIG, registry=[gen, quality]).run("t")  # BudgetExceeded is not raised

    assert [s.candidate.data for s in result.ranked] == ["c2", "c1", "c0"]
    assert result.winner is not None
    assert result.winner.candidate.data == "c2"
    stop = [e for e in result.decision if e["event"] == "stop"]
    assert stop == [{"event": "stop", "reason": "max_cost 25 reached (30 used)", "generated": 3}]
    assert result.budget["cost_used"] == 30


def test_ac11_generator_can_signal_budget_exhausted() -> None:
    @generator()
    def capped(task, v):
        if v["index"] == 2:
            raise BudgetExceeded("provider spending cap hit")
        return f"c{v['index']}"

    result = Engine(CONFIG.replace("max_cost = 25", ""), registry=[capped, quality]).run("t")
    assert len(result.ranked) == 2
    assert {"event": "stop", "reason": "provider spending cap hit", "generated": 2} in result.decision


def test_ac11_later_stages_skipped_when_budget_is_gone() -> None:
    @scorer(cost=0)
    def second(c):
        raise AssertionError("must not run")

    config = CONFIG.replace(
        '{ scorers = ["quality"] }', '{ scorers = ["quality"] }, { scorers = ["second"] }'
    )
    result = Engine(config, registry=[gen, quality, second]).run("t")
    assert all(s.stage_reached == 1 for s in result.ranked)
    assert {"event": "stop", "reason": "max_cost 25 reached (30 used)", "stage": 2} in result.decision


def test_ac11_money_budget_from_generator_meta() -> None:
    @generator()
    def paid(task, v):
        return Candidate.of(f"c{v['index']}", meta={"cost_usd": 0.4, "model": "gpt-x"})

    config = CONFIG.replace("max_cost = 25", "max_money_usd = 1.0")
    result = Engine(config, registry=[paid, quality]).run("t")
    assert len(result.ranked) == 3
    assert result.budget["money_used"] == pytest.approx(1.2)
    assert {
        "event": "stop",
        "reason": "max_money_usd 1 reached (1.2 used)",
        "generated": 3,
    } in result.decision
    assert set(result.budget) == {"cost_used", "seconds_used", "money_used"}
    assert result.ranked[0].candidate.meta["model"] == "gpt-x"


def test_ac11_zero_budget_gives_no_winner_without_raising() -> None:
    result = Engine(CONFIG.replace("max_cost = 25", "max_cost = 0"), registry=[gen, quality]).run("t")
    assert result.ranked == []
    assert result.winner is None
    assert {"event": "stop", "reason": "max_cost 0 reached (0 used)", "generated": 0} in result.decision
