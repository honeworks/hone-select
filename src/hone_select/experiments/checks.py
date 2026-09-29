"""Judging a reading against the declared conditions (design change 0010 §2, §5, §6, §9).

Each declared condition is `ok`, `outside` (with the value, the limit and a reason) or `unknown` (with why
it could not be measured); a condition holds only when it was measured. `status` sums up a sample.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from hone_select.experiments import gpulock
from hone_select.experiments.definition import ConditionsSpec

OK, OUTSIDE, UNKNOWN = "ok", "outside", "unknown"
WORST = {OK: 0, UNKNOWN: 1, OUTSIDE: 2}  # outside wins over unknown: it was measured
ON_GPU = 0.99  # a model with less than 99 % of its size in VRAM is partly on the CPU
MINE = (gpulock.BY_RUN, gpulock.BY_PARENT, gpulock.FREE)  # the lock is this run's, or free (plan)

Check = dict[str, Any]
Models = list[Mapping[str, Any]]


def _ok(value: Any = None, limit: Any = None) -> Check:
    return {"state": OK, "value": value, "limit": limit}


def _bad(state: str, reason: str, value: Any = None, limit: Any = None) -> Check:
    return {"state": state, "value": value, "limit": limit, "reason": reason}


def _unknown(name: str, why: str) -> Check:
    return _bad(UNKNOWN, f"{name}: {why}")


def _gb(v: float) -> str:
    return f"{v:.1f} GB"


def cpu(c: ConditionsSpec, r: Mapping[str, Any]) -> Check:
    v, limit = r.get("cpu_busy"), c.max_cpu_load
    if v is None:
        return _unknown("max_cpu_load", "the CPU load cannot be read (/proc/stat)")
    if v > cast(float, limit):
        return _bad(OUTSIDE, f"max_cpu_load: {v:.2f} of the CPU busy, limit {limit:g}", v, limit)
    return _ok(v, limit)


def ram(c: ConditionsSpec, r: Mapping[str, Any]) -> Check:
    v, limit = r.get("free_ram_gb"), c.min_free_ram_gb
    if v is None:
        return _unknown("min_free_ram_gb", "the free RAM cannot be read (/proc/meminfo)")
    if v < cast(float, limit):
        return _bad(OUTSIDE, f"min_free_ram_gb: {_gb(v)} available, needs {limit:g}", v, limit)
    return _ok(v, limit)


def _users(gpu: Mapping[str, Any]) -> str:
    procs = sorted(gpu.get("processes") or [], key=lambda p: -(p.get("memory_gb") or 0))[:2]
    return ", ".join(f"{p.get('name')} {_gb(p.get('memory_gb') or 0)}" for p in procs)


def vram(c: ConditionsSpec, r: Mapping[str, Any]) -> Check:
    gpu, limit = r.get("gpu"), c.min_free_vram_gb
    v = gpu.get("free_for_run_gb") if gpu else None
    if gpu is None or v is None:
        return _unknown("min_free_vram_gb", "GPU memory cannot be read (no nvidia-smi and no probe)")
    if v < cast(float, limit):
        users = _users(gpu)
        reason = f"min_free_vram_gb: {_gb(v)} free, needs {limit:g}" + (f" ({users})" if users else "")
        return _bad(OUTSIDE, reason, v, limit)
    return _ok(v, limit)


def utilization(c: ConditionsSpec, r: Mapping[str, Any]) -> Check:
    gpu, limit = r.get("gpu"), c.max_gpu_utilization_pct
    v = gpu.get("utilization_pct") if gpu else None
    if v is None:
        return _unknown(
            "max_gpu_utilization_pct", "GPU utilization cannot be read (no nvidia-smi and no probe)"
        )
    if v > cast(float, limit):
        return _bad(OUTSIDE, f"max_gpu_utilization_pct: GPU {v:g} % busy, limit {limit:g}", v, limit)
    return _ok(v, limit)


def _model_state_unknown(name: str, r: Mapping[str, Any]) -> Check | None:
    """The probe failed, or a model server could not tell what it holds."""
    if "probe" in r.get("errors", {}):
        return _unknown(name, f"the probe failed: {r['errors']['probe']}")
    if "loaded_models" not in r:
        return _unknown(name, "no probe reading")
    for s in r.get("servers", []):
        if s.get("running") is None:
            return _unknown(name, f"{s.get('server')} cannot tell what it holds ({s.get('error')})")
    return None


def describe(blocker: Any) -> str:
    """ "lease gpu:tts, pid 5120" for a lease; the value itself otherwise."""
    if isinstance(blocker, Mapping):
        b = cast(Mapping[str, Any], blocker)
        what = f"lease {b['name']}" if b.get("name") else str(b.get("holder") or dict(b))
        return what + (f", pid {b['pid']}" if b.get("pid") else "")
    return str(blocker)


def _prepared(name: str, r: Mapping[str, Any], prepared: Mapping[str, Any]) -> Check | None:
    """What `prepare` answered: failed, blocked by another process, or an unload that failed."""
    if "error" in prepared:
        return _unknown(name, f"prepare failed: {prepared['error']}")
    if prepared.get("blocked_by") and prepared.get("if_busy") != "unload":
        blockers = ", ".join(describe(b) for b in prepared["blocked_by"])
        return _bad(OUTSIDE, f"{name}: blocked by {blockers}", [], None)
    running = {s.get("server") for s in r.get("servers", []) if s.get("running") is True}
    errors: list[Any] = list(prepared.get("errors") or [])
    for e in errors:
        if not isinstance(e, Mapping):
            return _bad(OUTSIDE, f"{name}: {e}")
        err = cast(Mapping[str, Any], e)
        what = f"{name}: {err.get('name')} could not be unloaded: {err.get('error')}"
        server_down = err.get("server") is not None and err.get("server") not in running
        return _bad(UNKNOWN if server_down else OUTSIDE, what)
    return None


def only_needed(r: Mapping[str, Any], needed: Sequence[str], prepared: Mapping[str, Any] | None) -> Check:
    name = "only_needed_models"
    if (unknown := _model_state_unknown(name, r)) is not None:
        return unknown
    if prepared is not None and (bad := _prepared(name, r, prepared)) is not None:
        return bad
    extra = [str(m.get("name")) for m in cast(Models, r["loaded_models"]) if m.get("model_id") not in needed]
    if extra:
        verb = "is loaded (prepare would unload it)" if prepared is None else "is still loaded"
        return _bad(OUTSIDE, f"{name}: {', '.join(extra)} {verb}", extra)
    return _ok([])


def on_gpu(
    r: Mapping[str, Any],
    needed: Sequence[str],
    prepared: Mapping[str, Any] | None,
    loads: Sequence[Mapping[str, Any]] = (),
) -> Check:
    name = "models_on_gpu"
    if (unknown := _model_state_unknown(name, r)) is not None:
        return unknown
    gpu: Mapping[str, Any] = r.get("gpu") or {}
    total = gpu.get("total_gb")
    need = (prepared or {}).get("need_gb")
    if isinstance(need, int | float) and isinstance(total, int | float) and need > total:
        return _bad(OUTSIDE, f"{name}: the needed models do not fit in the GPU ({_gb(need)} of {_gb(total)})")
    shares: dict[str, float] = {}
    models = [*loads, *cast(Models, r["loaded_models"])]
    for m in models:
        size, on = m.get("size_gb"), m.get("vram_gb")
        mid = str(m.get("model_id"))
        if mid in needed and isinstance(size, int | float) and isinstance(on, int | float) and size > 0:
            shares.setdefault(mid, round(on / size, 3))
    partly = [f"{mid} ({share:.0%} on the GPU)" for mid, share in shares.items() if share < ON_GPU]
    if partly:
        return _bad(OUTSIDE, f"{name}: partly on the CPU: {', '.join(partly)}", shares)
    return _ok(shares)


def gpu_lock(r: Mapping[str, Any]) -> Check:
    name = "gpu_lock"
    state = r.get("gpu_lock")
    if state not in MINE:
        return _bad(OUTSIDE, f"{name}: another process holds the GPU lock ({state})", state)
    probe_lock: Mapping[str, Any] = r.get("probe_gpu_lock") or {}
    if probe_lock.get("held") and probe_lock.get("mine") is False:
        return _bad(OUTSIDE, f"{name}: another process holds the GPU lock: {probe_lock.get('holder')}", state)
    foreign = [describe(x) for x in r.get("leases", []) if x.get("mine") is False]
    if foreign:
        return _bad(OUTSIDE, f"{name}: another process holds a GPU lease: {', '.join(foreign)}", state)
    return _ok(state)


def judge(
    c: ConditionsSpec,
    r: Mapping[str, Any],
    needed: Sequence[str],
    prepared: Mapping[str, Any] | None,
    loads: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Check]:
    """Every declared condition against one reading."""
    judges = {
        "max_cpu_load": lambda: cpu(c, r),
        "min_free_ram_gb": lambda: ram(c, r),
        "min_free_vram_gb": lambda: vram(c, r),
        "max_gpu_utilization_pct": lambda: utilization(c, r),
        "only_needed_models": lambda: only_needed(r, needed, prepared),
        "models_on_gpu": lambda: on_gpu(r, needed, prepared, loads),
        "gpu_lock": lambda: gpu_lock(r),
    }
    return {name: judges[name]() for name in c.declared()}


def free_for_run(r: dict[str, Any], needed: Sequence[str]) -> None:
    """GPU memory free for this run: the free memory plus what the needed models already hold."""
    gpu = r.get("gpu")
    if not gpu or gpu.get("free_gb") is None:
        return
    held = sum(m.get("vram_gb") or 0.0 for m in r.get("loaded_models", []) if m.get("model_id") in needed)
    gpu["free_for_run_gb"] = round(gpu["free_gb"] + held, 2)


def reasons(checks: Mapping[str, Check]) -> list[str]:
    return [str(c["reason"]) for c in checks.values() if c["state"] != OK]


def holds(checks: Mapping[str, Check]) -> bool:
    return all(c["state"] == OK for c in checks.values())


def merge(before: Mapping[str, Check], after: Mapping[str, Check]) -> dict[str, Check]:
    """Per condition: the worse state of the readings before and after a sample, with both values."""
    out: dict[str, Check] = {}
    for name in dict.fromkeys([*before, *after]):
        b, a = before.get(name, {}), after.get(name, {})
        worst = max((x for x in (b, a) if x), key=lambda x: WORST[x["state"]])
        out[name] = {"state": worst["state"], "before": b.get("value"), "after": a.get("value")}
        out[name]["limit"] = worst.get("limit")
        if worst.get("reason"):
            out[name]["reason"] = worst["reason"]
    return out


def status(checks: Mapping[str, Check], declared: bool) -> str:
    """ok / outside / unknown for a sample; not_checked when the experiment declares no conditions."""
    if not declared:
        return "not_checked"
    states = {c["state"] for c in checks.values()}
    return OUTSIDE if OUTSIDE in states else UNKNOWN if UNKNOWN in states else OK
