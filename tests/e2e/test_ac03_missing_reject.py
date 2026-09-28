"""AC-3: missing = "reject" rejects a candidate whose scorer failed, with the reason recorded."""

import pytest

from hone_select import Engine, generator, scorer

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    return ["ok", "broken", "fine"][v["index"]]


@scorer()
def fragile(c):
    return None if c.data == "broken" else 0.5


@scorer()
def other(c):
    return 1.0 if c.data == "broken" else 0.1


def config(missing: str) -> str:
    return f"""
[generate]
n = 3
[score]
cascade = [{{ scorers = ["fragile", "other"] }}]
missing = "{missing}"
"""


def test_ac3_missing_reject() -> None:
    result = Engine(config("reject"), registry=[gen, fragile, other]).run("t")
    broken = next(s for s in result.ranked if s.candidate.data == "broken")
    assert broken.rejected
    assert result.ranked[-1] is broken
    assert result.winner is not None
    assert result.winner.candidate.data != "broken"
    reasons = [e for e in result.decision if e["event"] == "rejected"]
    assert reasons == [
        {
            "event": "rejected",
            "candidate": broken.candidate.id,
            "reason": "no score from ['fragile'] (missing=reject)",
        }
    ]


def test_ac3_other_missing_policies() -> None:
    renorm = Engine(config("renormalize"), registry=[gen, fragile, other]).run("t")
    assert renorm.winner is not None
    assert renorm.winner.candidate.data == "broken"  # 1.0 over the one scorer that answered
    zero = Engine(config("zero"), registry=[gen, fragile, other]).run("t")
    broken = next(s for s in zero.ranked if s.candidate.data == "broken")
    assert broken.total == pytest.approx(0.5)  # (0 + 1.0) / 2
    assert broken.scores["fragile"].value is None  # the score itself stays None
