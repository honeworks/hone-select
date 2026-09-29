"""A/B results (design change 0012 §5): per pair of setups, wins, losses and ties of each side, the win rate
without ties with a 95 % Wilson interval, `clear` when the interval excludes 50 %, `complete`, and the
winner per case; plus the summary lines."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from hone_select.experiments import ab
from hone_select.experiments import definition as d

Z95 = 1.959963984540054  # the 97.5th percentile of the standard normal


def wilson(wins: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    """The Wilson score interval of a proportion (wins of n); (None, None) without trials."""
    if n <= 0:
        return None, None
    p = wins / n
    scale = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / scale
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    return max(0.0, centre - margin), min(1.0, centre + margin)


def section(folder: Path, spec: d.ExperimentSpec, drawn: dict[str, Any] | None) -> dict[str, Any]:
    """`results.json` `ab`: per criterion, whether the pairs are drawn and the numbers per pair of setups."""
    out: dict[str, Any] = {}
    for name, c in spec.ab_scorers().items():
        entry: dict[str, Any] = {"question": c.question, "ready": drawn is not None, "pairs": []}
        if drawn is not None:
            plan = drawn["criteria"][name]
            made = ab.picks(folder, name)
            chosen = ab.judged(plan["pairs"], made)
            entry["pairs"] = [_pair(pair, plan["pairs"], chosen, c.pairs) for pair in plan["setups"]]
        out[name] = entry
    return out


def _winner(p: dict[str, Any], choice: str) -> str | None:
    """The setup a pick chose; None for a tie."""
    if choice == "tie":
        return None
    shown = p["left"] if choice == "left" else p["right"]
    return p["pair"][0] if shown.split("__")[-2] == p["pair"][0] else p["pair"][1]


def _pair(
    pair: list[str], planned: list[dict[str, Any]], chosen: dict[int, str], requested: int
) -> dict[str, Any]:
    mine = [i for i, p in enumerate(planned) if p["pair"] == pair]
    won = [(planned[i]["case"], _winner(planned[i], chosen[i])) for i in mine if i in chosen]
    wins = {s: sum(w == s for _, w in won) for s in pair}
    ties = sum(w is None for _, w in won)
    a, b = sorted(pair, key=lambda s: -wins[s])  # the setup with more wins first (plan order on a draw)
    decided = wins[a] + wins[b]
    low, high = wilson(wins[a], decided)
    return {
        "setups": [a, b],
        "sides": {
            a: {"wins": wins[a], "losses": wins[b], "ties": ties},
            b: {"wins": wins[b], "losses": wins[a], "ties": ties},
        },
        "win_rate": round(wins[a] / decided, 4) if decided else None,
        "low": round(low, 4) if low is not None else None,
        "high": round(high, 4) if high is not None else None,
        "clear": low is not None and high is not None and (low > 0.5 or high < 0.5),
        "judged": len(won),
        "planned": len(mine),
        "requested": requested,
        "complete": bool(mine) and len(won) == len(mine),
        "cases": _cases(won, a, b),
    }


def _cases(won: list[tuple[str, str | None]], a: str, b: str) -> dict[str, str]:
    """Per case with picks: the setup with more wins there, or "tie"."""
    out: dict[str, str] = {}
    for case in dict.fromkeys(c for c, _ in won):
        wa = sum(c == case and w == a for c, w in won)
        wb = sum(c == case and w == b for c, w in won)
        out[case] = a if wa > wb else b if wb > wa else "tie"
    return dict(sorted(out.items()))


def lines(res: dict[str, Any]) -> list[str]:
    """The summary's A/B section: one line per pair of setups."""
    found: dict[str, Any] = res.get("ab") or {}
    out: list[str] = []
    for name, entry in found.items():
        if not entry["ready"]:
            out.append(f"- A/B ({name}): waiting for the run.")
        out += [f"- {_line(name, p)}" for p in entry["pairs"]]
    return ["", "## A/B", "", *out] if out else []


def _line(name: str, p: dict[str, Any]) -> str:
    a, b = p["setups"]
    if not p["judged"]:
        return f"A/B ({name}): {a} vs {b}, no picks yet."
    s = p["sides"][a]
    ties = {0: "no ties", 1: "1 tie"}.get(s["ties"], f"{s['ties']} ties")
    verb = "beats" if s["wins"] > s["losses"] else "is even with"
    text = f"A/B ({name}): {a} {verb} {b}, {s['wins']}-{s['losses']} with {ties}"
    if p["win_rate"] is not None:
        rate, low, high = (round(100 * p[k]) for k in ("win_rate", "low", "high"))
        text += f", win rate {rate} % ({low}-{high} %), {'clear' if p['clear'] else 'not clear'}"
    text += "."
    return text if p["complete"] else f"{text} {p['judged']} of {p['planned']} picks so far."
