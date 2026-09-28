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
