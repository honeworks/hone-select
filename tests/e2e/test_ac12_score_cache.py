"""AC-12: a second identical run calls no scorers; bumping one scorer's version recomputes only it."""

import pytest

from hone_select import Candidate, Engine, PromptScorer, Score, generator, scorer
from hone_select.testing import FakeDecisionClient

pytestmark = pytest.mark.e2e

calls: list[str] = []


@generator()
def gen(task, v):
    return f"draft {v['index']}"


def make_scorer(name: str, version: str):
    @scorer(name=name, version=version)
    def fn(c):
        calls.append(name)
        value = int(c.id[:4], 16) / 0xFFFF  # differs per candidate
        return Score(value, confidence=0.7, reason=f"{name} on {c.id}", details={"v": version})

    return fn


CONFIG = """
[generate]
n = 3
[score]
cascade = [{ scorers = ["a", "b"] }]
"""


def test_ac12_cache_hits_and_version_bump() -> None:
    calls.clear()
    first = Engine(CONFIG, registry=[gen, make_scorer("a", "1"), make_scorer("b", "1")]).run("t")
    assert sorted(calls) == ["a"] * 3 + ["b"] * 3

    calls.clear()
    second = Engine(CONFIG, registry=[gen, make_scorer("a", "1"), make_scorer("b", "1")]).run("t")
    assert calls == []
    hits = [e for e in second.decision if e["event"] == "cache_hit"]
    assert len(hits) == 6
    assert second.budget["cost_used"] == 0  # cached scores cost nothing
    assert [(s.candidate.id, s.total, s.scores) for s in second.ranked] == [
        (s.candidate.id, s.total, s.scores) for s in first.ranked
    ]

    calls.clear()
    third = Engine(CONFIG, registry=[gen, make_scorer("a", "2"), make_scorer("b", "1")]).run("t")
    assert calls == ["a"] * 3
    assert {e["scorer"] for e in third.decision if e["event"] == "cache_hit"} == {"b"}
    assert all(s.scores["a"].details == {"v": "2"} for s in third.ranked)


def test_ac12_failed_scores_are_not_cached_and_cache_can_be_disabled() -> None:
    attempts: list[int] = []

    @scorer(name="a")
    def flaky(c):
        attempts.append(1)
        raise RuntimeError("try later")

    config = '[score]\ncascade = [{ scorers = ["a"] }]'
    Engine(config, registry=[flaky]).score([Candidate.of("x")])
    Engine(config, registry=[flaky]).score([Candidate.of("x")])
    assert len(attempts) == 2

    calls.clear()
    Engine(config, registry=[make_scorer("a", "1")], cache=None).score([Candidate.of("x")])
    Engine(config, registry=[make_scorer("a", "1")], cache=None).score([Candidate.of("x")])
    assert calls == ["a", "a"]


def test_ac12_judge_model_is_part_of_the_key() -> None:
    config = '[score]\ncascade = [{ scorers = ["q"] }]'
    first = FakeDecisionClient(model_id="judge-a")
    Engine(config, registry=[PromptScorer("q", "Good?", first)]).score([Candidate.of("x")])
    same = FakeDecisionClient(model_id="judge-a")
    Engine(config, registry=[PromptScorer("q", "Good?", same)]).score([Candidate.of("x")])
    other = FakeDecisionClient(model_id="judge-b")
    Engine(config, registry=[PromptScorer("q", "Good?", other)]).score([Candidate.of("x")])
    assert (len(first.calls), len(same.calls), len(other.calls)) == (1, 0, 1)


def test_ac12_zero_is_cached_but_none_is_not() -> None:
    seen: list[str] = []

    @scorer(name="z")
    def zero(c):
        seen.append("z")
        return 0.0

    @scorer(name="n")
    def nothing(c):
        seen.append("n")

    config = '[score]\ncascade = [{ scorers = ["z", "n"] }]'
    for _ in range(2):
        Engine(config, registry=[zero, nothing]).score([Candidate.of("x")])
    assert sorted(seen) == ["n", "n", "z"]


def test_ac12_cache_sits_next_to_a_configured_span_store(tmp_path) -> None:
    db = tmp_path / "store" / "spans.db"
    config = f'[score]\ncascade = [{{ scorers = ["a"] }}]\n[record]\npath = "{db}"'
    Engine(config, registry=[make_scorer("a", "1")]).score([Candidate.of("x")])
    assert (tmp_path / "store" / "cache.db").exists()
