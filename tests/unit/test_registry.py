import pytest

from hone_select import ConfigError, gate, generator, pairwise, scorer
from hone_select.registry import Component, as_component, load_registry


def test_decorators_keep_function_callable_and_set_metadata() -> None:
    @scorer(name="len", cost=2, version="3")
    def length(c: object) -> float:
        return 0.5

    assert isinstance(length, Component)
    assert (length.kind, length.name, length.cost, length.version) == ("scorer", "len", 2.0, "3")
    assert length(None) == 0.5

    @gate()
    def g(c: object) -> bool:
        return True

    @pairwise()
    def p(a: object, b: object) -> str:
        return "a"

    @generator()
    def gen(task: object, v: object) -> str:
        return "x"

    assert (g.name, g.kind, g.cost) == ("g", "gate", 0.0)
    assert (p.kind, p.cost) == ("pairwise", 10.0)
    assert (gen.kind, gen.cost) == ("generator", 0.0)


def test_plain_callable_with_scorer_attributes() -> None:
    class TasteScorer:
        name = "taste"
        cost = 3
        version = "7"

        def __call__(self, c: object) -> float:
            return 0.1

    comp = as_component(TasteScorer())
    assert (comp.kind, comp.name, comp.cost, comp.version, comp.judge_model) == (
        "scorer",
        "taste",
        3.0,
        "7",
        "",
    )

    def bare(c: object) -> float:
        return 0.2

    assert as_component(bare).name == "bare"


def test_registry_errors() -> None:
    with pytest.raises(ConfigError, match="not callable"):
        as_component(42)

    class Weird:
        kind = "oracle"

        def __call__(self) -> None: ...

    with pytest.raises(ConfigError, match="unknown kind 'oracle'"):
        as_component(Weird())

    class NoName:
        name = ""

        def __call__(self) -> None: ...

    with pytest.raises(ConfigError, match="no name"):
        as_component(NoName())

    @scorer(name="dup")
    def a(c: object) -> float:
        return 0.0

    @gate(name="dup")
    def b(c: object) -> bool:
        return True

    with pytest.raises(ConfigError, match="named 'dup'"):
        load_registry([a, b])


def test_scorer_taking_trace_gets_the_score_span_context() -> None:
    """design/current.md §7.1 / §7.7: a scorer from another package (e.g. hone-taste's for_select) joins the run's
    trace through a `trace=` keyword."""
    from hone_select import Engine
    from hone_select.testing import MemorySink

    seen: list[dict[str, str]] = []

    class TraceAware:
        name = "aware"

        def __call__(self, c: object, /, *, trace: dict[str, str] | None = None) -> float:
            seen.append(dict(trace or {}))
            return 0.5

    @generator()
    def say(task: object, v: dict[str, int]) -> str:
        return f"x{v['index']}"

    sink = MemorySink()
    config = "[generate]\nn = 1\n[score]\ncascade = [{ scorers = ['aware'] }]"
    result = Engine(config, registry=[say, TraceAware()], sink=sink).run("t")
    (score_span,) = [s for s in sink.spans if s["name"] == "hone.select.score"]
    assert seen[0]["traceparent"] == f"00-{result.trace_id}-{score_span['span_id']}-01"
    assert seen[0]["hone.scorer"] == "aware"
    assert result.winner is not None
    assert seen[0]["hone.candidate_id"] == result.winner.candidate.id


def test_callables_without_trace_or_signature_are_called_plainly() -> None:
    from hone_select.registry import takes_trace

    assert not takes_trace(max)  # no signature available
    assert not takes_trace(lambda c: 1.0)
    assert takes_trace(lambda c, trace=None: 1.0)
