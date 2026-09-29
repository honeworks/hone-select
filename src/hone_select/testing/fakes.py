"""Deterministic, scriptable fakes for every port. Each records its calls in ``.calls``."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hone_select.ports import Question, TextResult

Rule = Callable[[Any, Mapping[str, Question]], Mapping[str, Any]]


def _answer(question: Question, scripted: Any) -> dict[str, Any]:
    """Build a full answer. ``scripted`` may be None (default), a value 0..1, an option / "yes" / "no",
    or a dict that overrides fields of the answer."""
    kind = question["type"]
    answer: dict[str, Any] = {
        "type": kind,
        "value": 0.5,
        "choice": None,
        "probabilities": None,
        "raw": None,
        "confidence": None,
        "calibrated": False,
        "rationale": "fake",
        "error": None,
    }
    if kind == "choice":
        options = list(question.get("options", []))
        choice = scripted if isinstance(scripted, str) else options[0]
        answer.update(choice=choice, value=1.0, probabilities={o: float(o == choice) for o in options})
    elif isinstance(scripted, str):  # yes_no
        answer["value"] = 1.0 if scripted == "yes" else 0.0
    elif isinstance(scripted, int | float):
        answer["value"] = float(scripted)
    if isinstance(scripted, Mapping):
        answer.update(scripted)  # pyright: ignore[reportUnknownArgumentType]
    value = answer["value"]
    if kind == "yes_no" and value is not None:
        answer["probabilities"] = {"yes": value, "no": 1 - value}
    if kind == "score" and value is not None and answer["raw"] is None:
        low, high = question.get("scale", (1, 5))
        answer["raw"] = low + value * (high - low)
    return answer


class FakeDecisionClient:
    """A ``DecisionClient`` answering from a script.

    ``answers`` maps question name -> value, option, or answer dict. ``rule(state, questions)`` returns
    such a mapping per call instead. Unscripted questions get 0.5 (``choice``: the first option).

    >>> fake = FakeDecisionClient(answers={"q": 0.9})
    >>> fake.decide("text", {"q": {"type": "yes_no", "instructions": "ok?"}})["q"]["value"]
    0.9
    """

    def __init__(
        self,
        answers: Mapping[str, Any] | None = None,
        *,
        rule: Rule | None = None,
        model_id: str = "fake-judge",
    ) -> None:
        self.answers = dict(answers or {})
        self.rule = rule
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    def decide(
        self,
        state: str | Mapping[str, Any],
        questions: Mapping[str, Question],
        *,
        images: Sequence[str] = (),
        trace: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"state": state, "questions": dict(questions), "images": list(images), "trace": dict(trace or {})}
        )
        script = self.rule(state, questions) if self.rule else self.answers
        return {name: _answer(question, script.get(name)) for name, question in questions.items()}


class FakeTextClient:
    """A ``TextClient`` returning ``responses`` in turn; with a schema the text is parsed as JSON."""

    def __init__(self, responses: Sequence[str] = ('{"ok": true}',), *, model: str = "fake-text") -> None:
        self.responses = list(responses)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: Mapping[str, str] | None = None,
        **params: Any,
    ) -> TextResult:
        self.calls.append(
            {"messages": list(messages), "schema": schema, "trace": dict(trace or {}), "params": params}
        )
        text = self.responses[(len(self.calls) - 1) % len(self.responses)]
        if schema is None:
            return TextResult(text=text, model=self.model, finish_reason="stop")
        try:
            return TextResult(text=text, parsed=json.loads(text), model=self.model, finish_reason="stop")
        except json.JSONDecodeError as e:
            return TextResult(text=text, error=f"invalid JSON: {e}", model=self.model, finish_reason="stop")


class FakeEmbedder:
    """An ``Embedder`` with stable hash vectors: different texts are not similar unless scripted.

    ``similar={"b": "a"}`` makes "b" embed exactly like "a" (cosine 1.0).
    """

    def __init__(self, similar: Mapping[str, str] | None = None, *, dimensions: int = 32) -> None:
        self.similar = dict(similar or {})
        self.dimensions = dimensions
        self.model_id = "fake-embedder"
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, trace: Mapping[str, str] | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(self.similar.get(t, t)) for t in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest() * (self.dimensions // 32 + 1)
        raw = [b / 127.5 - 1.0 for b in digest[: self.dimensions]]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


def _machine_snapshot() -> dict[str, Any]:
    """A quiet machine: one 8 GB GPU, idle, Ollama running with nothing loaded, the lock free."""
    gpu: dict[str, Any] = {
        "index": 0,
        "name": "Fake GPU",
        "memory_total_gb": 8.0,
        "memory_used_gb": 0.0,
        "memory_free_gb": 8.0,
        "utilization_pct": 0,
        "processes": [],
    }
    return {
        "time": "2026-01-01T00:00:00Z",
        "gpus": [gpu],
        "servers": [{"server": "ollama", "running": True, "error": None}],
        "gpu_lock": {"path": None, "held": False, "holder": None, "mine": False},
        "leases": [],
    }


class FakeMachineProbe:
    """A ``MachineProbe`` (design change 0010 §6) with a live list of loaded models.

    ``snapshots`` are scripted answers of ``snapshot()``, one per call, repeating the last (default: a quiet
    machine); ``loaded_models`` comes from the live list ``loaded`` unless a scripted snapshot sets it.
    ``prepare`` records ``needed`` in ``calls`` and unloads every loaded model that is not needed, except
    while ``blocked_by`` is set and ``if_busy`` is ``"block"``; a model named in ``errors`` fails to unload.
    ``raises`` makes ``snapshot`` and ``prepare`` raise it. ``loads`` (model id -> answer) adds a ``load``
    method; an answer with ``loaded: True`` adds the model to ``loaded``. Change the attributes between
    calls to script a machine that changes.
    """

    def __init__(
        self,
        snapshots: Sequence[Mapping[str, Any]] | None = None,
        *,
        loaded: Sequence[Mapping[str, Any]] = (),
        blocked_by: Sequence[Any] | None = None,
        errors: Mapping[str, str] | None = None,
        raises: Exception | None = None,
        loads: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.snapshots = [dict(s) for s in snapshots or [{}]]
        self.loaded = [dict(m) for m in loaded]
        self.blocked_by = list(blocked_by) if blocked_by is not None else None
        self.errors = dict(errors or {})
        self.raises = raises
        self.loads = dict(loads) if loads is not None else None
        self.calls: list[dict[str, Any]] = []
        if loads is not None:
            self.load = self._load

    def snapshot(self) -> dict[str, Any]:
        count = sum(c["call"] == "snapshot" for c in self.calls)
        self.calls.append({"call": "snapshot"})
        if self.raises is not None:
            raise self.raises
        scripted = self.snapshots[min(count, len(self.snapshots) - 1)]
        return {**_machine_snapshot(), "loaded_models": [dict(m) for m in self.loaded], **scripted}

    def prepare(self, needed: Sequence[str], *, if_busy: str = "block") -> dict[str, Any]:
        self.calls.append({"call": "prepare", "needed": list(needed), "if_busy": if_busy})
        if self.raises is not None:
            raise self.raises
        blocked = self.blocked_by is not None and if_busy == "block"
        unneeded = [m for m in self.loaded if m.get("model_id") not in needed]
        failed = [m for m in unneeded if m.get("name") in self.errors]
        unloaded = [] if blocked else [m for m in unneeded if m not in failed]
        self.loaded = [m for m in self.loaded if m not in unloaded]
        ids = [m.get("model_id") for m in self.loaded]
        sizes = [m.get("size_gb") or 0.0 for m in self.loaded if m.get("model_id") in needed]
        return {
            "needed": list(needed),
            "if_busy": if_busy,
            "blocked_by": self.blocked_by,
            "unloaded": [{"server": m.get("server"), "name": m.get("name")} for m in unloaded],
            "released": [],
            "errors": []
            if blocked
            else [
                {"server": m.get("server"), "name": m.get("name"), "error": self.errors[m["name"]]}
                for m in failed
            ],
            "missing": [n for n in needed if n not in ids],
            "loaded_models": [dict(m) for m in self.loaded],
            "need_gb": sum(sizes),
        }

    def _load(self, model_id: str) -> dict[str, Any]:
        self.calls.append({"call": "load", "model_id": model_id})
        answer: dict[str, Any] = {
            "model_id": model_id,
            "loaded": True,
            "seconds": 1.0,
            "size_gb": 4.0,
            "vram_gb": 4.0,
        }
        answer |= dict((self.loads or {}).get(model_id, {}))
        answer.setdefault("error", None)
        if answer["loaded"] is True:
            self.loaded.append(
                {
                    "server": "ollama",
                    "name": model_id,
                    "model_id": model_id,
                    "size_gb": answer["size_gb"],
                    "vram_gb": answer["vram_gb"],
                }
            )
        return answer
