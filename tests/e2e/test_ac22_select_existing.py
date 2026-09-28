"""AC-22: Engine.select(candidates) returns the full Result for existing candidates (design change 0006)."""

from collections.abc import Mapping, Sequence

import pytest

from hone_select import Candidate, Engine, Result, scorer
from hone_select.explain import explain_run

pytestmark = pytest.mark.e2e


@scorer()
def fit(c):
    return {"a": 0.9, "b": 0.5, "c": 0.7}[c.data]


CONFIG = """
[score]
cascade = [{ scorers = ["fit"] }]
"""


def test_ac22_select_keeps_decision_trace_and_run_id(tmp_path) -> None:
    config = CONFIG + f'[record]\npath = "{tmp_path / "spans.db"}"\n'
    engine = Engine(config, registry=[fit], cache=None)
    a, b = Candidate.of("a"), Candidate.of("b")

    result = engine.select([a, b, Candidate.of("a")])

    assert isinstance(result, Result)
    assert result.winner is not None
    assert result.winner.candidate.id == a.id
    assert [s.candidate.id for s in result.ranked] == [a.id, b.id]
    dedup = [e for e in result.decision if e["event"] == "dedup"]
    assert dedup == [{"event": "dedup", "candidate": a.id, "duplicate_of": a.id, "method": "exact"}]
    assert result.budget["cost_used"] == 2.0
    assert f"run {result.run_id}" in explain_run(tmp_path / "spans.db", result.run_id)


def test_ac22_select_names_an_embedder_failure() -> None:
    config = CONFIG + '[dedup]\nmethod = "embedding"\n[record]\nsink = "none"\n'

    class Broken:
        model_id = "broken"
        dimensions = 3

        def embed(self, texts: Sequence[str], *, trace: Mapping[str, str] | None = None) -> list[list[float]]:
            raise ConnectionError("embedder is down")

    broken = Broken()
    result = Engine(config, registry=[fit], embedder=broken, cache=None).select(
        [Candidate.of("a"), Candidate.of("c")]
    )
    warnings = [e["message"] for e in result.decision if e["event"] == "warning"]
    assert any("embedding dedup failed" in w for w in warnings)


def test_ac22_score_is_select_ranked() -> None:
    engine = Engine(CONFIG + '[record]\nsink = "none"\n', registry=[fit], cache=None)
    candidates = [Candidate.of(x) for x in "bca"]
    ranked = engine.score(candidates)
    assert [s.candidate.id for s in ranked] == [s.candidate.id for s in engine.select(candidates).ranked]
