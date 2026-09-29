"""Cases a model cannot do (design change 0011 §3): the needs of a cell (the case's `needs` and the needs
its factor values create), judged against the model's stored guide, the plan's `applicability` section,
and the counts and lines of the results.

A need is a feature or input name (`"camera angle"`, `"references"`) or a limit (`"duration_s >= 60"`,
`"references >= 3"`, a factor value `"duration_s = 150"`, `"size = 1024x1024"`). A model meets a feature
only when its guide declares it; a limit the guide does not declare is `need_unknown` (the cell runs); no
guide at all makes every need `need_unknown`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from hone_select.experiments import generation, guides, overrides
from hone_select.experiments.definition import ExperimentSpec, setup_id
from hone_select.experiments.overrides import model_of

MET, UNMET, UNKNOWN = "met", "unmet", "unknown"
FACTOR_NEEDS = ("duration_s", "size")  # factors whose value is a need of the cell
LIMIT = re.compile(r"^\s*([A-Za-z_]\w*)\s*(>=|<=|=)\s*(\S+)\s*$")
LISTS = {"duration_s": "durations_s", "size": "sizes"}  # the guide's list of allowed values


def cell_needs(case: Mapping[str, Any], setup: Mapping[str, Any]) -> list[str]:
    """The case's `needs`, then one need per factor value a model may not take."""
    return [*(case.get("needs") or []), *(f"{f} = {setup[f]}" for f in FACTOR_NEEDS if f in setup)]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _compare(op: str, want: str, allowed: list[Any] | None, most: float | None) -> bool | None:
    """Whether a limit holds against the allowed values or the maximum; None when neither is declared."""
    if allowed:
        if op == "=":
            return want in {str(a) for a in allowed}
        values = [n for a in allowed if (n := _number(a)) is not None]
        most = max(values) if values else None
    if most is None:
        return None
    return op == "<=" or float(want) <= most


def _limit(name: str, op: str, want: str, g: Mapping[str, Any]) -> tuple[str, str]:
    allowed = g.get(LISTS.get(name, ""))
    most = _number(g.get(f"max_{name}"))
    try:
        held = _compare(op, want, cast(list[Any] | None, allowed), most)
    except ValueError:
        held = None
    if held is None:
        return UNKNOWN, f"{name} {op} {want}: the guide declares no limit"
    if held:
        return MET, ""
    limit = f"max_{name} {most:g}" if most is not None and not allowed else f"{LISTS.get(name)} {allowed}"
    return UNMET, f"{name} {op} {want} ({limit})"


def _names(value: Any) -> set[str]:
    """The names of a guide's `inputs` (a table of notes, or a list)."""
    items = cast(
        list[Any], list(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else value or []
    )
    return {str(i).lower() for i in items}


def judge(need: str, g: Mapping[str, Any] | None) -> tuple[str, str]:
    """(met / unmet / unknown, why) for one need against one guide."""
    if g is None:
        return UNKNOWN, f"{need}: no guide"
    if m := LIMIT.match(need):
        return _limit(m[1], m[2], m[3], g)
    features = {str(f.get("name")).lower() for f in cast(list[Mapping[str, Any]], g.get("features") or [])}
    if need.lower() in features | _names(g.get("inputs")):
        return MET, ""
    return UNMET, f"no feature {need!r}"


def section(
    spec: ExperimentSpec,
    cases: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    models: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """`plan.json` `applicability`: the cells not run (with the unmet needs) and the cells run with needs
    their model's guide cannot answer. None when no cell has a need."""
    plan = {"models": models or {}}
    na: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    any_need = False
    for setup in setups:
        model = model_of(spec, setup)
        for case in cases:
            wanted = cell_needs(case, setup)
            any_need = any_need or bool(wanted)
            found = [(n, *judge(n, guides.of(plan, model))) for n in wanted]
            cell = {"case": case["id"], "setup": setup_id(setup), "model": model}
            if unmet := [(n, why) for n, state, why in found if state == UNMET]:
                na.append(
                    cell | {"needs": [n for n, _ in unmet], "unmet": [f"{model}: {w}" for _, w in unmet]}
                )
            elif unsure := [n for n, state, _ in found if state == UNKNOWN]:
                unknown.append(cell | {"needs": unsure})
    return {"not_applicable": na, "need_unknown": unknown} if any_need else None


def _cells(plan: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    found = cast(Mapping[str, Any], plan.get("applicability") or {})
    return cast(list[Mapping[str, Any]], found.get(key) or [])


def skipped(plan: Mapping[str, Any]) -> set[tuple[str, str]]:
    """(case, setup) of every cell that is not applicable: not run."""
    return {(c["case"], c["setup"]) for c in _cells(plan, "not_applicable")}


def unknown_needs(plan: Mapping[str, Any], case: str, setup: str) -> list[str]:
    for c in _cells(plan, "need_unknown"):
        if (c["case"], c["setup"]) == (case, setup):
            return [str(n) for n in c["needs"]]
    return []


def cases_of(plan: Mapping[str, Any], setups: list[str]) -> set[str]:
    """The cases at least one of `setups` can do."""
    skip = skipped(plan)
    return {c for c in plan["cases"] for s in setups if (c, s) not in skip}


def setup_counts(plan: Mapping[str, Any], sid: str) -> dict[str, Any]:
    """`12 of 15 cases; 3 not applicable: camera angle`, as numbers."""
    na = [c for c in _cells(plan, "not_applicable") if c["setup"] == sid]
    needs: dict[str, int] = {}
    for c in na:
        for n in c["needs"]:
            needs[n] = needs.get(n, 0) + 1
    unsure = sum(c["setup"] == sid for c in _cells(plan, "need_unknown"))
    total = len(plan["cases"])
    return {
        "cases": total - len(na),
        "of": total,
        "not_applicable": len(na),
        "needs": needs,
        "need_unknown": unsure,
    }


def could_not(plan: Mapping[str, Any]) -> list[str]:
    """The summary's "what each model could not do" lines."""
    per: dict[str, dict[str, set[str]]] = {}
    for c in _cells(plan, "not_applicable"):
        for n in c["needs"]:
            per.setdefault(str(c["model"]), {}).setdefault(n, set()).add(str(c["case"]))
    return [
        f"`{m}` could not do: "
        + ", ".join(f"{n} ({len(cs)} of {len(plan['cases'])} cases)" for n, cs in needs.items())
        for m, needs in per.items()
    ]


def plan_parts(
    spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]], folder: Path
) -> dict[str, Any]:
    """The model-aware parts of `plan.json` (design change 0011 §4): `models` (the guides, read once),
    `asked` (how each setup's model is asked) and `applicability`. Raises `ConfigError` for a bad override."""
    overrides.check(spec, cases, setups)
    generation.check(spec, cases, setups, folder)
    out: dict[str, Any] = {}
    models = guides.read(spec, setups)
    if models is not None:
        out["models"] = models
    asked = overrides.section(spec, cases, setups, folder)
    if spec.generate.kind == "generate" or any(a["asked_differently"] for a in asked.values()):
        out["asked"] = asked
    if (found := section(spec, cases, setups, models)) is not None:
        out["applicability"] = found
    return out
