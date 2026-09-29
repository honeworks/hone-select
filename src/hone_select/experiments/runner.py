"""Running an approved experiment (design change 0009 §4): every cell, then a selection per case.

Generation is resumable (a sample with `result.json` is done) and ordered by `run.order`; scoring is in
`hone_select.experiments.selection`.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from hone_select.errors import HoneSelectError
from hone_select.experiments import (
    applicable,
    checks,
    conditions,
    generation,
    guides,
    needs,
    outside,
    overrides,
    results,
    selection,
    subjects,
)
from hone_select.experiments import definition as d
from hone_select.experiments.guard import Check, Guard, RunStoppedError, StartRefusedError
from hone_select.experiments.project import Project, now, read_json, write_json

OUTSIDE = "outside-1.json"  # a sample set aside because it ran outside the run conditions


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
    project: Project,
    eid: str,
    on_sample: Callable[[dict[str, Any]], None] | None = None,
    *,
    sources: conditions.Sources | None = None,
) -> dict[str, Any]:
    """Run an approved experiment (or resume a stopped one) to the end; returns its status. `sources` are
    where the run-condition readings come from (default: this machine; tests pass fakes)."""
    _startable(project.status(eid), eid)
    folder, spec, cases = project.load(eid)
    plan = read_json(folder / "plan.json")
    guides.check_installed(spec, plan)
    (folder / "STOP").unlink(missing_ok=True)
    run: dict[str, Any] = {
        "state": "running",
        "pid": os.getpid(),
        "started_at": now(),
        "definition_hash": plan["definition_hash"],
        "stopped_because": None,
    }
    guard = Guard(spec, folder, run, sources or conditions.DEFAULT)
    write_json(folder / "run.json", run)
    try:
        guard.hold_lock(eid.split("-", 1)[0])
        finished = _generate(project.root, folder, spec, cases, plan, on_sample=on_sample, guard=guard)
        if finished:
            selection.score(project.root, folder, spec, cases, eid.split("-", 1)[0])
            results.report(folder, spec, plan)
        run |= {"state": "completed" if finished else "stopped", "ended_at": now()}
    except (RunStoppedError, StartRefusedError) as e:
        run |= {"state": "stopped", "ended_at": now(), "stopped_because": e.reasons}
        if isinstance(e, StartRefusedError):
            raise
    except BaseException:
        run |= {"state": "stopped", "ended_at": now()}
        raise
    finally:
        generation.end_session()
        guard.release()
        write_json(folder / "run.json", run)
    return project.status(eid)


def _startable(state: dict[str, Any], eid: str) -> None:
    if state["status"] in ("running", "waiting"):  # a live process: two runs would write the same files
        raise HoneSelectError(
            f"{eid} is already {state['status']} (pid {state['run']['pid']}); stop it first: "
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


Cell = tuple[dict[str, Any], dict[str, Any], int, int]  # case, setup, sample, attempt


def _target(folder: Path, case: dict[str, Any], setup: dict[str, Any], k: int) -> Path:
    return folder / "outputs" / case["id"] / d.setup_id(setup) / f"s{k}" / "result.json"


def _pending(
    folder: Path, spec: d.ExperimentSpec, cases: list[dict[str, Any]], plan: dict[str, Any]
) -> deque[Cell]:
    """The samples not done yet, in run order, without the cells that are not applicable (design change
    0011 §3); a sample set aside once (`outside-1.json`) is attempt 2."""
    out: deque[Cell] = deque()
    skip = applicable.skipped(plan)
    for case, setup, k in cells(spec, cases):
        target = _target(folder, case, setup, k)
        if (case["id"], d.setup_id(setup)) in skip:
            continue
        if not target.is_file():
            out.append((case, setup, k, 2 if target.with_name(OUTSIDE).is_file() else 1))
    return out


def _generate(
    root: Path,
    folder: Path,
    spec: d.ExperimentSpec,
    cases: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    on_sample: Callable[[dict[str, Any]], None] | None,
    guard: Guard,
) -> bool:
    """Every missing sample, each between two checks of the run conditions; False when stopped early
    (STOP file or budget). Raises `RunStoppedError` when the conditions stop the run."""
    hook = subjects.import_object(spec.generate.after_group) if spec.generate.after_group else None
    queue = _pending(folder, spec, cases, plan)
    before: Check | None = None
    while queue:
        case, setup, k, attempt = queue.popleft()
        if (folder / "STOP").is_file() or _over_budget(folder, spec):
            return False
        need = needs.needed(spec, case, setup)
        if before is None or before.needed != need:
            before = guard.check(need, prepare=True)
        before = guard.settle(before, partial(guard.check, need, prepare=True))
        target, seed = _target(folder, case, setup, k), spec.seed + k
        where = subjects.Where(root, folder, target.parent / "files", guides.for_setup(plan, setup))
        out = subjects.run_sample(spec, case, setup, seed, where)
        after = _after(guard, hook, spec, nxt=queue[0] if queue else None, setup=setup, need=need)
        env = outside.out_of_memory(guard.environment(before, after, attempt), out, spec)
        result = _record(spec, folder, plan, (case, setup, k, seed)) | out | {"environment": env}
        if not _keep(spec, target, result, attempt):
            queue.appendleft((case, setup, k, 2))  # set aside: it runs once more
        elif on_sample is not None:
            on_sample(result)
        before = after
    return True


def _record(
    spec: d.ExperimentSpec,
    folder: Path,
    plan: dict[str, Any],
    cell: tuple[dict[str, Any], dict[str, Any], int, int],
) -> dict[str, Any]:
    case, setup, k, seed = cell
    sid = d.setup_id(setup)
    record = {
        "sample_id": sample_id(case["id"], sid, k),
        "case": case["id"],
        "case_fields": case["fields"],
        "judge_view": overrides.judge_view(case),
        "setup": sid,
        "params": setup,
        "sample": k,
        "seed": seed,
        "at": now(),
    }
    if asked := overrides.differences(spec, case, setup, folder):
        record["asked_differently"] = asked
    if unsure := applicable.unknown_needs(plan, case["id"], sid):
        record["need_unknown"] = unsure
    return record


def _after(
    guard: Guard,
    hook: Any,
    spec: d.ExperimentSpec,
    *,
    nxt: Cell | None,
    setup: dict[str, Any],
    need: list[str],
) -> Check:
    """The check after a sample, which is also the check before the next one (its `after_group` hook first
    when the `run.order` factor changes). After the last sample: a reading only."""
    group = setup.get(spec.run.order or "")
    if nxt is None or overrides.model_of(spec, nxt[1]) != overrides.model_of(spec, setup):
        generation.end_session()  # the model changes: its session ends and frees it
    if hook is not None and group is not None and (nxt is None or nxt[1].get(spec.run.order or "") != group):
        hook(group)
    if nxt is None:
        return guard.check(need, prepare=False)
    return guard.check(needs.needed(spec, nxt[0], nxt[1]), prepare=True)


def _keep(spec: d.ExperimentSpec, target: Path, result: dict[str, Any], attempt: int) -> bool:
    """Write the sample; a first attempt outside the conditions is set aside as `outside-1.json` instead
    (it runs again), unless the experiment only records (`record_only`). False when set aside."""
    env = result["environment"]
    mode = spec.conditions.on_violation
    if env["status"] in ("outside", "unknown") and mode != "record_only" and attempt == 1:
        write_json(target.with_name(OUTSIDE), result)
        if mode == "stop":
            raise RunStoppedError(checks.reasons(env["checks"]))
        return False
    write_json(target, result)
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
