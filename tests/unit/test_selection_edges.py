"""Edge cases of gating, cascade and ranking, through the public API."""

import pytest

from hone_select import Candidate, ConfigError, Engine, GateResult, gate, generator, scorer
from hone_select.testing import MemorySink
from hone_select.variation import variations


def engine(config: str, *items: object) -> Engine:
    return Engine(config, registry=list(items), sink=MemorySink(), cache=None)


def test_none_ranks_after_zero_and_keeps_generation_order() -> None:
    @scorer()
    def s(c):
        return 0.0 if c.data == "B" else None

    ranked = engine('[score]\ncascade = [{ scorers = ["s"] }]', s).score(
        [Candidate.of("A"), Candidate.of("B"), Candidate.of("C")]
    )
    assert [r.candidate.data for r in ranked] == ["B", "A", "C"]
    assert [r.total for r in ranked] == [0.0, None, None]


def test_gates_run_cheapest_first_and_short_circuit() -> None:
    calls: list[str] = []

    @gate(cost=5)
    def expensive(c):
        calls.append("expensive")
        return True

    @gate(cost=1)
    def cheap(c):
        calls.append("cheap")
        return GateResult(c.data == "ok", reason="must be ok")

    @generator()
    def gen(task, v):
        return ["ok", "no"][v["index"]]

    @scorer()
    def s(c):
        return 0.5

    config = '[generate]\nn = 2\n[score]\ngates = ["expensive", "cheap"]\ncascade = [{ scorers = ["s"] }]'
    result = engine(config, gen, expensive, cheap, s).run("t")
    assert calls == ["cheap", "expensive", "cheap"]
    gated = next(e for e in result.decision if e["event"] == "gated")
    assert gated == {
        "event": "gated",
        "passed": [Candidate.of("ok").id],
        "rejected": {Candidate.of("no").id: "cheap: must be ok"},
    }


def test_gate_returning_none_rejects_with_error() -> None:
    @gate()
    def forgetful(c):
        return None

    result = engine('[score]\ngates = ["forgetful"]', forgetful).score([Candidate.of("x")])
    assert result[0].rejected
    assert (
        result[0].gates["forgetful"].reason
        == "error: TypeError: a gate must return bool or GateResult, got None"
    )


def test_keep_top_does_not_treat_none_as_a_tie() -> None:
    seen: list[str] = []

    @scorer()
    def first(c):
        return {"a": None, "b": None, "c": 0.1}[c.data]

    @scorer()
    def second(c):
        seen.append(c.data)
        return 0.5

    config = '[score]\ncascade = [{ scorers = ["first"], keep_top = 2 }, { scorers = ["second"] }]'
    engine(config, first, second).score([Candidate.of(x) for x in "abc"])
    assert sorted(seen) == ["a", "c"]


def test_missing_reject_in_stage_one_never_reaches_stage_two() -> None:
    seen: list[str] = []

    @scorer()
    def first(c):
        return None if c.data == "a" else 0.5

    @scorer()
    def second(c):
        seen.append(c.data)
        return 0.5

    config = '[score]\nmissing = "reject"\ncascade = [{ scorers = ["first"] }, { scorers = ["second"] }]'
    engine(config, first, second).score([Candidate.of("a"), Candidate.of("b")])
    assert seen == ["b"]


@pytest.mark.parametrize("fallback", ["best_rejected", "none"])
def test_missing_reject_rejecting_everyone(fallback: str) -> None:
    @scorer()
    def s(c):
        return None

    @scorer()
    def t(c):
        return 0.3

    cascade = '[{ scorers = ["s", "t"] }]'
    config = f'[score]\nmissing = "reject"\ncascade = {cascade}\n[select]\nfallback = "{fallback}"'
    ranked = engine(config, s, t).score([Candidate.of("a"), Candidate.of("b")])
    assert all(r.rejected for r in ranked)
    assert ranked[0].total == pytest.approx(0.3)


def test_fallback_not_used_when_someone_passes() -> None:
    @gate()
    def only_short(c):
        return len(c.data) < 3

    @scorer()
    def length(c):
        return len(c.data) / 10

    @generator()
    def gen(task, v):
        return ["ab", "abcdef"][v["index"]]

    config = (
        '[generate]\nn = 2\n[score]\ngates = ["only_short"]\ncascade = [{ scorers = ["length"] }]\n'
        '[select]\nfallback = "best_rejected"'
    )
    result = engine(config, gen, only_short, length).run("t")
    assert result.winner is not None
    assert result.winner.candidate.data == "ab"
    assert not result.winner.rejected
    assert not any(e["event"] == "fallback" for e in result.decision)
    assert result.ranked[1].scores == {}


def test_kind_confusion_is_a_config_error() -> None:
    @gate()
    def g(c):
        return True

    @scorer()
    def s(c):
        return 0.5

    with pytest.raises(ConfigError, match="unknown scorer 'g'"):
        engine('[score]\ncascade = [{ scorers = ["g"] }]', g, s)
    with pytest.raises(ConfigError, match="unknown gate 's'"):
        engine('[score]\ngates = ["s"]', g, s)
    with pytest.raises(ConfigError, match="select.threshold"):
        engine("[select]\nthreshold = 1.5")


def test_variation_grids_cycle_independently() -> None:
    vs = variations(6, {"a": [1, 2], "b": ["x", "y", "z"]})
    assert [(v["params"]["a"], v["params"]["b"]) for v in vs] == [
        (1, "x"),
        (2, "y"),
        (1, "z"),
        (2, "x"),
        (1, "y"),
        (2, "z"),
    ]
