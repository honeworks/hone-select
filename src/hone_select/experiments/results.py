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

from hone_select.experiments import definition as d
from hone_select.experiments import ratings
from hone_select.experiments.project import now, read_json, write_json

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
            s = selection["samples"].get(r["sample_id"], {})
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
                    "cost_usd": r.get("cost_usd", 0.0) or 0.0,
                    "human": human.get(r["sample_id"], {}),
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
            m: _round(_mean(_case_means(rs, lambda r, m=m: r["measurements"].get(m))))
            for m in {k for r in rs for k in r["measurements"]}
        },
        "cost_usd": _round(sum(r["cost_usd"] for r in rs)),
    }


def _mean(by_case: dict[str, float]) -> float | None:
    return statistics.fmean(by_case.values()) if by_case else None


def _round(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None


def _baseline(rs: list[dict[str, Any]], base: str, setup: str, seed: int) -> dict[str, Any]:
    a = _case_means([r for r in rs if r["setup"] == setup], lambda r: r["total"])
    b = _case_means([r for r in rs if r["setup"] == base], lambda r: r["total"])
    mean, low, high = interval([a[c] - b[c] for c in a if c in b], seed)
    clear = low is not None and high is not None and (low > 0 or high < 0)
    return {"diff": _round(mean), "low": _round(low), "high": _round(high), "clear": clear}


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


def compute(folder: Path, spec: d.ExperimentSpec, plan: dict[str, Any]) -> dict[str, Any]:
    rs = rows(folder, spec)
    setups: dict[str, dict[str, Any]] = plan["setups"]
    per_setup = {
        sid: {"params": params, **_stats([r for r in rs if r["setup"] == sid], spec, spec.seed)}
        for sid, params in setups.items()
    }
    per_factor = {
        f: {
            str(level): _stats([r for r in rs if r["params"].get(f) == level], spec, spec.seed)
            for level in levels
        }
        for f, levels in spec.factors.items()
    }
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
    }


def report(folder: Path, spec: d.ExperimentSpec, plan: dict[str, Any]) -> dict[str, Any]:
    """Compute the results and write `results/results.json` and `results/summary.md`."""
    res = compute(folder, spec, plan)
    write_json(folder / "results" / "results.json", res)
    (folder / "results" / "summary.md").write_text(summary(res))
    return res


def summary(res: dict[str, Any]) -> str:
    lines = [
        f"# {res['eid']}: {res['title']}",
        "",
        res["question"],
        "",
        f"**Best setup:** `{res['best']}` {res['setups'][res['best']]['params'] if res['best'] else ''}",
        "",
        "## Setups (best first)",
        "",
        "| setup | params | total (95 % interval) | pass rate | wins |",
        "|---|---|---|---|---|",
    ]
    for sid in res["ranking"]:
        s = res["setups"][sid]
        t = s["total"]
        lines.append(
            f"| `{sid}` | {s['params']} | {t['mean']} ({t['low']} to {t['high']}) | {s['pass_rate']} | "
            f"{s['wins']} |"
        )
    for factor, levels in res["factors"].items():
        lines += [
            "",
            f"## {factor}",
            "",
            "| level | total (95 % interval) | pass rate | wins |",
            "|---|---|---|---|",
        ]
        for level, s in levels.items():
            t = s["total"]
            lines.append(
                f"| {level} | {t['mean']} ({t['low']} to {t['high']}) | {s['pass_rate']} | {s['wins']} |"
            )
    for name, diffs in res["baselines"].items():
        lines += [
            "",
            f"## Against baseline `{name}`",
            "",
            "| setup | difference (95 % interval) | clear |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{sid}` | {v['diff']} ({v['low']} to {v['high']}) | {'yes' if v['clear'] else 'no'} |"
            for sid, v in diffs.items()
        ]
    return "\n".join(lines) + "\n"
