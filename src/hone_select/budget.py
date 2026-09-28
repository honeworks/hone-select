"""Cost, time and money budget for one run."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hone_select.config import BudgetConfig
from hone_select.errors import BudgetExceeded


@dataclass(slots=True)
class Budget:
    """What a run has spent. ``check()`` raises ``BudgetExceeded``; the engine turns it into a stop."""

    limits: BudgetConfig
    started: float = field(default_factory=time.monotonic)
    cost_used: float = 0.0
    money_used: float = 0.0

    def charge(self, cost: float = 0.0, money: float = 0.0) -> None:
        self.cost_used += cost
        self.money_used += money

    def seconds_used(self) -> float:
        return time.monotonic() - self.started

    def exhausted(self) -> str | None:
        """Why the budget is used up, or ``None`` if there is room left."""
        limits = self.limits
        if limits.max_cost is not None and self.cost_used >= limits.max_cost:
            return f"max_cost {limits.max_cost:g} reached ({self.cost_used:g} used)"
        if limits.max_seconds is not None and self.seconds_used() >= limits.max_seconds:
            return f"max_seconds {limits.max_seconds:g} reached"
        if limits.max_money_usd is not None and self.money_used >= limits.max_money_usd:
            return f"max_money_usd {limits.max_money_usd:g} reached ({self.money_used:g} used)"
        return None

    def check(self) -> None:
        if reason := self.exhausted():
            raise BudgetExceeded(reason)

    def summary(self) -> dict[str, float]:
        return {
            "cost_used": self.cost_used,
            "seconds_used": round(self.seconds_used(), 3),
            "money_used": self.money_used,
        }
