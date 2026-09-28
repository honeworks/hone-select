"""AC-19: the OpenAI adapter passes the contract checkers against recorded HTTP replies and parses the
emulated decision JSON (plain, fenced, truncated, empty, out-of-range), and transport errors raise."""

import json
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
import respx

from hone_select import Candidate, Engine, PromptPairwise, PromptScorer, generator
from hone_select.adapters.openai import OpenAIDecisionClient, OpenAITextClient
from hone_select.testing import contracts

pytestmark = pytest.mark.e2e

BASE = "http://llm.test/v1"
URL = f"{BASE}/chat/completions"
HTTP = Path(__file__).parent.parent / "fixtures" / "http"
QUESTIONS: dict[str, Any] = {
    "q1": {"type": "yes_no", "instructions": "Is the sky described as blue?"},
    "q2": {"type": "choice", "instructions": "Colour?", "options": ["blue", "red"]},
    "q3": {"type": "score", "instructions": "How vivid?", "scale": [1, 5]},
}


def reply(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=json.loads((HTTP / name).read_text()))


def client() -> openai.OpenAI:
    # openai>=3 talks through `httpx2` by default, which respx cannot see; the SDK still accepts a plain
    # httpx client (its annotation says httpx2), and that one respx intercepts.
    http = httpx.Client()
    return openai.OpenAI(base_url=BASE, api_key="test-key", max_retries=0, http_client=http)  # pyright: ignore[reportArgumentType]


@respx.mock
def test_ac19_contract_checkers_pass() -> None:
    route = respx.post(URL).mock(return_value=reply("decision_ok.json"))
    contracts.check_decision_client(OpenAIDecisionClient(client(), model="gpt-4.1-mini"))
    route.mock(return_value=reply("text_ok.json"))
    contracts.check_text_client(OpenAITextClient(client(), model="gpt-4.1-mini"))


@respx.mock
def test_ac19_decision_emulation_parses_json_answers() -> None:
    route = respx.post(URL).mock(return_value=reply("decision_ok.json"))
    judge = OpenAIDecisionClient(client(), model="gpt-4.1-mini", seed=7)
    answers = judge.decide("The sky is blue.", QUESTIONS)
    assert answers["q1"]["value"] == 1.0
    assert answers["q1"]["probabilities"] == {"yes": 1.0, "no": 0.0}
    assert answers["q2"]["choice"] == "blue"
    assert answers["q3"]["value"] == 0.75
    assert answers["q3"]["raw"] == 4.0
    assert all(a["calibrated"] is False and a["error"] is None for a in answers.values())
    request = json.loads(route.calls.last.request.content)
    assert request["model"] == "gpt-4.1-mini"
    assert request["temperature"] == 0
    assert request["seed"] == 7
    assert request["response_format"]["type"] == "json_schema"
    assert set(request["response_format"]["json_schema"]["schema"]["properties"]) == set(QUESTIONS)
    assert "The sky is blue." in json.dumps(request["messages"])


@respx.mock
def test_ac19_fenced_json_is_parsed() -> None:
    respx.post(URL).mock(return_value=reply("decision_fenced.json"))
    answers = OpenAIDecisionClient(client(), model="m").decide("The sky is blue.", QUESTIONS)
    assert [answers[q]["value"] for q in QUESTIONS] == [1.0, 1.0, 0.75]


@pytest.mark.parametrize("fixture", ["decision_truncated.json", "decision_empty_thinking.json"])
@respx.mock
def test_ac19_unparsable_reply_is_an_error_per_question(fixture: str) -> None:
    respx.post(URL).mock(return_value=reply(fixture))
    answers = OpenAIDecisionClient(client(), model="m").decide("x", QUESTIONS)
    assert set(answers) == set(QUESTIONS)
    assert all(a["value"] is None and "invalid JSON" in a["error"] for a in answers.values())


@respx.mock
def test_ac19_out_of_range_answers_are_errors_not_zero() -> None:
    respx.post(URL).mock(return_value=reply("decision_bad_values.json"))
    answers = OpenAIDecisionClient(client(), model="m").decide("x", QUESTIONS)
    assert all(a["value"] is None and a["error"].startswith("unusable answer") for a in answers.values())


@pytest.mark.parametrize(
    ("fixture", "status", "error"),
    [("error_400.json", 400, openai.BadRequestError), ("error_429.json", 429, openai.RateLimitError)],
)
@respx.mock
def test_ac19_transport_errors_raise(fixture: str, status: int, error: type[Exception]) -> None:
    respx.post(URL).mock(return_value=reply(fixture, status))
    with pytest.raises(error):
        OpenAIDecisionClient(client(), model="nope").decide("x", QUESTIONS)


@respx.mock
def test_ac19_timeout_becomes_a_missing_score() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    @generator()
    def write(task, v):
        return Candidate.of({"lyrics": f"{task} {v['index']}"})

    judge = OpenAIDecisionClient(client(), model="m")
    singable = PromptScorer("singable", "Easy to sing back.", judge, field="lyrics")
    config = "[generate]\nn = 2\n[score]\ncascade = [{ scorers = ['singable'] }]\n[select]\nfallback = 'best_rejected'"
    result = Engine(config, registry=[write, singable], cache=None).run("la")
    score = result.ranked[0].scores["singable"]
    assert score.value is None
    assert "APITimeoutError" in (score.error or "")


def judge_by_word(request: httpx.Request) -> httpx.Response:
    """Score 5 when the state says "good", else 1; in pairwise, pick the side that says "good"."""
    sent = json.loads(request.content)
    state = sent["messages"][-1]["content"][0]["text"].split("Questions:")[0]
    (name, prop), *_ = sent["response_format"]["json_schema"]["schema"]["properties"].items()
    if prop["properties"]["answer"]["type"] == "number":
        value: Any = 5 if "good" in state else 1
    else:
        value = "A" if state.index('"A"') < state.index("good") < state.index('"B"') else "B"
    body = json.loads((HTTP / "text_ok.json").read_text())
    body["choices"][0]["message"]["content"] = json.dumps({name: {"rationale": "r", "answer": value}})
    return httpx.Response(200, json=body)


@respx.mock
def test_ac19_prompt_scorer_and_pairwise_through_the_adapter() -> None:
    respx.post(URL).mock(side_effect=judge_by_word)
    judge = OpenAIDecisionClient(client(), model="m")
    quality = PromptScorer("quality", "Is it good?", judge)
    config = "[score]\ncascade = [{ scorers = ['quality'] }]"
    ranked = Engine(config, registry=[quality], cache=None).score([Candidate.of("bad"), Candidate.of("good")])
    assert [r.candidate.data for r in ranked] == ["good", "bad"]
    assert [r.scores["quality"].value for r in ranked] == [1.0, 0.0]
    better = PromptPairwise("better", "Which is better?", judge)
    good, bad = Candidate.of("good"), Candidate.of("bad")
    assert better(good, bad)[0] == "a"
    assert better(bad, good)[0] == "b"


@respx.mock
def test_ac19_text_client_maps_the_reply_and_params() -> None:
    route = respx.post(URL).mock(return_value=reply("text_ok.json"))
    text = OpenAITextClient(client(), model="gpt-4.1-mini", temperature=0.7, max_tokens=50)
    result = text.complete([{"role": "user", "content": "hi"}], temperature=0, think=False)
    sent = json.loads(route.calls.last.request.content)
    assert (sent["temperature"], sent["max_tokens"]) == (0, 50)
    assert "think" not in sent
    assert "response_format" not in sent
    assert result.text == '{"ok": true}'
    assert result.parsed is None  # no schema asked, nothing parsed
    assert result.model == "gpt-4.1-mini-2025-04-14"
    assert result.finish_reason == "stop"
    assert result.usage == {"input_tokens": 120, "output_tokens": 40}


@respx.mock
def test_ac19_temperature_none_is_left_out() -> None:
    route = respx.post(URL).mock(return_value=reply("decision_ok.json"))
    OpenAIDecisionClient(client(), model="o-reasoner", temperature=None).decide("The sky is blue.", QUESTIONS)
    assert "temperature" not in json.loads(route.calls.last.request.content)
