"""How each model is asked (design change 0011 §2): the overrides of a case (`per_model` in the case) and of
`[generate]` (`[generate.per_model."<model>"]`), prompt files per model (`prompts/<model>/<file>` before
`prompts/<file>`), what differs for one cell ("asked differently"), and the view of a case judges get.

Judges never see an override: they get the case's shared fields, or only those named by its `judge_view`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from hone_select.errors import ConfigError
from hone_select.experiments.definition import ExperimentSpec, GenerateSpec, setup_id


def model_of(spec: ExperimentSpec, setup: dict[str, Any]) -> str | None:
    """The model a setup runs: its `model` factor, else `[generate] client_args.model`."""
    model = setup.get("model", spec.generate.client_args.get("model"))
    return None if model is None else str(model)


def _case_override(case: dict[str, Any], model: str | None) -> dict[str, Any]:
    table = cast(dict[str, Any], case.get("per_model") or {})
    return dict(table.get(model or "") or {})


def case_for(case: dict[str, Any], model: str | None) -> dict[str, Any]:
    """The case as `model` is asked: its `per_model[model]` fields over its fields. The shared fields stay
    in `shared_fields` (what judges see)."""
    over = _case_override(case, model)
    if not over:
        return case
    return {**case, "fields": {**case["fields"], **over}, "shared_fields": case["fields"]}


def judge_view(case: dict[str, Any]) -> dict[str, Any]:
    """What judges see of a case: its shared fields (never an override), only `judge_view` when set."""
    shared: dict[str, Any] = case.get("shared_fields", case["fields"])
    keep = case.get("judge_view")
    return {k: v for k, v in shared.items() if keep is None or k in keep}


def prompt_file(prompts: Path, name: str, model: str | None) -> Path | None:
    """`prompts/<model>/<name>` when it exists, else `prompts/<name>`, else None."""
    for path in ([prompts / model / name] if model else []) + [prompts / name]:
        if path.is_file():
            return path
    return None


def generate_for(g: GenerateSpec, model: str | None) -> tuple[str, dict[str, Any]]:
    """The prompt template and inputs for `model`: `[generate.per_model.<model>]` over `[generate]`."""
    over = g.per_model.get(model or "")
    if over is None:
        return g.prompt, dict(g.inputs)
    return over.prompt or g.prompt, {**g.inputs, **over.inputs}


def differences(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], folder: Path
) -> dict[str, Any]:
    """What is asked differently of this cell's model: the `[generate.per_model]` table, the case's
    override and a model-specific prompt file. Empty when the model is asked like the others."""
    model, g = model_of(spec, setup), spec.generate
    out: dict[str, Any] = {}
    over = g.per_model.get(model or "")
    if over is not None:
        out["per_model"] = over.model_dump(exclude_defaults=True)
    if case_over := _case_override(case, model):
        out["case"] = case_over
    names = [str(setup.get("prompt", "")), generate_for(g, model)[0], str(g.system or "")]
    files = [f"prompts/{model}/{n}" for n in names if model and (folder / "prompts" / model / n).is_file()]
    if files:
        out["prompt_files"] = files
    return out


def check(spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]]) -> None:
    """Plan-time mistakes: an override for a model the experiment does not run, a case override of a field
    the case does not have, or reserved case keys of the wrong type."""
    models = sorted({m for s in setups if (m := model_of(spec, s)) is not None})
    for model in spec.generate.per_model:
        if model not in models:
            raise ConfigError(
                f'[generate.per_model."{model}"]: {model!r} is not a model of this experiment ({models})'
            )
    for case in cases:
        _check_case(case, models)


def _check_case(case: dict[str, Any], models: list[str]) -> None:
    where = f"case {case['id']!r}"
    for key in ("needs", "judge_view"):
        value = case.get(key)
        items = cast(list[Any], value) if isinstance(value, list) else None
        if value is not None and (items is None or not all(isinstance(v, str) for v in items)):
            raise ConfigError(f"{where}: `{key}` is a list of strings")
    table: Any = case.get("per_model") or {}
    if not isinstance(table, dict):
        raise ConfigError(f'{where}: `per_model` is a table of models, e.g. [per_model."gpt-image-1.5"]')
    for model, over in cast(dict[str, Any], table).items():
        if model not in models:
            raise ConfigError(f"{where}: per_model {model!r} is not a model of this experiment ({models})")
        unknown = sorted(set(cast(dict[str, Any], over)) - set(case["fields"]))
        if unknown:
            raise ConfigError(
                f"{where}: per_model {model!r} sets {unknown}, which are not fields of the case; an "
                "override replaces a field the case already has"
            )


def section(
    spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]], folder: Path
) -> dict[str, Any]:
    """`plan.json` `asked`: per setup its model, the resolved prompt and inputs after overrides, and
    whether (and for which cases) it is asked differently."""
    out: dict[str, Any] = {}
    for setup in setups:
        model = model_of(spec, setup)
        prompt, inputs = generate_for(spec.generate, model)
        name = str(setup["prompt"]) if "{prompt}" in prompt and "prompt" in setup else prompt
        found = prompt_file(folder / "prompts", name, model) if name.endswith(".md") else None
        diffs = {c["id"]: differences(spec, c, setup, folder) for c in cases}
        shared = {k: v for d in diffs.values() for k, v in d.items() if k != "case"}
        out[setup_id(setup)] = {
            "model": model,
            "prompt": str(found.relative_to(folder)) if found else prompt,
            "inputs": inputs,
            "asked_differently": any(diffs.values()),
            "overrides": shared,
            "case_overrides": {c: d["case"] for c, d in diffs.items() if "case" in d},
        }
    return out
