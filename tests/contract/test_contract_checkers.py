"""The exported contract checkers reject implementations that break the ports (design/current.md §7)."""

from typing import Any

import pytest

from hone_select.testing import FakeDecisionClient, FakeEmbedder, contracts


class ScriptedDecisions(FakeDecisionClient):
    """Returns the fake's answers with one field of one question overridden."""

    def __init__(self, question: str, **override: Any) -> None:
        super().__init__()
        self.question, self.override = question, override

    def decide(self, state, questions, *, images=(), trace=None):
        answers = super().decide(state, questions, images=images, trace=trace)
        answers[self.question] = {**answers[self.question], **self.override}
        return answers


@pytest.mark.parametrize(
    "client",
    [
        ScriptedDecisions("q1", value=1.5),
        ScriptedDecisions("q3", type="yes_no"),
        ScriptedDecisions("q2", choice="green"),
        ScriptedDecisions("q1", calibrated="yes"),
    ],
)
def test_bad_decision_clients_fail(client: FakeDecisionClient) -> None:
    with pytest.raises(AssertionError):
        contracts.check_decision_client(client)


class BadEmbedder(FakeEmbedder):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def embed(self, texts, *, trace=None):
        vectors = super().embed(texts)
        if self.mode == "unnormalized":
            return [[2 * x for x in v] for v in vectors]
        if self.mode == "short":
            return [v[:-1] for v in vectors]
        return vectors or [[1.0]]


@pytest.mark.parametrize("mode", ["unnormalized", "short", "empty"])
def test_bad_embedders_fail(mode: str) -> None:
    with pytest.raises(AssertionError):
        contracts.check_embedder(BadEmbedder(mode))


def test_sink_that_drops_spans_fails() -> None:
    class Dropping:
        def emit(self, span): ...
        def flush(self): ...

    with pytest.raises(AssertionError):
        contracts.check_record_sink(Dropping(), list)


def test_fake_decision_defaults() -> None:
    fake = FakeDecisionClient(answers={"q": "no"}, rule=lambda state, qs: {"q": "yes"})
    q = {"q": {"type": "yes_no", "instructions": "?"}}
    assert fake.decide("s", q, images=["i.png"])["q"]["value"] == 1.0  # rule wins over answers
    assert fake.calls[0]["images"] == ["i.png"]
    plain = FakeDecisionClient()
    assert plain.decide("s", q)["q"]["value"] == 0.5
    c = {"c": {"type": "choice", "instructions": "?", "options": ["x", "y"]}}
    assert plain.decide("s", c)["c"]["choice"] == "x"
