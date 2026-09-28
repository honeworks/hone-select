"""AC-8: a low-confidence deciding score triggers escalation even with a large margin."""

import pytest

from hone_select import Candidate, Engine, Score, generator, pairwise, scorer

pytestmark = pytest.mark.e2e


@scorer()
def unsure(c):
    return {"alpha": Score(0.9, confidence=0.4), "beta": Score(0.3, confidence=0.9)}[c.data]


calls: list[str] = []


@pairwise()
def judge(a, b):
    calls.append(a.data)
    return "a" if a.data == "beta" else "b"


@generator()
def gen(task, v):
    return ["alpha", "beta"][v["index"]]


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    calls.clear()


CONFIG = """
[score]
cascade = [{ scorers = ["unsure"] }]
[select]
tie_margin = 0.01
min_confidence = 0.6
escalate = "pairwise"
pairwise = "judge"
"""


def test_ac8_low_confidence_escalates() -> None:
    result = Engine("[generate]\nn = 2\n" + CONFIG, registry=[gen, unsure, judge]).run("t")
    assert result.ranked[0].candidate.data == "beta"
    assert calls == ["alpha", "beta"]  # both orders
    escalation = next(e for e in result.decision if e["event"] == "escalation")
    assert escalation["reason"] == "unsure confidence 0.4 below min_confidence 0.6"


def test_ac8_confident_scores_do_not_escalate() -> None:
    config = CONFIG.replace("min_confidence = 0.6", "min_confidence = 0.3")
    ranked = Engine(config, registry=[unsure, judge]).score([Candidate.of("alpha"), Candidate.of("beta")])
    assert ranked[0].candidate.data == "alpha"


def test_ac8_single_candidate_never_escalates() -> None:
    ranked = Engine(CONFIG, registry=[unsure, judge]).score([Candidate.of("alpha")])
    assert ranked[0].candidate.data == "alpha"
    assert calls == []


@pytest.mark.parametrize(("confidence", "escalates"), [(None, False), (0.0, True)])
def test_ac8_none_confidence_is_not_low(confidence: float | None, escalates: bool) -> None:
    @scorer(name="unsure")
    def scored(c):
        return Score({"alpha": 0.9, "beta": 0.3}[c.data], confidence=confidence)

    ranked = Engine(CONFIG, registry=[scored, judge]).score([Candidate.of("alpha"), Candidate.of("beta")])
    assert (calls != []) is escalates
    assert ranked[0].candidate.data == ("beta" if escalates else "alpha")
