"""Prompt scorers: plain-English criteria answered by any ``DecisionClient`` (an LLM or a decision model)."""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import KW_ONLY, dataclass
from typing import Any, ClassVar

from hone_select._tracing import current_trace
from hone_select.errors import ConfigError, PortError
from hone_select.ports import DecisionClient, Question, get
from hone_select.types import Candidate, GateResult, Score, as_text


def candidate_state(candidate: Candidate, field: str | None) -> str:
    """``candidate.data[field]`` (or all of ``data``) as text; non-strings are JSON-dumped."""
    return as_text(candidate.data if field is None else candidate.data[field])


def image_keys(images_from: str | Sequence[str] | None) -> list[str]:
    """``images_from`` as a list of file keys: ``None`` -> ``[]``, ``"image"`` -> ``["image"]``."""
    if images_from is None:
        return []
    if isinstance(images_from, str):
        return [images_from]
    return [str(key) for key in images_from]


def candidate_images(candidate: Candidate, images_from: str | Sequence[str] | None) -> list[str]:
    """The paths of ``candidate.files`` named by ``images_from``, in that order (design change 0005)."""
    paths: list[str] = []
    for key in image_keys(images_from):
        if key not in candidate.files:
            raise ValueError(
                f"candidate {candidate.id} has no file {key!r} (images_from); its files: "
                f"{sorted(candidate.files)}. Put every image the judge needs, including a shared "
                "reference, into Candidate.files"
            )
        paths.append(candidate.files[key])
    return paths


def judge_model(judge: object) -> str:
    """The judge's model id, read from ``model_id`` or ``model`` if it has one (else ``""``)."""
    return str(getattr(judge, "model_id", "") or getattr(judge, "model", "") or "")


def model_family(model: str) -> str:
    """The leading letters of a model id: ``"gemma4-12b"`` -> ``"gemma"``, ``"gpt-4.1-mini"`` -> ``"gpt"``."""
    match = re.match(r"[a-z]+", model.lower().rsplit("/", 1)[-1])
    return match.group(0) if match else ""


def _ask(
    judge: DecisionClient | str,
    name: str,
    state: str | Mapping[str, Any],
    questions: Mapping[str, Question],
    images: Sequence[str] = (),
) -> Mapping[str, Any]:
    """Ask ``judge`` inside the current trace; a judge still given by name is a config error."""
    if isinstance(judge, str):
        raise ConfigError(
            f"judge {judge!r} of {name!r} is not resolved; pass the scorer to Engine(registry=...) with "
            f"judges={{{judge!r}: client}} or a [judges.{judge}] config section"
        )
    return judge.decide(state, questions, images=images, trace=current_trace())


def answer_value(answer: Any, question: Question) -> tuple[float | None, str]:
    """``(value 0..1, error)`` from one answer; a ``score`` answer with only ``raw`` is normalized."""
    if answer is None:
        return None, "not answered"
    value, raw, error = get(answer, "value"), get(answer, "raw"), get(answer, "error")
    if value is None and raw is not None and not error and question["type"] == "score":
        low, high = question.get("scale", (1, 5))
        value = (float(raw) - low) / (high - low)
    if value is None:
        return None, str(error or "answer has no value")
    if not 0.0 <= float(value) <= 1.0:  # also rejects NaN
        return None, f"value {value} is outside 0..1"
    return float(value), ""


@dataclass(frozen=True)
class PromptScorer:
    """Score candidates against criteria with a judge. A list of criteria is a checklist (mean of items).

    >>> from hone_select.testing import FakeDecisionClient
    >>> s = PromptScorer("clear", "Is it clear?", FakeDecisionClient(answers={"clear": 0.75}))
    >>> s(Candidate.of("text")).value
    0.75
    """

    name: str
    criteria: str | Sequence[str]
    judge: DecisionClient | str
    _: KW_ONLY
    output: str = "score"  # "score" | "yes_no"
    scale: tuple[float, float] = (1, 5)
    anchors: Mapping[str, str] | None = None
    field: str | None = None
    images_from: str | Sequence[str] | None = None  # file keys sent as images, in order
    cost: float = 5.0
    version: str = "1"
    kind: ClassVar[str] = "scorer"

    def questions(self) -> dict[str, Question]:
        if isinstance(self.criteria, str):
            texts = {self.name: self.criteria}
        else:
            texts = {f"{self.name}_{i}": text for i, text in enumerate(self.criteria, start=1)}
        questions: dict[str, Question] = {}
        for key, text in texts.items():
            question: dict[str, Any] = {"type": self.output, "instructions": text}
            if self.output == "score":
                question["scale"] = list(self.scale)
                if self.anchors:
                    question["anchors"] = dict(self.anchors)
            questions[key] = question
        return questions

    def __call__(self, candidate: Candidate) -> Score:
        questions = self.questions()
        images = candidate_images(candidate, self.images_from)
        answers = _ask(self.judge, self.name, candidate_state(candidate, self.field), questions, images)
        values: dict[str, float | None] = {}
        errors: list[str] = []
        for key, question in questions.items():
            values[key], error = answer_value(answers.get(key), question)
            if error:
                errors.append(f"{key}: {error}")
        present = [v for v in values.values() if v is not None]
        if not present:
            return Score(None, details=values, error="; ".join(errors))
        confidences = [c for key in values if (c := get(answers.get(key), "confidence")) is not None]
        reason = str(get(answers.get(self.name), "rationale") or "")
        # a checklist with some unanswered items keeps the mean of the rest, and says so in `error`
        return Score(
            statistics.fmean(present), min(confidences, default=None), reason, values, "; ".join(errors)
        )


@dataclass(frozen=True)
class PromptGate:
    """Pass a candidate when the judge's probability of "yes" reaches ``threshold``."""

    name: str
    criteria: str
    judge: DecisionClient | str
    _: KW_ONLY
    threshold: float = 0.5
    field: str | None = None
    cost: float = 1.0
    version: str = "1"
    kind: ClassVar[str] = "gate"

    def __call__(self, candidate: Candidate) -> GateResult:
        question: Question = {"type": "yes_no", "instructions": self.criteria}
        answers = _ask(self.judge, self.name, candidate_state(candidate, self.field), {self.name: question})
        answer = answers.get(self.name)
        value, error = answer_value(answer, question)
        if value is None:
            raise PortError(f"judge could not answer gate {self.name!r}: {error}")
        return GateResult(value >= self.threshold, value, str(get(answer, "rationale") or ""))


@dataclass(frozen=True)
class PromptPairwise:
    """Ask the judge which of two candidates better meets the criteria (options "A", "B", "tie").

    The engine asks in both orders, (A, B) and (B, A); a win counts only when both orders agree.
    ``images_from`` sends A's images, then B's (design change 0007); a file both share (the same path,
    e.g. a reference sheet) is sent once, first.
    """

    name: str
    criteria: str
    judge: DecisionClient | str
    _: KW_ONLY
    field: str | None = None
    images_from: str | Sequence[str] | None = None
    cost: float = 10.0
    version: str = "1"
    kind: ClassVar[str] = "pairwise"

    def __call__(self, a: Candidate, b: Candidate) -> tuple[str, float | None]:
        question: Question = {"type": "choice", "instructions": self.criteria, "options": ["A", "B", "tie"]}
        state = {"A": candidate_state(a, self.field), "B": candidate_state(b, self.field)}
        images = list(
            dict.fromkeys([*candidate_images(a, self.images_from), *candidate_images(b, self.images_from)])
        )
        answer = _ask(self.judge, self.name, state, {self.name: question}, images).get(self.name)
        choice = get(answer, "choice")
        if choice not in ("A", "B", "tie"):
            raise PortError(
                f"judge gave no usable choice for {self.name!r}: {get(answer, 'error') or choice!r}"
            )
        return str(choice).lower(), get(answer, "confidence")
