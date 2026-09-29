"""An experiment's results (design change 0009 §4): per setup, per factor level, against the baselines.

Every mean is taken per case first (over its samples), then over cases; the 95 % intervals come from a
seeded bootstrap over cases. Written to `results/results.json` and `results/summary.md`.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hone_select.experiments import applicable, ratings
from hone_select.experiments import definition as d
from hone_select.experiments.outside import (
    EXCLUDED,
    SPEED,
    counts,
    environment_status,
    is_cold,
    off_conditions,
)
from hone_select.experiments.project import now, read_json, write_json
from hone_select.experiments.summary import summary

RESAMPLES = 1000
MEASURES = ("seconds", "peak_memory_mb", "output_bytes")


def rows(folder: Path, spec: d.ExperimentSpec) -> list[dict[str, Any]]:
    """One row per sample: its setup, total, scores, measurements and ratings."""
    human = ratings.normalized(folder, spec)
    out: list[dict[str, Any]] = []
    for case_dir in sorted(p for p in (folder / "outputs").glob("*") if p.is_dir()):
        selection = read_json(case_dir / "selection.json", {"samples": {}, "winner": None})
        for p in sorted(case_dir.glob("*/*/result.json")):
            r = read_json(p)
            s = selection["samples"].get(r["sample_id"]) or selection.get("outside", {}).get(
                r["sample_id"], {}
            )
            out.append(
                {
                    "sample_id": r["sample_id"],
                    "case": r["case"],
                    "setup": r["setup"],
                    "params": r["params"],
                    "total": s.get("total"),
                    "rejected": s.get("rejected", bool(r.get("error"))),
                    "winner": r["sample_id"] == selection.get("winner"),
                    "error": r.get("error"),
                    "scores": {k: v.get("value") for k, v in s.get("scores", {}).items()},
                    "measurements": r.get("measurements", {}),
                    "cost_usd": r.get("cost_usd"),
                    "human": human.get(r["sample_id"], {}),
                    "environment_status": environment_status(r),
                    "environment": r.get("environment"),
                    "cold": is_cold(r),
                    "off_conditions": off_conditions(r.get("environment")),
                    "error_kind": r.get("error_kind"),
                    "license": r.get("license"),
                    "commercial_use": r.get("commercial_use"),
                    "asked_differently": bool(r.get("asked_differently")),
                    "need_unknown": r.get("need_unknown", []),
                }
            )
    return out


def _case_means(rs: Sequence[dict[str, Any]], value: Callable[[dict[str, Any]], Any]) -> dict[str, float]:
    by_case: dict[str, list[float]] = {}
    for r in rs:
        v = value(r)
        if isinstance(v, int | float):
            by_case.setdefault(r["case"], []).append(float(v))
    return {c: statistics.fmean(v) for c, v in by_case.items()}


def interval(values: Sequence[float], seed: int = 0) -> tuple[float | None, float | None, float | None]:
    """(mean, low, high): the mean and a 95 % bootstrap interval; None parts when there are no values."""
    if not values:
        return None, None, None
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean, mean, mean
    rng = random.Random(seed)  # noqa: S311 - a reproducible bootstrap, not a secret
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(RESAMPLES))
    return mean, means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def _stats(rs: Sequence[dict[str, Any]], spec: d.ExperimentSpec, seed: int) -> dict[str, Any]:
    mean, low, high = interval(list(_case_means(rs, lambda r: r["total"]).values()), seed)
    criteria = sorted({k for r in rs for k in r["scores"]} - {"ran_ok"})
    return {
        "samples": len(rs),
        "pass_rate": _round(sum(not r["rejected"] for r in rs) / len(rs)) if rs else None,
        "errors": sum(bool(r["error"]) for r in rs),
        "wins": len({r["case"] for r in rs if r["winner"]}),
        "total": {"mean": _round(mean), "low": _round(low), "high": _round(high)},
        "criteria": {c: _round(_mean(_case_means(rs, lambda r, c=c: r["scores"].get(c)))) for c in criteria},
        "human": {
            c: {
                "mean": _round(_mean(_case_means(rs, lambda r, c=c: r["human"].get(c)))),
                "rated": sum(c in r["human"] for r in rs),
                "complete": sum(c in r["human"] for r in rs) >= h.min_ratings,
            }
            for c, h in spec.human_scorers().items()
        },
        "measurements": {
            m: _round(_mean(_case_means(rs, lambda r, m=m: _measure(r, m))))
            for m in {k for r in rs for k in r["measurements"]}
        },
        "cost_usd": _known_sum(r["cost_usd"] for r in rs),
    }


def _measure(r: dict[str, Any], name: str) -> Any:
    """A measurement; None for the speed of a cold sample (design change 0010, open question 9)."""
    return None if r["cold"] and name in SPEED else r["measurements"].get(name)


def _counted(
    rs: Sequence[dict[str, Any]], spec: d.ExperimentSpec, seed: int, include: bool
) -> dict[str, Any]:
    """`_stats` over the samples that count, plus how many were left out."""
    kept = [r for r in rs if include or r["environment_status"] not in EXCLUDED]
    return {
        **_stats(kept, spec, seed),
        "outside": sum(r["environment_status"] == "outside" for r in rs),
        "unknown": sum(r["environment_status"] == "unknown" for r in rs),
    }


def _known_sum(values: Any) -> float | None:
    """The sum of the known values; None when none is known (an unknown cost is not $0)."""
    known = [v for v in values if isinstance(v, int | float)]
    return _round(sum(known)) if known else None


def _mean(by_case: dict[str, float]) -> float | None:
    return statistics.fmean(by_case.values()) if by_case else None


def _round(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None


def _baseline(rs: list[dict[str, Any]], base: str, setup: str, seed: int) -> dict[str, Any]:
    """The difference to a baseline over the cases both can do (design change 0011 §3), with how many."""
    a = _case_means([r for r in rs if r["setup"] == setup], lambda r: r["total"])
    b = _case_means([r for r in rs if r["setup"] == base], lambda r: r["total"])
    diffs = [a[c] - b[c] for c in a if c in b]
    mean, low, high = interval(diffs, seed)
    clear = low is not None and high is not None and (low > 0 or high < 0)
    return {
        "diff": _round(mean),
        "low": _round(low),
        "high": _round(high),
        "clear": clear,
        "shared_cases": len(diffs),
        "wins": sum(x > 0 for x in diffs),
        "losses": sum(x < 0 for x in diffs),
    }


def _agreement(rs: list[dict[str, Any]], pairs: list[list[str]]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for a, b in pairs:
        gaps = [
            abs(r["scores"][a] - r["scores"][b])
            for r in rs
            if isinstance(r["scores"].get(a), int | float) and isinstance(r["scores"].get(b), int | float)
        ]
        out[f"{a} vs {b}"] = _round(statistics.fmean(gaps)) if gaps else None
    return out


def compute(
    folder: Path, spec: d.ExperimentSpec, plan: dict[str, Any], *, include_outside: bool = False
) -> dict[str, Any]:
    """The results; samples outside the run conditions are left out of every number unless
    `include_outside` (design change 0010 §10)."""
    every = rows(folder, spec)
    rs = [r for r in every if include_outside or r["environment_status"] not in EXCLUDED]
    setups: dict[str, dict[str, Any]] = plan["setups"]

    def stats(rows_: list[dict[str, Any]]) -> dict[str, Any]:
        return _counted(rows_, spec, spec.seed, include_outside)

    per_setup = {
        sid: {"params": params, **stats([r for r in every if r["setup"] == sid]), **_asked(plan, sid)}
        for sid, params in setups.items()
    }
    per_factor = {f: _factor(every, plan, f, levels, stats) for f, levels in spec.factors.items()}
    ranked = sorted(per_setup, key=lambda s: -(per_setup[s]["total"]["mean"] or -1e9))
    winners = {
        c: sel["winner"]
        for c in plan["cases"]
        if (sel := read_json(folder / "outputs" / c / "selection.json")) and sel.get("winner")
    }
    return {
        "eid": plan["eid"],
        "title": spec.title,
        "question": spec.question,
        "computed_at": now(),
        "definition_hash": plan["definition_hash"],
        "best": ranked[0] if ranked else None,
        "ranking": ranked,
        "setups": per_setup,
        "factors": per_factor,
        "winners": winners,
        "baselines": {
            name: {sid: _baseline(rs, base, sid, spec.seed) for sid in setups if sid != base}
            for name, base in plan["baselines"].items()
        },
        "agreement": _agreement(rs, spec.criteria.compare),
        "samples": len(every),
        "conditions": counts(every, spec, include_outside),
        "models": _models(every, plan),
        "could_not": applicable.could_not(plan),
    }


def report(
    folder: Path, spec: d.ExperimentSpec, plan: dict[str, Any], *, include_outside: bool = False
) -> dict[str, Any]:
    """Compute the results and write `results/results.json` and `results/summary.md`."""
    res = compute(folder, spec, plan, include_outside=include_outside)
    write_json(folder / "results" / "results.json", res)
    (folder / "results" / "summary.md").write_text(summary(res))
    return res


def _asked(plan: dict[str, Any], sid: str) -> dict[str, Any]:
    """A setup's applicable cases (design change 0011 §3) and whether its model is asked differently."""
    every: dict[str, dict[str, Any]] = plan.get("asked") or {}
    asked = bool((every.get(sid) or {}).get("asked_differently"))
    return {"applicable": applicable.setup_counts(plan, sid), "asked_differently": asked}


def _factor(
    every: list[dict[str, Any]],
    plan: dict[str, Any],
    factor: str,
    levels: list[Any],
    stats: Callable[[list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    """Each level of a factor over the cases every level can do, with how many."""
    by_level = {
        str(level): [sid for sid, params in plan["setups"].items() if params.get(factor) == level]
        for level in levels
    }
    shared = set(plan["cases"])
    for sids in by_level.values():
        shared &= applicable.cases_of(plan, sids)
    return {
        str(level): stats([r for r in every if r["params"].get(factor) == level and r["case"] in shared])
        | {"shared_cases": len(shared)}
        for level in levels
    }


def _models(every: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    """License and commercial use per model, from the samples or the plan's guides; never a filter."""
    out: dict[str, Any] = {}
    models: dict[str, dict[str, Any]] = plan.get("models") or {}
    for model, entry in models.items():
        out[model] = {"license": entry.get("license"), "commercial_use": entry.get("commercial_use")}
    for r in every:
        model = r["params"].get("model")
        if model is not None and (r.get("license") is not None or r.get("commercial_use") is not None):
            out[str(model)] = {"license": r.get("license"), "commercial_use": r.get("commercial_use")}
    return out
