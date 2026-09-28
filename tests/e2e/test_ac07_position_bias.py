"""AC-7 (design change 0003): a pairwise judge that chooses by position is named, and can stop being asked."""

import pytest

from hone_select import Candidate, ConfigError, Engine, pairwise, scorer
from hone_select.testing import MemorySink

pytestmark = pytest.mark.e2e

calls: list[tuple[str, str]] = []

FOUR = [Candidate.of(x) for x in ("alpha", "beta", "gamma", "delta")]


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    calls.clear()


@scorer()
def flat(c):
    return 0.5  # every candidate ties: three comparisons in the king-of-the-hill


def judge(rule):
    @pairwise(name="judge")
    def compare(a, b):
        calls.append((a.data, b.data))
        return rule(a.data, b.data)

    return compare


def config(extra: str = "") -> str:
    return f"""
[score]
cascade = [{{ scorers = ["flat"] }}]
[select]
escalate = "pairwise"
pairwise = "judge"
{extra}
"""


def test_ac7_first_shown_bias_is_named_and_warned() -> None:
    result = Engine(config(), registry=[flat, judge(lambda a, b: "a")], cache=None).select(FOUR)
    entries = [e for e in result.decision if e["event"] == "pairwise"]
    assert len(entries) == 3
    assert all(e["outcome"] == "tie" and e["reason"] == "position_bias" for e in entries)
    warnings = [e["message"] for e in result.decision if e["event"] == "warning"]
    assert len(warnings) == 1
    assert "'judge'" in warnings[0]
    assert "3/3" in warnings[0]
    assert "first-shown" in warnings[0]
    assert result.ranked[0].candidate.data == "alpha"  # the ranking is unchanged


def test_ac7_second_shown_bias_is_named_too() -> None:
    result = Engine(config(), registry=[flat, judge(lambda a, b: "b")], cache=None).select(FOUR[:2])
    entry = next(e for e in result.decision if e["event"] == "pairwise")
    assert entry["reason"] == "position_bias"
    assert any("second-shown" in e["message"] for e in result.decision if e["event"] == "warning")


def test_ac7_a_consistent_judge_has_no_bias_reason_or_warning() -> None:
    prefers_gamma = judge(lambda a, b: "a" if a == "gamma" else ("b" if b == "gamma" else "tie"))
    result = Engine(config(), registry=[flat, prefers_gamma], cache=None).select(FOUR)
    assert result.ranked[0].candidate.data == "gamma"
    assert not any("reason" in e for e in result.decision if e["event"] == "pairwise")
    assert not any(e["event"] == "warning" for e in result.decision)


def test_ac7_bias_in_some_comparisons_gives_no_run_warning() -> None:
    # alpha vs beta: biased; alpha vs gamma: gamma wins in both orders; gamma vs delta: tie
    rule = judge(
        lambda a, b: "a" if a == "gamma" else ("b" if b == "gamma" else ("tie" if "delta" in (a, b) else "a"))
    )
    result = Engine(config(), registry=[flat, rule], cache=None).select(FOUR)
    reasons = [e.get("reason") for e in result.decision if e["event"] == "pairwise"]
    assert reasons == ["position_bias", None, None]
    assert not any(e["event"] == "warning" for e in result.decision)


def test_ac7_max_biased_pairwise_skips_further_calls() -> None:
    engine = Engine(config("max_biased_pairwise = 2"), registry=[flat, judge(lambda a, b: "a")], cache=None)
    result = engine.select(FOUR)
    assert len(calls) == 4  # two biased comparisons (both orders each), then no more judge calls
    skipped = [e for e in result.decision if e["event"] == "escalation_skipped"]
    assert skipped == [
        {
            "event": "escalation_skipped",
            "a": FOUR[0].id,
            "b": FOUR[3].id,
            "reason": "position_bias",
            "biased_in_a_row": 2,
        }
    ]
    assert result.budget["cost_used"] == 4 * 1.0 + 4 * 10.0
    assert result.ranked[0].candidate.data == "alpha"


def test_ac7_default_never_skips() -> None:
    Engine(config(), registry=[flat, judge(lambda a, b: "a")], cache=None).select(FOUR)
    assert len(calls) == 6


def test_ac7_bias_reason_survives_content_capture_off() -> None:
    sink = MemorySink()
    extra = "[record]\ncapture_content = false"
    Engine(config(extra), registry=[flat, judge(lambda a, b: "a")], sink=sink, cache=None).select(FOUR[:2])
    trace = sink.named("hone.select.decision")[0]["attributes"]["hone.select.decision_trace"]
    assert next(e for e in trace if e["event"] == "pairwise")["reason"] == "position_bias"


def test_ac7_max_biased_pairwise_must_be_positive() -> None:
    with pytest.raises(ConfigError, match="max_biased_pairwise"):
        Engine(config("max_biased_pairwise = 0"), registry=[flat, judge(lambda a, b: "a")])
