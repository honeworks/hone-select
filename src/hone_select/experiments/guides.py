"""What each model can take (design change 0011 §4, §5): the guide source, the guides read once at plan
time into `plan.json` `models`, the models that are not installed, and a guide as text for prompts."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import entry_points
from typing import Any, cast

from hone_select.errors import ConfigError, HoneSelectError
from hone_select.experiments.definition import ExperimentSpec, GenerateSpec

GROUP = "hone.model_guides"
DEFAULT = "hone_models:guides"  # the source when the client is a hone_models:* factory
TARGET = "target_model"  # a setup's model a prompt is written for ({model_guide}, ctx.model_guide)


def source_name(g: GenerateSpec) -> str | None:
    """The declared `guides`, else hone-models' when the client is one of its factories, else None."""
    if g.guides:
        return g.guides
    return DEFAULT if (g.client or "").startswith("hone_models:") else None


def resolve(name: str) -> Any:
    """`guides = "<name>"`: a `hone.model_guides` entry point by exact name, else a "module:factory"; the
    factory is called with no arguments."""
    found = entry_points(group=GROUP, name=name)
    if found:
        return next(iter(found)).load()()
    from hone_select.experiments.subjects import import_object  # noqa: PLC0415 - subjects imports this module

    try:
        return import_object(name)()
    except ConfigError as e:
        available = sorted(ep.name for ep in entry_points(group=GROUP))
        raise ConfigError(
            f"[generate] guides {name!r} is neither an installed guide source ({available}) nor a "
            f"'module:factory' ({e}); install its package (for hone-models: hone-select[models]) or remove "
            "`guides`"
        ) from e


def models_in(spec: ExperimentSpec, setups: list[dict[str, Any]]) -> list[str]:
    """Every model the setups name (`model`, `target_model`, `[generate] client_args.model`), in order."""
    default = spec.generate.client_args.get("model")
    found = [s.get(key, default if key == "model" else None) for s in setups for key in ("model", TARGET)]
    return list(dict.fromkeys(str(m) for m in found if m is not None))


def _entry(model: str, guide: Mapping[str, Any] | None) -> dict[str, Any]:
    g = dict(guide) if guide is not None else None
    installed = str(g.get("installed") or "unknown") if g else "unknown"
    return {
        "guide": g,
        "installed": installed,
        "install": (g.get("install") or f"hone-models models install {model}") if g else None,
        "license": g.get("license") if g else None,
        "commercial_use": g.get("commercial_use") if g else None,
    }


def read(spec: ExperimentSpec, setups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """`plan.json` `models`: per model its guide, whether it is installed and how to install it, its
    license; None without a guide source (then every need is need_unknown)."""
    name = source_name(spec.generate)
    if name is None:
        return None
    source = resolve(name)
    return {m: _entry(m, source.guide(m)) for m in models_in(spec, setups)}


def of(plan: Mapping[str, Any], model: Any) -> Mapping[str, Any] | None:
    """The stored guide of `model`, or None."""
    models = cast(Mapping[str, Mapping[str, Any]], plan.get("models") or {})
    return cast(Mapping[str, Any] | None, (models.get(str(model)) or {}).get("guide"))


def for_setup(plan: Mapping[str, Any], setup: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The guide a sample's prompt and `ctx` get: of its `target_model`, else of its `model`."""
    return of(plan, setup.get(TARGET, setup.get("model")))


def not_installed(plan: Mapping[str, Any]) -> list[str]:
    models = cast(Mapping[str, Mapping[str, Any]], plan.get("models") or {})
    return [m for m, e in models.items() if e.get("installed") == "no"]


def _installed(guide: Any) -> Any:
    return cast(Mapping[str, Any], guide or {}).get("installed")


def check_installed(spec: ExperimentSpec, plan: Mapping[str, Any]) -> None:
    """`start` refuses while a model the plan found not installed is still reported not installed (the
    source is asked again, so installing the model is enough; no new plan is needed)."""
    missing = not_installed(plan)
    if not missing:
        return
    source = resolve(str(source_name(spec.generate)))
    still = [m for m in missing if str(_installed(source.guide(m))) == "no"]
    if still:
        models = cast(Mapping[str, Mapping[str, Any]], plan["models"])
        commands = "; ".join(str(models[m]["install"]) for m in still)
        raise HoneSelectError(
            f"not installed: {', '.join(still)}. Install them ({commands}) or remove them from the "
            "factors and plan again"
        )


def _limits(g: Mapping[str, Any]) -> list[str]:
    keys = ("sizes", "durations_s", "max_duration_s", "max_references", "license", "commercial_use")
    return [f"{k}: {g[k]}" for k in keys if g.get(k) is not None]


def _inputs(value: Any) -> list[str]:
    """`inputs` (a table of notes, or a list of names) as lines."""
    if isinstance(value, Mapping):
        notes = cast(Mapping[str, Any], value)
        return [f"- input {k}: {v}" if v else f"- input {k}" for k, v in notes.items()]
    return [f"- input {k}" for k in cast(list[Any], value or [])]


def text(g: Mapping[str, Any] | None) -> str:
    """A guide as plain text for a prompt (`{model_guide}`): the source's own text when it has one."""
    if not g:
        return ""
    if isinstance(g.get("text"), str):
        return str(g["text"])
    lines = [
        f"{g.get('id', '')} ({g.get('kind') or 'model'})",
        *(str(g[k]) for k in ("summary",) if g.get(k)),
    ]
    if g.get("prompt"):
        lines.append(f"Prompt: {g['prompt']}")
    lines += _inputs(g.get("inputs"))
    for f in cast(list[Mapping[str, Any]], g.get("features") or []):
        examples = "; ".join(str(e) for e in cast(list[Any], f.get("examples") or []))
        lines.append(
            f"- feature {f.get('name')}: {f.get('how') or ''}" + (f" (e.g. {examples})" if examples else "")
        )
    return "\n".join([*lines, *_limits(g)])
