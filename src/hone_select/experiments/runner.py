"""Running an approved experiment (design change 0009 §4): every cell, then a selection per case.

Generation is resumable (a sample with `result.json` is done) and ordered by `run.order`; scoring is in
`hone_select.experiments.selection`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hone_select.errors import HoneSelectError
from hone_select.experiments import definition as d
from hone_select.experiments import results, selection, subjects
from hone_select.experiments.project import Project, now, read_json, write_json


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
            selection.score(project.root, folder, spec, cases, eid.split("-", 1)[0])
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


def stop(project: Project, eid: str) -> None:
    """Ask a running experiment to stop after the current sample."""
    (project.path(eid) / "STOP").write_text(now() + "\n")
