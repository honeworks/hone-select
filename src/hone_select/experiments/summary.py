"""`results/summary.md` (design change 0009 §4): the best setup, the setups, each factor and each baseline,
with the run-condition line (0010) and, from design change 0011, each setup's applicable cases, the setups
whose model is asked differently, what each model could not do and which models are non-commercial."""

from __future__ import annotations

from typing import Any

from hone_select.experiments.ab_results import lines as ab_lines
from hone_select.experiments.outside import conditions_line

ASKED = " (asked differently)"


def _total(s: dict[str, Any]) -> str:
    t = s["total"]
    if not s["samples"] and (s.get("outside") or s.get("unknown")):
        return "no samples in conditions"
    return f"{t['mean']} ({t['low']} to {t['high']})"


def _cases(s: dict[str, Any]) -> str:
    """`12 of 15; 3 not applicable: camera angle`."""
    a = s.get("applicable")
    if not a:
        return ""
    text = f"{a['cases']} of {a['of']}"
    if a["not_applicable"]:
        text += f"; {a['not_applicable']} not applicable: {', '.join(a['needs'])}"
    return text


def _setups(res: dict[str, Any]) -> list[str]:
    lines = [
        "## Setups (best first)",
        "",
        "| setup | params | cases | total (95 % interval) | pass rate | wins |",
        "|---|---|---|---|---|---|",
    ]
    for sid in res["ranking"]:
        s = res["setups"][sid]
        name = f"`{sid}`" + (ASKED if s.get("asked_differently") else "")
        lines.append(
            f"| {name} | {s['params']} | {_cases(s)} | {_total(s)} | {s['pass_rate']} | {s['wins']} |"
        )
    if any(s.get("asked_differently") for s in res["setups"].values()):
        lines += [
            "",
            "Setups marked asked differently give their model its own prompt or inputs (see plan.json).",
        ]
    return lines


def _factors(res: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    total = next((s["applicable"]["of"] for s in res["setups"].values() if s.get("applicable")), None)
    for factor, levels in res["factors"].items():
        stats: list[dict[str, Any]] = list(levels.values())
        shared = stats[0].get("shared_cases") if stats else None
        over = f" (over the {shared} cases every level can do)" if shared != total else ""
        lines += [
            "",
            f"## {factor}{over}",
            "",
            "| level | total (95 % interval) | pass rate | wins |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {level} | {_total(s)} | {s['pass_rate']} | {s['wins']} |" for level, s in levels.items()
        ]
    return lines


def _baselines(res: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for name, diffs in res["baselines"].items():
        lines += [
            "",
            f"## Against baseline `{name}` (on the cases both can do)",
            "",
            "| setup | difference (95 % interval) | clear | shared cases | wins / losses |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| `{sid}` | {v['diff']} ({v['low']} to {v['high']}) | {'yes' if v['clear'] else 'no'} "
            f"| {v.get('shared_cases', '')} | {v.get('wins', '')} / {v.get('losses', '')} |"
            for sid, v in diffs.items()
        ]
    return lines


def _models(res: dict[str, Any]) -> list[str]:
    could_not: list[str] = res.get("could_not") or []
    lines = ["", "## What each model could not do", "", *(f"- {c}" for c in could_not)] if could_not else []
    models: dict[str, dict[str, Any]] = res.get("models") or {}
    marks = [
        f"- `{m}`: non-commercial ({info.get('license') or 'license not declared'})"
        for m, info in models.items()
        if info.get("commercial_use") is False
    ]
    return [*lines, "", "## Licenses", "", *marks] if marks else lines


def summary(res: dict[str, Any]) -> str:
    first = conditions_line(res)
    best = f"{res['setups'][res['best']]['params']}" if res["best"] else ""
    lines = [
        f"# {res['eid']}: {res['title']}",
        "",
        *([first, ""] if first else []),
        res["question"],
        "",
        f"**Best setup:** `{res['best']}` {best}",
        "",
        *_setups(res),
        *_factors(res),
        *_baselines(res),
        *ab_lines(res),
        *_models(res),
    ]
    return "\n".join(lines) + "\n"
