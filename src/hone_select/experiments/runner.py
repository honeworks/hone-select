"""Running an approved experiment (design change 0009 §4): every cell, then a selection per case.

Generation is resumable (a sample with `result.json` is done) and ordered by `run.order`. Scoring makes
one hone-select selection per case over its samples (the implicit gate `ran_ok` rejects failed samples;
`measure` criteria are normalized over the whole experiment), recorded with the experiment id and case in
the trace context.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hone_select.config import SelectionConfig
from hone_select.engine import Engine
from hone_select.errors import ConfigError, HoneSelectError
from hone_select.experiments import definition as d
from hone_select.experiments import results, subjects
from hone_select.experiments.project import Project, now, read_json, write_json
from hone_select.registry import gate, module_items, scorer
from hone_select.types import Candidate, GateResult, Score

SMALL = 20 * 1024 * 1024  # keep_files = "small": files above this are hashed, then deleted


def sample_id(case: str, setup: str, k: int) -> str:
    return f"{case}__{setup}__s{k}"


def cells(
    spec: d.ExperimentSpec, cases: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    """(case, setup, sample) in run order: grouped by the `run.order` factor, then case, then setup."""
    setups = d.setups(spec)
    key = spec.run.order
    order = {str(v): i for i, v in enumerate(spec.factors.get(key or "", []))}
    grid = [(c, s, k) for s in setups for c in cases for k in range(spec.samples)]
    return (
        sorted(grid, key=lambda x: order.get(str(x[1].get(key or "")), len(order)))
        if key in spec.factors
        else grid
    )


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


# -- run ---------------------------------------------------------------------------------------------


def start(
    project: Project, eid: str, on_sample: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Run an approved experiment (or resume a stopped one) to the end; returns its status."""
    state = project.status(eid)
    if state["status"] == "running":  # a live process: two runs would write the same files
        raise HoneSelectError(
            f"{eid} is already running (pid {state['run']['pid']}); stop it first: "
            f"hone-select experiments stop {eid}"
        )
    if state["status"] == "completed":
        raise HoneSelectError(
            f"{eid} is completed; `hone-select experiments report {eid}` recomputes the results, "
            "and a changed definition needs a new plan and approval"
        )
    if state["status"] not in ("approved", "stopped"):
        raise HoneSelectError(
            f"{eid} is {state['status']}: only an approved experiment starts "
            "(plan it, then approve it in the dashboard or with `experiments approve`)"
        )
    folder, spec, cases = project.load(eid)
    plan = read_json(folder / "plan.json")
    (folder / "STOP").unlink(missing_ok=True)
    run = {
        "state": "running",
        "pid": os.getpid(),
        "started_at": now(),
        "definition_hash": plan["definition_hash"],
    }
    write_json(folder / "run.json", run)
    try:
        finished = _generate(project.root, folder, spec, cases, on_sample)
        if finished:
            score(project.root, folder, spec, cases, eid.split("-", 1)[0])
            results.report(folder, spec, plan)
        run |= {"state": "completed" if finished else "stopped", "ended_at": now()}
    except BaseException:
        run |= {"state": "stopped", "ended_at": now()}
        raise
    finally:
        write_json(folder / "run.json", run)
    return project.status(eid)


def _generate(
    root: Path,
    folder: Path,
    spec: d.ExperimentSpec,
    cases: list[dict[str, Any]],
    on_sample: Callable[[dict[str, Any]], None] | None,
) -> bool:
    """Every missing sample; False when stopped early (STOP file or budget)."""
    hook = subjects.import_object(spec.generate.after_group) if spec.generate.after_group else None
    group: Any = None
    for case, setup, k in cells(spec, cases):
        sid = d.setup_id(setup)
        target = folder / "outputs" / case["id"] / sid / f"s{k}" / "result.json"
        if target.is_file():
            continue
        if (folder / "STOP").is_file() or _over_budget(folder, spec):
            return False
        current = setup.get(spec.run.order or "")
        if hook is not None and group is not None and current != group:
            hook(group)
        group = current
        seed = spec.seed + k
        out = subjects.run_sample(
            spec, case, setup, seed, subjects.Where(root, folder, target.parent / "files")
        )
        result = {
            "sample_id": sample_id(case["id"], sid, k),
            "case": case["id"],
            "case_fields": case["fields"],
            "setup": sid,
            "params": setup,
            "sample": k,
            "seed": seed,
            "at": now(),
            **out,
        }
        write_json(target, result)
        if on_sample is not None:
            on_sample(result)
    if hook is not None and group is not None:
        hook(group)
    return True


def _over_budget(folder: Path, spec: d.ExperimentSpec) -> bool:
    done = [read_json(p) for p in folder.glob("outputs/*/*/*/result.json")]
    seconds = sum(r.get("measurements", {}).get("seconds", 0.0) for r in done)
    money = sum(r["cost_usd"] for r in done if isinstance(r.get("cost_usd"), int | float))  # known costs only
    b = spec.budget
    return (b.seconds is not None and seconds >= b.seconds) or (
        b.money_usd is not None and money >= b.money_usd
    )


# -- score -------------------------------------------------------------------------------------------


def samples(folder: Path, case: str) -> list[dict[str, Any]]:
    return [read_json(p) for p in sorted((folder / "outputs" / case).glob("*/*/result.json"))]


def score(root: Path, folder: Path, spec: d.ExperimentSpec, cases: list[dict[str, Any]], eid: str) -> None:
    """One selection per case over its samples; writes `outputs/<case>/selection.json`."""
    ranges = _ranges(folder, spec)
    eng = engine(spec, root, ranges)
    for case in cases:
        rows = samples(folder, case["id"])
        candidates = [_candidate(folder, r) for r in rows]
        trace = {"hone.run_id": eid, "hone.item": case["id"], "hone.step": "experiment"}
        result = eng.select(candidates, trace=trace)
        write_json(
            folder / "outputs" / case["id"] / "selection.json",
            {
                "run_id": result.run_id,
                "winner": result.winner.candidate.id if result.winner else None,
                "samples": {
                    s.candidate.id: {
                        "total": s.total,
                        "rejected": s.rejected,
                        "scores": {k: _score(v) for k, v in s.scores.items()},
                        "gates": {k: _gate(v) for k, v in s.gates.items()},
                    }
                    for s in result.ranked
                },
            },
        )
        _prune(folder, spec, rows)


def _candidate(folder: Path, r: dict[str, Any]) -> Candidate:
    base = folder / "outputs" / r["case"] / r["setup"] / f"s{r['sample']}" / "files"
    files = {name: str(base / name) for name in r.get("files", {}) if (base / name).is_file()}
    meta = {
        "params": r["params"],
        "setup": r["setup"],
        "seed": r["seed"],
        "measurements": r.get("measurements", {}),
        "error": r.get("error"),
    }
    return Candidate(r["sample_id"], r.get("data") if r.get("data") is not None else "", files, meta)


def _score(s: Score) -> dict[str, Any]:
    return {"value": s.value, "confidence": s.confidence, "reason": s.reason, "error": s.error}


def _gate(g: GateResult) -> dict[str, Any]:
    return {"passed": g.passed, "reason": g.reason, "details": dict(g.details)}


def _ranges(folder: Path, spec: d.ExperimentSpec) -> dict[str, tuple[float, float]]:
    values: dict[str, list[float]] = {name: [] for name in spec.criteria.measure}
    for p in folder.glob("outputs/*/*/*/result.json"):
        m = read_json(p).get("measurements", {})
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


def stop(project: Project, eid: str) -> None:
    """Ask a running experiment to stop after the current sample."""
    (project.path(eid) / "STOP").write_text(now() + "\n")
