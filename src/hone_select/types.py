"""The data types that flow through a selection: candidates, scores, gate results and results."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict


class Variation(TypedDict):
    """What a generator receives for one candidate: its position, seed and sampled params."""

    index: int
    seed: int
    params: dict[str, Any]


def _json_default(value: Any) -> Any:
    """Stable stand-ins for common non-JSON values (sets are sorted; set order varies per process)."""
    if isinstance(value, set | frozenset):
        return sorted(canonical_json(v) for v in value)  # pyright: ignore[reportUnknownVariableType]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return str(value)


def canonical_json(value: Any) -> str:
    """Stable JSON text: sorted keys, no spaces; sets sorted, dataclasses as dicts, others via ``str``."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


def candidate_id(data: Any, files: Mapping[str, str]) -> str:
    """First 16 hex chars of sha256 over the canonical JSON of ``data`` plus each file's content hash."""
    file_hashes = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in files.items()}
    text = canonical_json({"data": data, "files": file_hashes})
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def as_text(value: Any) -> str:
    """A string as is; anything else as canonical JSON."""
    return value if isinstance(value, str) else canonical_json(value)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One generated output. Build it with ``Candidate.of(data)`` so the id is computed for you.

    >>> Candidate.of("hello").id == Candidate.of("hello").id
    True
    """

    id: str
    data: Any
    files: Mapping[str, str] = field(default_factory=dict[str, str])
    meta: Mapping[str, Any] = field(default_factory=dict[str, Any])

    @classmethod
    def of(
        cls, data: Any, files: Mapping[str, str] | None = None, meta: Mapping[str, Any] | None = None
    ) -> Candidate:
        files = dict(files or {})
        return cls(id=candidate_id(data, files), data=data, files=files, meta=dict(meta or {}))


@dataclass(frozen=True, slots=True)
class Score:
    """A scorer's verdict: ``value`` in 0..1 (higher is better), or ``None`` when it could not score."""

    value: float | None
    confidence: float | None = None
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict[str, Any])
    error: str = ""


@dataclass(frozen=True, slots=True)
class GateResult:
    """A gate's verdict: pass or reject."""

    passed: bool
    probability: float | None = None
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict[str, Any])  # e.g. per-item verdicts (0004)


@dataclass(slots=True)
class Scored:
    """A candidate with everything learned about it during selection."""

    candidate: Candidate
    gates: dict[str, GateResult]
    scores: dict[str, Score]
    total: float | None
    rejected: bool
    stage_reached: int


@dataclass(slots=True)
class Result:
    """The outcome of ``Engine.run`` or ``Engine.select``: the winner, everyone ranked, the decision trace."""

    winner: Scored | None
    ranked: list[Scored]
    decision: list[dict[str, Any]]
    run_id: str
    trace_id: str
    budget: dict[str, float]


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a Mapping or an attribute-style object (design/current.md §7)."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return getattr(obj, key, default)


def to_score(value: Any) -> Score:
    """float -> Score; None -> Score(None); Score or any ScoreLike (design/current.md §7.7) -> Score.

    A value outside 0..1 becomes ``Score(None, error=...)``: it cannot be trusted as a score.
    """
    if value is None:
        return Score(None)
    if isinstance(value, Score):
        score = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        score = Score(float(value))
    elif hasattr(value, "value") or (isinstance(value, Mapping) and "value" in value):
        raw = get(value, "value")
        score = Score(
            value=None if raw is None else float(raw),
            confidence=get(value, "confidence"),
            reason=str(get(value, "reason", "") or ""),
            details=dict(get(value, "details", None) or {}),
            error=str(get(value, "error", "") or ""),
        )
    else:
        raise TypeError(f"a scorer must return float, Score, None or a ScoreLike with `value`; got {value!r}")
    for label, v in (("value", score.value), ("confidence", score.confidence)):
        if v is not None and (math.isnan(v) or not 0.0 <= v <= 1.0):
            return Score(None, None, score.reason, score.details, f"{label} {v} is outside 0..1")
    return score


def to_gate_result(value: Any) -> GateResult:
    """bool -> GateResult; GateResult or any GateLike (``passed``, ``probability``, ``reason``,
    ``details``) as is."""
    if isinstance(value, GateResult):
        return value
    if isinstance(value, bool):
        return GateResult(value)
    passed = get(value, "passed")
    if not isinstance(passed, bool):
        raise TypeError(f"a gate must return bool or GateResult, got {value!r}")
    return GateResult(
        passed,
        get(value, "probability"),
        str(get(value, "reason", "") or ""),
        dict(get(value, "details", None) or {}),
    )
