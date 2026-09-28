"""Bring your own client: plug an OpenAI-style SDK client, or a judge you write yourself, into the port.

What: (1) OpenAIDecisionClient wrapping an object shaped like the OpenAI SDK - here a local stub, so the
      example runs offline - and the request it receives; (2) a DecisionClient written from scratch,
      checked with the public contract checker before use.
How:  1. real use: OpenAIDecisionClient(OpenAI(), model="gpt-4.1-mini") (extra hone-select[openai]), or
         OpenAI(base_url="http://localhost:11434/v1", api_key="ollama") for Ollama. The adapter only
         calls client.chat.completions.create(...), so anything with that shape works;
      2. your own judge: a class with decide(state, questions, *, images=(), trace=None) returning
         {question name: answer dict} (type, value 0..1, choice, confidence, error, ...);
      3. run hone_select.testing.contracts.check_decision_client(judge) in your tests;
      4. hand either to PromptScorer / PromptGate / PromptPairwise, or to Engine(judges={"name": judge}).
Why:  hone-select owns the DecisionClient port and never imports a model library; any client, local or
      hosted, fits through it. Pitfall: an answer the judge cannot give is value None plus error, never a
      made-up 0 - the scorer then returns Score(None, error=...).
"""

import json
from types import SimpleNamespace

from hone_select import Candidate, Engine, PromptScorer
from hone_select.adapters.openai import OpenAIDecisionClient
from hone_select.testing import contracts


# --- 1. An object shaped like openai.OpenAI(): client.chat.completions.create(**request) ---------------
class StubCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        schema = request["response_format"]["json_schema"]["schema"]  # the adapter asks for JSON
        reply = {}
        for name, question in schema["properties"].items():
            answer = question["properties"]["answer"]  # enum for yes/no and choices, number for scores
            reply[name] = {"rationale": "stub", "answer": answer["enum"][0] if "enum" in answer else 4}
        message = SimpleNamespace(content=json.dumps(reply))
        return SimpleNamespace(
            model=request["model"],
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
        )


stub = SimpleNamespace(chat=SimpleNamespace(completions=StubCompletions()))
# with the real SDK: judge = OpenAIDecisionClient(OpenAI(), model="gpt-4.1-mini")
judge = OpenAIDecisionClient(stub, model="gpt-4.1-mini")

vivid = PromptScorer("vivid", "The line paints a concrete picture.", judge, field="text")
score = vivid(Candidate.of({"text": "rain on a tin roof at midnight"}))
request = stub.chat.completions.requests[-1]
print("score:", score.value, "| request keys:", sorted(request))
assert score.value == 0.75  # answer 4 on the 1..5 scale
assert request["model"] == "gpt-4.1-mini" and request["temperature"] == 0  # judges default to temperature 0
assert request["response_format"]["type"] == "json_schema"


# --- 2. A DecisionClient written from scratch ---------------------------------------------------------
class KeywordJudge:
    """Answers every question by counting keywords in the state. model_id names it in the score cache."""

    model_id = "keyword-judge-v1"

    def __init__(self, keywords):
        self.keywords = keywords

    def decide(self, state, questions, *, images=(), trace=None):
        text = state if isinstance(state, str) else json.dumps(state)
        hits = sum(word in text for word in self.keywords) / len(self.keywords)
        answers = {}
        for name, question in questions.items():
            answer = {"type": question["type"], "value": hits, "confidence": 1.0, "calibrated": False}
            if question["type"] == "choice":
                answer.update(value=1.0, choice=question["options"][0])  # this judge cannot compare
            answers[name] = answer
        return answers


keyword_judge = KeywordJudge(["rain", "roof", "night"])
contracts.check_decision_client(keyword_judge)  # raises AssertionError if the port is not honoured

imagery = PromptScorer("imagery", "Counts weather and night images.", keyword_judge, field="text")
engine = Engine('[score]\ncascade = [{ scorers = ["imagery"] }]', registry=[imagery])
ranked = engine.score([Candidate.of({"text": t}) for t in ["sunny day", "rain on the roof tonight"]])
print("ranked:", [(s.candidate.data["text"], round(s.total, 3)) for s in ranked])
assert ranked[0].candidate.data["text"] == "rain on the roof tonight"
