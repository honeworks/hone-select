"""Adapters: emulated decisions over a text client, LangChain, the hone_models resolver."""

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from hone_select import ConfigError, Engine, PromptScorer
from hone_select.adapters import hone_models as resolver
from hone_select.adapters.emulated import EmulatedDecisionClient, build_messages, parse_json
from hone_select.adapters.langchain import LangChainDecisionClient, LangChainTextClient
from hone_select.adapters.openai import to_openai_content
from hone_select.testing import FakeDecisionClient, FakeTextClient, contracts
from hone_select.types import Candidate

ANSWERS = json.dumps(
    {
        "q1": {"rationale": "blue", "answer": "yes"},
        "q2": {"rationale": "blue", "answer": "blue"},
        "q3": {"rationale": "ok", "answer": 3},
    }
)


def test_parse_json_variants() -> None:
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": {"b": 2}} Hope that helps.') == {"a": {"b": 2}}
    with pytest.raises(json.JSONDecodeError):
        parse_json("no json here")


def test_prompt_lists_questions_anchors_and_images() -> None:
    questions: dict[str, Any] = {
        "vivid": {"type": "score", "instructions": "How vivid?", "scale": [0, 10], "anchors": {"0": "flat"}}
    }
    system, user = build_messages({"lyrics": "la"}, questions, ["cover.png"])
    assert "JSON object only" in system["content"]
    text = user["content"][0]["text"]
    assert '"lyrics": "la"' in text
    assert "- vivid: How vivid? (answer a number from 0 to 10 (0 = flat))" in text
    assert user["content"][1] == {"type": "image", "path": "cover.png"}


def test_emulated_client_error_paths() -> None:
    q = {"q": {"type": "yes_no", "instructions": "ok?"}}
    for reply in ["[1, 2]", "not json at all"]:
        answer = EmulatedDecisionClient(FakeTextClient([reply])).decide("x", q)["q"]
        assert answer["value"] is None
        assert answer["error"]
    missing = EmulatedDecisionClient(FakeTextClient(['{"other": {}}'])).decide("x", q)["q"]
    assert missing["value"] is None
    assert missing["error"] == "unusable answer None"


def test_openai_content_parts(tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG")
    parts = to_openai_content(
        [
            {"type": "text", "text": "hi"},
            {"type": "image", "path": str(image)},
            {"type": "image", "data_b64": "QUJD", "mime": "image/jpeg"},
        ]
    )
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,iVBORw=="
    assert parts[2]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_langchain_adapters_pass_contracts() -> None:
    contracts.check_decision_client(LangChainDecisionClient(FakeListChatModel(responses=[ANSWERS])))
    contracts.check_text_client(LangChainTextClient(FakeListChatModel(responses=['{"ok": true}'])))
    contracts.check_text_client(LangChainTextClient(FakeListChatModel(responses=["not json"])))


def test_langchain_text_client_parses_and_asks_for_json() -> None:
    model = FakeListChatModel(responses=['```json\n{"ok": true}\n```'])
    result = LangChainTextClient(model).complete(
        [{"role": "user", "content": "hi"}], schema={"type": "object"}
    )
    assert result.parsed == {"ok": True}
    assert result.error is None
    bad = LangChainTextClient(FakeListChatModel(responses=["nope"])).complete([], schema={"type": "object"})
    assert bad.parsed is None
    assert bad.error is not None
    assert bad.error.startswith("invalid JSON")


def test_hone_models_resolver_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_models", None)  # makes the import fail
    with pytest.raises(ConfigError, match=r"install hone-select\[models\]"):
        resolver.decision("qwen3.8-27b")


def test_config_judge_resolves_through_the_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    fake = FakeDecisionClient(answers={"singable": 1.0})

    def decision(model: str, **options: Any) -> FakeDecisionClient:
        calls.append((model, options))
        return fake

    monkeypatch.setitem(sys.modules, "hone_models", types.SimpleNamespace(decision=decision))
    config = """
[judges.local]
client = "hone_models:decision"
model = "qwen3.8-27b"
think = false
[score]
cascade = [{ scorers = ["singable"] }]
"""
    singable = PromptScorer("singable", "Singable?", "local")
    ranked = Engine(config, registry=[singable], cache=None).score([Candidate.of("la la")])
    assert calls == [("qwen3.8-27b", {"think": False})]
    assert ranked[0].scores["singable"].value == 1.0


class Replying:
    """A text client returning one fixed result mapping, whatever it is asked."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def complete(
        self, messages: Any, *, schema: Any = None, trace: Any = None, **params: Any
    ) -> dict[str, Any]:
        return self.result


def test_emulated_client_passes_the_text_client_error_on() -> None:
    q = {"a": {"type": "yes_no", "instructions": "ok?"}, "b": {"type": "score", "instructions": "how?"}}
    answers = EmulatedDecisionClient(Replying({"text": "", "error": "invalid JSON: empty"})).decide("x", q)
    assert {name: (a["value"], a["error"]) for name, a in answers.items()} == {
        "a": (None, "invalid JSON: empty"),
        "b": (None, "invalid JSON: empty"),
    }


@pytest.mark.parametrize(
    ("question", "reply", "expected"),
    [
        ({"type": "score", "instructions": "?"}, True, None),  # a bool is not a number
        ({"type": "score", "instructions": "?", "scale": [3, 3]}, 3, None),  # empty scale
        ({"type": "score", "instructions": "?"}, 1, 0.0),  # low end is 0.0, not missing
        ({"type": "choice", "instructions": "?", "options": ["x", "y"]}, "y", 1.0),
    ],
)
def test_emulated_answer_edges(question: dict[str, Any], reply: Any, expected: float | None) -> None:
    text = json.dumps({"q": {"answer": reply, "rationale": "r"}})
    answer = EmulatedDecisionClient(FakeTextClient([text])).decide("x", {"q": question})["q"]
    assert answer["value"] == expected
    assert (answer["error"] is None) == (expected is not None)
    if question["type"] == "choice":
        assert answer["probabilities"] == {"x": 0.0, "y": 1.0}


class RecordingChat:
    """A LangChain-like chat model: records what it is sent, replies with list content and metadata."""

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        self.sent: list[Any] = []
        self.model_name = "chat-test"

    def invoke(self, messages: Any) -> AIMessage:
        self.sent.append(messages)
        return AIMessage(
            content=[{"type": "text", "text": p} for p in self.parts],
            usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            response_metadata={"finish_reason": "stop"},
        )


def test_langchain_text_client_sends_schema_and_maps_the_reply() -> None:
    chat = RecordingChat(['{"a":', " 1}"])
    schema = {"type": "object", "properties": {"a": {"type": "number"}}}
    result = LangChainTextClient(chat).complete([{"role": "user", "content": "hi"}], schema=schema)
    system = chat.sent[0][0]
    assert system["role"] == "system"
    assert json.dumps(schema) in system["content"]
    assert result.parsed == {"a": 1}
    assert result.usage == {"input_tokens": 7, "output_tokens": 3}
    assert result.finish_reason == "stop"
    assert result.model == "chat-test"
    LangChainTextClient(chat).complete([{"role": "user", "content": "hi"}])
    assert chat.sent[1] == [{"role": "user", "content": "hi"}]


def test_parsed_must_match_the_top_level_schema() -> None:
    schema = {"type": "object", "required": ["ok"]}
    for text in ["[1, 2]", '{"other": 1}']:
        result = LangChainTextClient(FakeListChatModel(responses=[text])).complete([], schema=schema)
        assert result.parsed is None
        assert result.error is not None
        assert result.error.startswith("reply does not match the schema")


def test_langchain_string_parts_are_joined() -> None:
    class StrParts(RecordingChat):
        def invoke(self, messages: Any) -> AIMessage:
            return AIMessage(content=["{", '"ok": true}'])

    assert LangChainTextClient(StrParts([])).complete([], schema={"type": "object"}).parsed == {"ok": True}
