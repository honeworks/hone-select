"""Samples that ran outside the run conditions (design change 0010 §9, §10): a sample's status, why it is
left out, and the counts and summary line of the results."""

from __future__ import annotations

from typing import Any, cast

from hone_select.experiments import definition as d

EXCLUDED = ("outside", "unknown")  # left out of every number unless --include-outside
SPEED = ("seconds",)  # left out for a cold sample, which includes loading its model


def environment_status(r: dict[str, Any]) -> str:
    """A sample's run-condition status; a `result.json` written before design change 0010 is not_checked."""
    env: dict[str, Any] = r.get("environment") or {}
    return str(env.get("status", "not_checked"))


def is_cold(r: dict[str, Any]) -> bool:
    """The sample included loading its model (no warm-up): its speed is not counted."""
    env: dict[str, Any] = r.get("environment") or {}
    return bool(env.get("cold"))


def off_conditions(env: dict[str, Any] | None) -> list[str]:
    """The conditions a sample did not hold (outside or unknown)."""
    found = cast(dict[str, dict[str, Any]], (env or {}).get("checks") or {})
    return [n for n, c in found.items() if c.get("state") != "ok"]


def counts(rs: list[dict[str, Any]], spec: d.ExperimentSpec, include: bool) -> dict[str, Any]:
    """`results.json` `conditions`: what was declared, how many samples were left out, and why."""
    reasons: dict[str, int] = {}
    excluded = [r for r in rs if r["environment_status"] in EXCLUDED]
    for r in excluded:
        for name in r["off_conditions"] or [r["environment_status"]]:
            reasons[name] = reasons.get(name, 0) + 1
    return {
        "declared": spec.conditions.model_dump(exclude_defaults=True),
        "excluded": 0 if include else len(excluded),
        "outside_or_unknown": len(excluded),
        "reasons": reasons,
        "not_checked": sum(r["environment_status"] == "not_checked" for r in rs)
        if spec.conditions.declared()
        else 0,
        "cold": sum(r["cold"] for r in rs),
        "include_outside": include,
    }


def conditions_line(res: dict[str, Any]) -> str:
    """The summary's first line when samples ran outside the run conditions (or were not checked)."""
    c: dict[str, Any] = res.get("conditions") or {}
    n, total = int(c.get("outside_or_unknown", 0)), res.get("samples", 0)
    reasons: dict[str, int] = c.get("reasons", {})
    why = ", ".join(f"{k} × {name}" for name, k in sorted(reasons.items(), key=lambda x: -x[1]))  # noqa: RUF001
    line = ""
    if n and c.get("include_outside"):
        line = f"{n} of {total} samples ran outside the run conditions ({why}); they are counted here."
    elif n:
        line = f"{n} of {total} samples ran outside the run conditions and are not counted ({why})."
    if c.get("not_checked"):
        line += f" {c['not_checked']} of {total} samples were not checked (they ran before the conditions)."
    return line.strip()


def out_of_memory(env: dict[str, Any], out: dict[str, Any], spec: d.ExperimentSpec) -> dict[str, Any]:
    """A generation that ran out of memory is a run-conditions signal (design change 0011 §1): with
    declared conditions the sample is `outside` (the machine was short), so it runs once more."""
    if out.get("error_kind") != "out_of_memory" or not spec.conditions.declared():
        return env
    check = {"state": "outside", "reason": f"out_of_memory: {out.get('error')}"}
    return env | {"status": "outside", "checks": {**env["checks"], "out_of_memory": check}}
