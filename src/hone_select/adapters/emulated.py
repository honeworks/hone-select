"""A ``DecisionClient`` emulated over any ``TextClient``: all questions in one prompt, answers as JSON.

Used by the OpenAI and LangChain adapters; works with any object that has ``complete(messages, schema=)``.
Probabilities are not calibrated (``calibrated=False``): a chosen option gets ``value=1.0``.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from hone_select.ports import Question, TextClient, TextResult, TraceContext, get

DEFAULT_SCALE = (1, 5)  # design/current.md §7.2: a score question without "scale"
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_json(text: str) -> Any:
    """JSON from a model reply: plain, inside ``` fences, or the outermost ``{...}`` in the text."""
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def with_parsed(result: TextResult, schema: Mapping[str, Any] | None) -> TextResult:
    """``result`` with ``parsed`` set from its text when a ``schema`` was asked for, else ``error``.

    Only the top level is checked against the schema (an object with its ``required`` keys).
    """
    if schema is None:
        return result
    try:
        parsed = parse_json(result.text)
    except json.JSONDecodeError as e:
        return dataclasses.replace(result, error=f"invalid JSON: {e}")
    if schema.get("type") == "object":
        missing = [k for k in schema.get("required", []) if not isinstance(parsed, dict) or k not in parsed]
        if not isinstance(parsed, dict) or missing:
            return dataclasses.replace(result, error=f"reply does not match the schema; missing {missing}")
    return dataclasses.replace(result, parsed=parsed)


def answer_schema(questions: Mapping[str, Question]) -> dict[str, Any]:
    """The JSON schema of the reply: ``{name: {"answer": ..., "rationale": str}}`` per question."""
    properties: dict[str, Any] = {}
    for name, q in questions.items():
        if q["type"] == "yes_no":
            answer: dict[str, Any] = {"type": "string", "enum": ["yes", "no"]}
        elif q["type"] == "choice":
            answer = {"type": "string", "enum": list(q.get("options", []))}
        else:
            low, high = q.get("scale", DEFAULT_SCALE)
            answer = {"type": "number", "minimum": low, "maximum": high}
        properties[name] = {
            "type": "object",
            "properties": {"rationale": {"type": "string"}, "answer": answer},
            "required": ["rationale", "answer"],
        }
    return {"type": "object", "properties": properties, "required": list(questions)}


def _describe(name: str, q: Question) -> str:
    if q["type"] == "yes_no":
        kind = 'answer "yes" or "no"'
    elif q["type"] == "choice":
        kind = "answer one of " + ", ".join(json.dumps(o) for o in q.get("options", []))
    else:
        low, high = q.get("scale", DEFAULT_SCALE)
        given: Mapping[str, Any] = q.get("anchors") or {}
        anchors = "; ".join(f"{k} = {v}" for k, v in given.items())
        kind = f"answer a number from {low:g} to {high:g}" + (f" ({anchors})" if anchors else "")
    return f"- {name}: {q['instructions']} ({kind})"


def build_messages(
    state: str | Mapping[str, Any], questions: Mapping[str, Question], images: Sequence[str]
) -> list[dict[str, Any]]:
    """A system prompt with the reply format and a user message with the state and the questions."""
    system = (
        "You are a careful, impartial judge. Read the state and answer every question. Reply with a "
        'JSON object only: {"<question name>": {"rationale": "<one sentence>", "answer": <answer>}, ...}.'
    )
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=2)
    lines = "\n".join(_describe(name, q) for name, q in questions.items())
    content: list[dict[str, Any]] = [{"type": "text", "text": f"State:\n{text}\n\nQuestions:\n{lines}"}]
    content += [{"type": "image", "path": path} for path in images]
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def to_answer(q: Question, reply: Any) -> dict[str, Any]:
    """One ``Answer`` (design/current.md §7.2) from the model's ``{"answer", "rationale"}`` for ``q``."""
    answer: dict[str, Any] = {"type": q["type"], "value": None, "calibrated": False, "error": None}
    if not isinstance(reply, Mapping):
        reply = {}
    raw = get(reply, "answer")
    answer["rationale"] = str(get(reply, "rationale") or "")
    if q["type"] == "yes_no" and raw in ("yes", "no"):
        value = 1.0 if raw == "yes" else 0.0
        answer.update(value=value, probabilities={"yes": value, "no": 1 - value})
    elif q["type"] == "choice" and raw in q.get("options", []):
        answer.update(value=1.0, choice=raw, probabilities={o: float(o == raw) for o in q["options"]})
    elif q["type"] == "score" and isinstance(raw, int | float) and not isinstance(raw, bool):
        low, high = q.get("scale", DEFAULT_SCALE)
        if low <= raw <= high and low < high:
            answer.update(value=(raw - low) / (high - low), raw=float(raw))
    if answer["value"] is None:
        answer["error"] = f"unusable answer {raw!r}"
    return answer


class EmulatedDecisionClient:
    """Answer decision questions with a text model.

    >>> from hone_select.testing import FakeTextClient
    >>> client = EmulatedDecisionClient(FakeTextClient(['{"q": {"answer": "yes", "rationale": "blue"}}']))
    >>> client.decide("The sky is blue.", {"q": {"type": "yes_no", "instructions": "Blue?"}})["q"]["value"]
    1.0
    """

    def __init__(self, text_client: TextClient, *, model_id: str = "") -> None:
        self.text_client = text_client
        self.model_id = model_id or str(getattr(text_client, "model", "") or "")

    def decide(
        self,
        state: str | Mapping[str, Any],
        questions: Mapping[str, Question],
        *,
        images: Sequence[str] = (),
        trace: TraceContext | None = None,
    ) -> dict[str, dict[str, Any]]:
        messages = build_messages(state, questions, images)
        result = self.text_client.complete(messages, schema=answer_schema(questions), trace=trace)
        parsed, error = get(result, "parsed"), get(result, "error")  # a TextClient parses (design §7.3)
        if not isinstance(parsed, Mapping):
            error = error or f"expected a JSON object, got {parsed!r}"
            return {name: {"type": q["type"], "value": None, "error": error} for name, q in questions.items()}
        return {name: to_answer(q, get(parsed, name)) for name, q in questions.items()}  # pyright: ignore[reportUnknownArgumentType]
