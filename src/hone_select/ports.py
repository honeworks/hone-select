"""Protocols this package owns (design/current.md §7; ports version 1).

Adapters implement them structurally; nothing here imports another package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from hone_select.types import Score, get

PORTS_VERSION = "1"
__all__ = [
    "PORTS_VERSION",
    "Answer",
    "DecisionClient",
    "Embedder",
    "MachineProbe",
    "ModelGuides",
    "Question",
    "RecordSink",
    "ScoreCache",
    "TextClient",
    "TextResult",
    "TraceContext",
    "get",
]

TraceContext = Mapping[str, str]
Question = Mapping[str, Any]  # {"type": "yes_no" | "choice" | "score", "instructions": ..., ...}
Answer = Any  # Mapping or attribute object with value, choice, confidence, error, ...


class DecisionClient(Protocol):
    """Answers structured questions about a state (design/current.md §7.2)."""

    def decide(
        self,
        state: str | Mapping[str, Any],
        questions: Mapping[str, Question],
        *,
        images: Sequence[str] = (),
        trace: TraceContext | None = None,
    ) -> Mapping[str, Answer]: ...


@dataclass(frozen=True, slots=True)
class TextResult:
    """What a ``TextClient`` returns (design/current.md §7.3). Any object with these attributes also fits."""

    text: str
    parsed: Any = None
    error: str | None = None
    model: str = ""
    finish_reason: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict[str, int])
    span_id: str | None = None


class TextClient(Protocol):
    """Prompt in, text or structured object out (design/current.md §7.3)."""

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> Any: ...


class Embedder(Protocol):
    """Turns texts into L2-normalized vectors (design/current.md §7.4)."""

    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str], *, trace: TraceContext | None = None) -> list[list[float]]: ...


class RecordSink(Protocol):
    """Receives finished spans (design/current.md §7.5). Must never raise into the caller."""

    def emit(self, span: Mapping[str, Any]) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


class ScoreCache(Protocol):
    """Stores scores by (candidate id, scorer name, scorer version, judge id)."""

    def get(self, key: tuple[str, str, str, str]) -> Score | None: ...
    def put(self, key: tuple[str, str, str, str], score: Score) -> None: ...


class MachineProbe(Protocol):
    """The machine's model state for experiment run conditions (design change 0010 §6), provided by
    hone-models (``hone_models.machine.Machine``); any object with this shape fits. Every key of the answers
    is optional, and an unknown value is ``None``, never 0.

    ``snapshot()`` returns ``{"time", "gpus": [{"index", "name", "memory_total_gb", "memory_used_gb",
    "memory_free_gb", "utilization_pct", "processes": [{"pid", "name", "memory_gb"}]}] | None,
    "servers": [{"server", "running": True | False | None, "error"}], "loaded_models": [{"server", "name",
    "model_id", "size_gb", "vram_gb"}], "gpu_lock": {"path", "held", "holder", "mine"},
    "leases": [{"name", "pid", "gb", "mine"}]}``.

    ``prepare(needed, if_busy=...)`` makes sure only ``needed`` (registry ids) are loaded: it unloads the
    others, never loads one and never waits; with ``if_busy="block"`` it unloads nothing while another
    process holds a lease or the lock. It returns ``{"needed", "if_busy", "blocked_by", "unloaded",
    "released", "errors", "missing", "loaded_models", "need_gb"}``.

    Optional (checked with ``hasattr``): ``load(model_id)`` warms a model up and returns ``{"model_id",
    "loaded": True | False | None, "seconds", "size_gb", "vram_gb", "error"}``.
    """

    def snapshot(self) -> Mapping[str, Any]: ...
    def prepare(self, needed: Sequence[str], *, if_busy: str = "block") -> Mapping[str, Any]: ...


class ModelGuides(Protocol):
    """What each model can take (design change 0011 §5), provided by hone-models (``mk.guide``); any object
    with this shape fits.

    ``guide(model_id)`` returns the model's guide as JSON, or ``None`` for a model the source does not know:
    ``{"id", "kind", "summary", "prompt", "inputs", "features": [{"name", "how", "input", "examples",
    "source"}], "source", "checked", "license", "commercial_use", "sizes", "durations_s", "max_duration_s",
    "max_references", "installed": "yes" | "no" | "unknown", "install": "<command to run>"}``. Every key is
    optional; an unknown value is ``None``, never 0.
    """

    def guide(self, model_id: str) -> Mapping[str, Any] | None: ...
