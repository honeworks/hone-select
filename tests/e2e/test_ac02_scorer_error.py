"""AC-2: a scorer that raises for one candidate gives Score(None, error); aggregation renormalizes."""

import pytest

from hone_select import Candidate, Engine, generator, scorer

pytestmark = pytest.mark.e2e


@scorer()
def flaky(c):
    if c.data == "bad":
        raise RuntimeError("model timed out")
    return 0.2


@scorer()
def steady(c):
    return 0.9


CONFIG = """
[score]
cascade = [{ scorers = ["flaky", "steady"] }]
"""


def test_ac2_scorer_error_is_none_not_zero() -> None:
    engine = Engine(CONFIG, registry=[flaky, steady])
    ranked = engine.score([Candidate.of("good"), Candidate.of("bad")])

    bad = next(s for s in ranked if s.candidate.data == "bad")
    assert bad.scores["flaky"].value is None
    assert "RuntimeError: model timed out" in bad.scores["flaky"].error
    assert bad.total == pytest.approx(0.9)  # renormalized over the scorer that worked, not (0 + 0.9) / 2
    assert not bad.rejected
    good = next(s for s in ranked if s.candidate.data == "good")
    assert good.total == pytest.approx((0.2 + 0.9) / 2)
    assert ranked[0] is bad  # the run completed and ranked by totals


def test_ac2_error_is_in_decision_trace() -> None:
    @generator()
    def gen(task, v):
        return "bad"

    result = Engine(
        CONFIG.replace("[score]", "[generate]\nn = 1\n[score]"), registry=[gen, flaky, steady]
    ).run("t")
    errors = [e for e in result.decision if e["event"] == "score_error"]
    assert errors == [
        {
            "event": "score_error",
            "candidate": Candidate.of("bad").id,
            "scorer": "flaky",
            "error": "RuntimeError: model timed out",
        }
    ]
