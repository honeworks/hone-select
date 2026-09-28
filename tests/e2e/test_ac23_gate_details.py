"""AC-23: a gate's per-item details are kept in Scored.gates and recorded on the gate span (design change
0004)."""

from typing import Any

import pytest

from hone_select import Candidate, Engine, GateResult, gate, scorer
from hone_select.testing import MemorySink

pytestmark = pytest.mark.e2e


@gate()
def questions_valid(c):
    rows = [{"q": i, "ok": ok} for i, ok in enumerate(c.data["checks"])]
    return GateResult(all(c.data["checks"]), reason="some question failed", details={"questions": rows})


@gate()
def mapping_gate(c):
    return {"passed": True, "details": {"confirmed": sum(c.data["checks"])}}


@scorer()
def one(c):
    return 1.0


CONFIG = """
[score]
gates = ["questions_valid", "mapping_gate"]
cascade = [{ scorers = ["one"] }]
"""

GOOD = Candidate.of({"checks": [True, True]})
BAD = Candidate.of({"checks": [True, False]})


def test_ac23_gate_details_in_scored() -> None:
    engine = Engine(CONFIG, registry=[questions_valid, mapping_gate, one], sink=MemorySink(), cache=None)
    result = engine.select([GOOD, BAD])
    by_id = {s.candidate.id: s for s in result.ranked}

    good = by_id[GOOD.id].gates
    assert good["questions_valid"].details == {"questions": [{"q": 0, "ok": True}, {"q": 1, "ok": True}]}
    assert good["mapping_gate"].details == {"confirmed": 2}  # a GateLike mapping keeps its details too
    bad = by_id[BAD.id]
    assert bad.rejected
    assert bad.gates["questions_valid"].details["questions"][1] == {"q": 1, "ok": False}


def _gate_spans(sink: MemorySink, name: str) -> list[dict[str, Any]]:
    return [
        s["attributes"] for s in sink.named("hone.select.gate") if s["attributes"]["hone.select.gate"] == name
    ]


def test_ac23_gate_details_on_the_span() -> None:
    sink = MemorySink()
    Engine(CONFIG, registry=[questions_valid, mapping_gate, one], sink=sink, cache=None).select([GOOD])
    (attributes,) = _gate_spans(sink, "questions_valid")
    assert attributes["hone.select.gate.details"] == {
        "questions": [{"q": 0, "ok": True}, {"q": 1, "ok": True}]
    }


def test_ac23_gate_details_are_content() -> None:
    sink = MemorySink()
    config = CONFIG + "[record]\ncapture_content = false\n"
    Engine(config, registry=[questions_valid, mapping_gate, one], sink=sink, cache=None).select([GOOD])
    (attributes,) = _gate_spans(sink, "questions_valid")
    assert set(attributes["hone.select.gate.details"]) == {"sha256", "len"}


def test_ac23_gate_without_details() -> None:
    assert GateResult(True).details == {}
