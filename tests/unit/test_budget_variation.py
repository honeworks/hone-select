import pytest

from hone_select import BudgetExceeded
from hone_select.budget import Budget
from hone_select.config import BudgetConfig
from hone_select.variation import variations


def test_budget_limits() -> None:
    b = Budget(BudgetConfig(max_cost=10, max_money_usd=1.0))
    assert b.exhausted() is None
    b.charge(cost=5, money=1.0)
    assert b.exhausted() == "max_money_usd 1 reached (1 used)"
    with pytest.raises(BudgetExceeded, match="max_money_usd"):
        b.check()
    assert Budget(BudgetConfig(max_seconds=0)).exhausted() == "max_seconds 0 reached"
    unlimited = Budget(BudgetConfig())
    unlimited.charge(cost=1e9)
    unlimited.check()
    assert unlimited.summary()["cost_used"] == 1e9


def test_variations() -> None:
    assert variations(2, {}) == [{"index": 0, "seed": 0, "params": {}}, {"index": 1, "seed": 1, "params": {}}]
    vs = variations(3, {"seed": [5, 9], "top_p": [0.9]}, seed=100)
    assert [v["seed"] for v in vs] == [5, 9, 5]
    assert all(v["params"] == {"top_p": 0.9} for v in vs)
