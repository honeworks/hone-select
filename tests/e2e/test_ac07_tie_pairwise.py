"""AC-7: top two within tie_margin escalate to a pairwise judge asked in both orders."""

import pytest

from hone_select import Candidate, Engine, generator, pairwise, scorer

pytestmark = pytest.mark.e2e

calls: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    calls.clear()


@scorer()
def quality(c):
    return {"alpha": 0.80, "beta": 0.79, "gamma": 0.5}[c.data]


def judge(rule):
    @pairwise(name="judge")
    def compare(a, b):
        calls.append((a.data, b.data))
        return rule(a.data, b.data)

    return compare


CONFIG = """
[score]
cascade = [{ scorers = ["quality"] }]
[select]
tie_margin = 0.03
escalate = "pairwise"
pairwise = "judge"
"""

CANDIDATES = [Candidate.of("alpha"), Candidate.of("beta"), Candidate.of("gamma")]


@generator()
def gen3(task, v):
    return ["alpha", "beta", "gamma"][v["index"]]


def test_ac7_pairwise_decides_the_tie() -> None:
    prefers_beta = judge(lambda a, b: "a" if a == "beta" else "b")
    ranked = Engine(CONFIG, registry=[quality, prefers_beta]).score(CANDIDATES)
    assert calls == [("alpha", "beta"), ("beta", "alpha")]
    assert ranked[0].candidate.data == "beta"
    assert ranked[1].candidate.data == "alpha"


def test_ac7_orders_disagree_is_a_tie() -> None:
    position_biased = judge(lambda a, b: "a")  # always prefers whatever is shown first
    engine = Engine(
        CONFIG.replace("[score]", "[generate]\nn = 3\n[score]"), registry=[quality, position_biased]
    )
    ranked = engine.score(CANDIDATES)
    assert calls == [("alpha", "beta"), ("beta", "alpha")]
    assert ranked[0].candidate.data == "alpha"  # original ranking kept


def test_ac7_trace_records_escalation_and_outcome() -> None:
    position_biased = judge(lambda a, b: "a")
    config = CONFIG.replace("[score]", "[generate]\nn = 3\n[score]")
    result = Engine(config, registry=[gen3, quality, position_biased]).run("t")
    escalation = next(e for e in result.decision if e["event"] == "escalation")
    assert escalation["candidates"] == [Candidate.of("alpha").id, Candidate.of("beta").id]
    assert "tie_margin" in escalation["reason"]
    outcome = next(e for e in result.decision if e["event"] == "pairwise")
    assert (outcome["ab"], outcome["ba"], outcome["outcome"]) == ("a", "b", "tie")


def test_ac7_no_escalation_notes_tie_only() -> None:
    j = judge(lambda a, b: "b")
    config = CONFIG.replace('escalate = "pairwise"', 'escalate = "none"')
    config = config.replace("[score]", "[generate]\nn = 3\n[score]")
    result = Engine(config, registry=[gen3, quality, j]).run("t")
    assert calls == []
    assert result.ranked[0].candidate.data == "alpha"
    tie = next(e for e in result.decision if e["event"] == "tie")
    alpha, beta = Candidate.of("alpha").id, Candidate.of("beta").id
    assert (tie["candidates"], tie["kept"]) == ([alpha, beta], alpha)
    assert "tie_margin" in tie["reason"]
    assert not any(e["event"] == "escalation" for e in result.decision)


def test_ac7_pairwise_errors_count_as_tie() -> None:
    def broken(a, b):
        raise RuntimeError("judge offline")

    config = CONFIG.replace("[score]", "[generate]\nn = 3\n[score]")
    result = Engine(config, registry=[gen3, quality, judge(broken)]).run("t")
    assert result.ranked[0].candidate.data == "alpha"
    errors = [e["error"] for e in result.decision if e["event"] == "pairwise_error"]
    assert errors == ["RuntimeError: judge offline"] * 2
    assert next(e for e in result.decision if e["event"] == "pairwise")["outcome"] == "tie"
    assert result.budget["cost_used"] == 3 * 1.0 + 2 * 10.0  # three scorer calls, two pairwise calls
    bad_answer = judge(lambda a, b: ("maybe", 0.5))
    ranked = Engine(CONFIG, registry=[quality, bad_answer]).score(CANDIDATES)
    assert ranked[0].candidate.data == "alpha"
    tuple_answer = judge(lambda a, b: ("a", 0.9) if a == "beta" else ("b", 0.9))
    ranked = Engine(CONFIG, registry=[quality, tuple_answer]).score(CANDIDATES)
    assert ranked[0].candidate.data == "beta"


def test_pairwise_tournament_policy() -> None:
    prefers_gamma = judge(lambda a, b: "a" if a == "gamma" else ("b" if b == "gamma" else "tie"))
    config = CONFIG.replace('escalate = "pairwise"', 'policy = "pairwise_tournament"')
    ranked = Engine(config, registry=[quality, prefers_gamma]).score(CANDIDATES)
    assert ranked[0].candidate.data == "gamma"
    assert len(calls) == 4  # alpha vs beta (tie), alpha vs gamma (gamma wins), both orders each


def test_ac7_three_way_tie_is_a_tournament() -> None:
    @scorer(name="quality")
    def flat(c):
        return 0.5

    prefers_gamma = judge(lambda a, b: "a" if a == "gamma" else ("b" if b == "gamma" else "tie"))
    ranked = Engine(CONFIG, registry=[flat, prefers_gamma]).score(CANDIDATES)
    assert ranked[0].candidate.data == "gamma"
    assert calls == [("alpha", "beta"), ("beta", "alpha"), ("alpha", "gamma"), ("gamma", "alpha")]
