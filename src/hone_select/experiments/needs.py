"""What an experiment's run conditions need (design change 0010 §3, §5, §6, §7): the models each sample
needs, the probe, the plan-time checks of the definition and the plan's `conditions` section."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from hone_select.errors import ConfigError
from hone_select.experiments import checks, conditions, gpulock, subjects
from hone_select.experiments.definition import ConditionsSpec, ExperimentSpec, setup_id

ACTIONS = {"wait": "wait", "stop": "refuse to start", "record_only": "run and mark the samples"}


def needed(spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any]) -> list[str]:
    """The registry ids a sample needs loaded: `[conditions] models` (placeholders filled), else the prompt
    subject's model, else none."""
    c, g = spec.conditions, spec.generate
    if c.models is not None:
        values = subjects.placeholders(case, setup, subjects.Where(Path(), Path(), Path()), spec.seed)
        out: list[str] = []
        for m in c.models:
            try:
                out.append(m.format_map(values))
            except (KeyError, AttributeError, IndexError, ValueError, ConfigError) as e:
                raise ConfigError(
                    f"[conditions] models: {m!r} names no factor or case field ({e}); use "
                    '"{setup.<factor>}" or "{case.<field>}"'
                ) from e
        return out
    if g.kind == "prompt":
        model = setup.get("model", g.client_args.get("model"))
        return [str(model)] if model is not None else []
    return []


def resolve(name: str) -> Any:
    """`probe = "<name>"`: the factory of that `hone.machine_probes` entry point, called with no arguments."""
    found = entry_points(group="hone.machine_probes", name=name)
    if not found:
        available = sorted(ep.name for ep in entry_points(group="hone.machine_probes"))
        raise ConfigError(
            f"[conditions] probe {name!r} is not installed; available: {available}. Install its package "
            "(for hone-models: hone-select[models]) or remove `probe`"
        )
    return next(iter(found)).load()()


def probe_for(spec: ExperimentSpec, src: conditions.Sources) -> Any:
    """The declared probe (the one in `src` when given, as tests do), or None."""
    if not spec.conditions.probe:
        return None
    return src.probe if src.probe is not None else resolve(spec.conditions.probe)


def check_definition(spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]]) -> None:
    """The definition mistakes that plan refuses (design change 0010 §5)."""
    c, fix = spec.conditions, 'set probe = "hone_models:machine" (install hone-select[models]) or remove'
    for key in ("only_needed_models", "models_on_gpu"):
        if getattr(c, key) and not c.probe:
            raise ConfigError(f"[conditions] {key} needs a probe: {fix} `{key}`")
    if c.min_free_vram_gb is not None and spec.generate.kind == "prompt" and not c.probe:
        raise ConfigError(
            "[conditions] min_free_vram_gb with a prompt subject needs a probe (its own model stays loaded "
            f"and would count as used): {fix} `min_free_vram_gb`"
        )
    if gpulock.lock_path(c.gpu_lock) is not None and any("gpu-lock.sh" in w for w in spec.generate.wrap):
        raise ConfigError(
            "remove gpu-lock.sh from [generate] wrap: the run holds the GPU lock ([conditions] gpu_lock)"
        )
    for case in cases:
        for setup in setups:
            needed(spec, case, setup)


def declared(c: ConditionsSpec) -> dict[str, Any]:
    out = c.model_dump(exclude_defaults=True) | {
        "on_violation": c.on_violation,
        "wait_timeout": c.wait_timeout,
    }
    path = gpulock.lock_path(c.gpu_lock)
    if path is not None:
        out["gpu_lock"] = str(path)
    return out


def would(c: ConditionsSpec, found: dict[str, checks.Check]) -> str:
    """What `start` would do with this reading."""
    if checks.holds(found):
        return "start"
    if any(v["state"] == checks.UNKNOWN for v in found.values()):
        return "refuse to start (cannot measure): " + "; ".join(checks.reasons(found))
    extra = found.get("only_needed_models", {})
    unload = f" (prepare would unload {', '.join(extra['value'])})" if extra.get("value") else ""
    return f"{ACTIONS[c.on_violation]}: " + "; ".join(checks.reasons(found)) + unload


def plan_section(
    spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]], src: conditions.Sources
) -> dict[str, Any] | None:
    """`plan.json` `conditions`: the declared conditions, the models per setup and a reading now. `plan`
    unloads nothing: the reading is a snapshot."""
    c = spec.conditions
    if c == ConditionsSpec():
        return None
    per_setup = {
        setup_id(s): list(dict.fromkeys(m for case in cases for m in needed(spec, case, s))) for s in setups
    }
    every = list(dict.fromkeys(m for ms in per_setup.values() for m in ms))
    path = gpulock.lock_path(c.gpu_lock)
    window = src.window if c.max_cpu_load is not None else conditions.SHORT_WINDOW
    probe = probe_for(spec, src)
    reading = conditions.read(src, probe, window=window, lock=gpulock.state_now(path) if path else None)
    checks.free_for_run(reading, every if probe is not None else [])
    found = checks.judge(c, reading, every, None)
    now = {"at": reading["at"], "reading": reading, "checks": found, "would": would(c, found)}
    return {"declared": declared(c), "needed_models": per_setup, "now": now}
