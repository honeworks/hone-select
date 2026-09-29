"""The scoring phase of an experiment (design change 0009 §4): one hone-select selection per case over its
samples. The implicit gate `ran_ok` rejects failed samples; `measure` criteria are normalized over the
whole experiment; each selection is recorded with the experiment id and case in the trace context.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from hone_select.config import SelectionConfig
from hone_select.engine import Engine
from hone_select.errors import ConfigError
from hone_select.experiments import definition as d
from hone_select.experiments.outside import EXCLUDED, SPEED, environment_status, is_cold
from hone_select.experiments.project import read_json, write_json
from hone_select.registry import gate, module_items, scorer
from hone_select.types import Candidate, GateResult, Score, Scored

SMALL = 20 * 1024 * 1024  # keep_files = "small": files above this are hashed, then deleted


def _registry(spec: d.ExperimentSpec, root: Path) -> list[Any]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return [item for name in spec.registry for item in module_items(name)]


def _selection_config(spec: d.ExperimentSpec, root: Path) -> SelectionConfig:
    scorers = [s for s in spec.criteria.scorers if s not in spec.human_scorers()] + list(
        spec.criteria.measure
    )
    machine = {n: s for n, s in spec.scorers.items() if s.get("kind") != "human"}
    return SelectionConfig.model_validate(
        {
            "judges": spec.judges,
            "scorers": machine,
            "dedup": {"method": "off"},
            "score": {
                "gates": ["ran_ok", *spec.criteria.gates],
                "cascade": [{"scorers": scorers}] if scorers else [],
                "weights": spec.criteria.weights,
            },
            "select": {"policy": "argmax"},
            "record": {"path": str(root / ".hone" / "select" / "spans.db")},
        }
    )


def _measure_scorers(spec: d.ExperimentSpec, ranges: dict[str, tuple[float, float]]) -> list[Any]:
    """`measure = {name = "lower" | "higher"}`: a measurement as a 0..1 score over the experiment's range."""
    made: list[Any] = []
    for name, direction in spec.criteria.measure.items():
        low, high = ranges.get(name, (0.0, 0.0))

        def score(
            c: Candidate, name: str = name, direction: str = direction, low: float = low, high: float = high
        ) -> float | None:
            value = c.meta.get("measurements", {}).get(name)
            if not isinstance(value, int | float):
                return None
            share = 1.0 if high == low else (value - low) / (high - low)
            return 1.0 - share if direction == "lower" else share

        made.append(scorer(name, cost=0.0)(score))
    return made


@gate("ran_ok", cost=0.0)
def _ran_ok(c: Candidate) -> bool:
    """Implicit first gate: a sample whose subject failed is rejected (and counted per setup)."""
    return not c.meta.get("error")


def engine(
    spec: d.ExperimentSpec, root: Path, ranges: dict[str, tuple[float, float]] | None = None
) -> Engine:
    items = [_ran_ok, *_measure_scorers(spec, ranges or {}), *_registry(spec, root)]
    return Engine(_selection_config(spec, root), registry=items)


def check_criteria(spec: d.ExperimentSpec, root: Path) -> None:
    """Fail at plan time, not after hours of generation: every criterion must resolve."""
    for name in spec.criteria.scorers:
        if name not in spec.scorers and not _has(spec, root, name):
            raise ConfigError(
                f"criteria.scorers: {name!r} is neither a [scorers.{name}] section nor in the registry"
            )
    engine(spec, root)


def _has(spec: d.ExperimentSpec, root: Path, name: str) -> bool:
    return any(getattr(i, "name", None) == name for i in _registry(spec, root))


# -- score -------------------------------------------------------------------------------------------


def samples(folder: Path, case: str) -> list[dict[str, Any]]:
    return [read_json(p) for p in sorted((folder / "outputs" / case).glob("*/*/result.json"))]


def score(root: Path, folder: Path, spec: d.ExperimentSpec, cases: list[dict[str, Any]], eid: str) -> None:
    """One selection per case over its samples; writes `outputs/<case>/selection.json`. Samples that ran
    outside the run conditions are not candidates; they are scored on their own (`outside`), so
    `report --include-outside` can count them (design change 0010 §10)."""
    ranges = _ranges(folder, spec)
    eng = engine(spec, root, ranges)
    for case in cases:
        rows = samples(folder, case["id"])
        trace = {"hone.run_id": eid, "hone.item": case["id"], "hone.step": "experiment"}
        inside = [_candidate(folder, r) for r in rows if environment_status(r) not in EXCLUDED]
        outside = [_candidate(folder, r) for r in rows if environment_status(r) in EXCLUDED]
        result = eng.select(inside, trace=trace) if inside else None
        record: dict[str, Any] = {
            "run_id": result.run_id if result else None,
            "winner": result.winner.candidate.id if result and result.winner else None,
            "samples": _scored(result.ranked) if result else {},
        }
        if outside:
            record["outside"] = _scored(
                eng.score(outside, trace={**trace, "hone.step": "experiment-outside"})
            )
        write_json(folder / "outputs" / case["id"] / "selection.json", record)
        _prune(folder, spec, rows)


def _scored(ranked: list[Scored]) -> dict[str, Any]:
    return {
        s.candidate.id: {
            "total": s.total,
            "rejected": s.rejected,
            "scores": {k: _score(v) for k, v in s.scores.items()},
            "gates": {k: _gate(v) for k, v in s.gates.items()},
        }
        for s in ranked
    }


def _candidate(folder: Path, r: dict[str, Any]) -> Candidate:
    """A sample as a candidate; a cold sample's speed is left out (it includes loading the model)."""
    base = folder / "outputs" / r["case"] / r["setup"] / f"s{r['sample']}" / "files"
    files = {name: str(base / name) for name in r.get("files", {}) if (base / name).is_file()}
    measurements = dict(r.get("measurements", {}))
    if is_cold(r):
        measurements = {k: v for k, v in measurements.items() if k not in SPEED}
    meta = {
        "params": r["params"],
        "setup": r["setup"],
        "seed": r["seed"],
        "measurements": measurements,
        "error": r.get("error"),
        "environment_status": environment_status(r),
    }
    return Candidate(r["sample_id"], r.get("data") if r.get("data") is not None else "", files, meta)


def _score(s: Score) -> dict[str, Any]:
    return {"value": s.value, "confidence": s.confidence, "reason": s.reason, "error": s.error}


def _gate(g: GateResult) -> dict[str, Any]:
    return {"passed": g.passed, "reason": g.reason, "details": dict(g.details)}


def _ranges(folder: Path, spec: d.ExperimentSpec) -> dict[str, tuple[float, float]]:
    values: dict[str, list[float]] = {name: [] for name in spec.criteria.measure}
    for p in folder.glob("outputs/*/*/*/result.json"):
        r = read_json(p)
        if environment_status(r) in EXCLUDED:
            continue  # a busy machine's numbers do not set the range
        m = _candidate(folder, r).meta["measurements"]
        for name, found in values.items():
            if isinstance(m.get(name), int | float):
                found.append(float(m[name]))
    return {n: (min(v), max(v)) for n, v in values.items() if v}


def _prune(folder: Path, spec: d.ExperimentSpec, rows: list[dict[str, Any]]) -> None:
    """`keep_files`: small keeps files under 20 MB, none keeps no files (their hashes stay in result.json)."""
    if spec.generate.keep_files == "all":
        return
    for r in rows:
        base = folder / "outputs" / r["case"] / r["setup"] / f"s{r['sample']}" / "files"
        for name, info in r.get("files", {}).items():
            if spec.generate.keep_files == "none" or info["size"] > SMALL:
                (base / name).unlink(missing_ok=True)
        if spec.generate.keep_files == "none" and base.is_dir():
            shutil.rmtree(base, ignore_errors=True)
