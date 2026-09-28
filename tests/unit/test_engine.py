import pytest

from hone_select import Candidate, ConfigError, Engine, Score, generator, scorer
from hone_select.testing import MemorySink

Key = tuple[str, str, str, str]  # (candidate id, scorer, version, judge model)
CASCADE = '[score]\ncascade = [{ scorers = ["q"] }]\n'


@scorer()
def q(c):
    return 0.5


@generator()
def gen(task, v):
    return f"{task}{v['index']}"


def test_run_needs_exactly_one_generator() -> None:
    with pytest.raises(ConfigError, match=r"exactly one registered @generator, found \[\]"):
        Engine(CASCADE, registry=[q], sink=MemorySink()).run("t")

    @generator(name="other")
    def gen2(task, v):
        return 1

    with pytest.raises(ConfigError, match="found"):
        Engine(CASCADE, registry=[gen, gen2, q], sink=MemorySink()).run("t")


def test_generator_error_is_recorded_and_skipped() -> None:
    @generator()
    def sometimes(task, v):
        if v["index"] == 1:
            raise ValueError("model refused")
        return v["index"]

    sink = MemorySink()
    result = Engine("[generate]\nn = 3\n" + CASCADE, registry=[sometimes, q], sink=sink).run("t")
    assert len(result.ranked) == 2
    assert {"event": "generate_error", "index": 1, "error": "ValueError: model refused"} in result.decision
    statuses = [s["status"]["code"] for s in sink.named("hone.select.generate")]
    assert statuses == ["ok", "error", "ok"]


def test_warnings_goodhart_and_all_none() -> None:
    @scorer(name="q")
    def nothing(c):
        return None

    result = Engine("[generate]\nn = 11\n" + CASCADE, registry=[gen, nothing], sink=MemorySink()).run("t")
    messages = [e["message"] for e in result.decision if e["event"] == "warning"]
    assert any("Goodhart risk: n=11" in m for m in messages)
    assert any("all scores are None" in m for m in messages)
    assert result.winner is not None  # still a winner, but the trace says the ranking is uninformative
    assert result.winner.total is None


def test_explain_mentions_winner_and_events() -> None:
    engine = Engine("[generate]\nn = 2\n" + CASCADE, registry=[gen, q], sink=MemorySink())
    result = engine.run("t")
    text = engine.explain(result)
    assert result.run_id in text
    assert "1. " + result.ranked[0].candidate.id in text
    assert "total=0.5000" in text
    assert "selected: winner=" in text


def test_score_without_cascade_gives_none_totals() -> None:
    ranked = Engine("", registry=[], sink=MemorySink()).score([Candidate.of("a"), Candidate.of("b")])
    assert [s.total for s in ranked] == [None, None]
    assert [s.candidate.data for s in ranked] == ["a", "b"]


class DictCache:
    """A ScoreCache of one's own (examples/score_cache.py): any object with get and put."""

    def __init__(self) -> None:
        self.scores: dict[Key, Score] = {}
        self.gets = 0

    def get(self, key: Key) -> Score | None:
        self.gets += 1
        return self.scores.get(key)

    def put(self, key: Key, score: Score) -> None:
        self.scores[key] = score


def test_any_object_with_get_and_put_is_a_score_cache() -> None:
    calls: list[str] = []

    @scorer(version="3")
    def flaky(c):
        calls.append(c.data)
        if c.data == "bad":
            raise RuntimeError("down")
        return 0.25

    config = '[score]\ncascade = [{ scorers = ["flaky"] }]\n'
    cache = DictCache()
    candidates = [Candidate.of("good"), Candidate.of("bad")]
    Engine(config, registry=[flaky], sink=MemorySink(), cache=cache).score(candidates)
    assert list(cache.scores) == [(candidates[0].id, "flaky", "3", "")]  # the failure is not stored
    ranked = Engine(config, registry=[flaky], sink=MemorySink(), cache=cache).score(candidates)
    assert calls == ["good", "bad", "bad"]  # only the failure is retried
    assert ranked[0].scores["flaky"].value == 0.25
    assert cache.gets == 4
