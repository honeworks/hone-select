"""AC-17: a judge from the generator's model family triggers a warning in the decision trace."""

import pytest

from hone_select import Candidate, Engine, PromptGate, PromptPairwise, PromptScorer, generator
from hone_select.testing import FakeDecisionClient

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    return Candidate.of(f"lyric {v['index']}", meta={"model": "gemma4-12b:latest"})


def run(judge_model: str) -> list[str]:
    judge = FakeDecisionClient(model_id=judge_model)
    quality = PromptScorer("quality", "Is it good?", judge)
    config = '[generate]\nn = 2\n[score]\ncascade = [{ scorers = ["quality"] }]'
    result = Engine(config, registry=[gen, quality]).run("t")
    return [e["message"] for e in result.decision if e["event"] == "warning"]


def test_ac17_same_family_warns() -> None:
    warnings = run("gemma3:4b")
    assert len(warnings) == 1
    assert "self-judging" in warnings[0]
    assert "'gemma3:4b'" in warnings[0]
    assert "'gemma4-12b:latest'" in warnings[0]


def test_ac17_other_family_is_quiet() -> None:
    assert run("qwen3.8-27b") == []


def test_ac17_warning_is_logged_and_covers_gates_and_pairwise(caplog: pytest.LogCaptureFixture) -> None:
    judge = FakeDecisionClient(model_id="ollama/gemma3")
    config = """
[generate]
n = 2
[score]
gates = ["clean"]
[select]
policy = "pairwise_tournament"
pairwise = "better"
"""
    registry = [gen, PromptGate("clean", "ok?", judge), PromptPairwise("better", "which?", judge)]
    with caplog.at_level("WARNING", logger="hone_select"):
        result = Engine(config, registry=registry).run("t")
    warnings = [e["message"] for e in result.decision if e["event"] == "warning"]
    assert sum("self-judging" in w for w in warnings) == 2
    # the fake always answers "A" (the first option): a position-biased judge, warned separately (0003)
    assert sum("first-shown" in w for w in warnings) == 1
    assert sum("self-judging" in r.getMessage() for r in caplog.records) == 2


def test_ac17_no_warning_without_generator_model_or_for_unused_judges() -> None:
    @generator(name="plain")
    def plain(task, v):
        return f"lyric {v['index']}"

    judge = FakeDecisionClient(model_id="gemma3")
    config = '[generate]\nn = 2\n[score]\ncascade = [{ scorers = ["quality"] }]'
    result = Engine(config, registry=[plain, PromptScorer("quality", "ok?", judge)]).run("t")
    assert not [e for e in result.decision if e["event"] == "warning"]

    unused = PromptScorer("unused", "ok?", judge)
    quiet = PromptScorer("quality", "ok?", FakeDecisionClient(model_id="qwen3"))
    result = Engine(config, registry=[gen, quiet, unused]).run("t")
    assert not [e for e in result.decision if e["event"] == "warning"]
