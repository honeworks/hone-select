"""A/B judgement (design change 0012): a person picks the better of two outputs of the same case, blind.

The pairs are drawn once, when they become available (generation and automatic scoring are done, so the
best setups are known), and fixed in `ab_plan.json`; picks are lines in `ab.jsonl`, append-only, where an
undo is a line `{"undo": <index of the line it removes>}`. Nothing served names a setup, a model or a
sample: a pair is its index in the criterion's plan, and its files are served by that index.
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path
from typing import Any

from hone_select.errors import ConfigError
from hone_select.experiments import definition as d
from hone_select.experiments.outside import EXCLUDED, environment_status
from hone_select.experiments.project import now, read_json, write_json

AB_PLAN = "ab_plan.json"
AB = "ab.jsonl"
CHOICES = ("left", "right", "tie")
SIDES = ("left", "right")


def criterion(spec: d.ExperimentSpec, name: str) -> d.ABScorer:
    found = spec.ab_scorers()
    if name not in found:
        raise ConfigError(f"{name!r} is not an A/B criterion of this experiment ({sorted(found)})")
    return found[name]


def check_definition(spec: d.ExperimentSpec) -> None:
    """At plan time: every name in `between` is a setup, and `between = "baseline"` has a baseline."""
    names, count = d.setup_names(spec), len(d.setups(spec))
    for name, c in spec.ab_scorers().items():
        if isinstance(c.between, list):
            unknown = [n for n in c.between if n not in names]
            if unknown:
                raise ConfigError(
                    f"[scorers.{name}] between names {unknown}, which are not setups; use setup ids "
                    "(see plan.json), baseline names or [[setup]] names"
                )
            if len({names[n] for n in c.between}) < 2:
                raise ConfigError(f"[scorers.{name}] between needs at least two different setups")
        elif c.between == "baseline" and not spec.baseline:
            raise ConfigError(f'[scorers.{name}] between = "baseline" needs a [[baseline]]')
        elif c.between == "top" and count < 2:
            raise ConfigError(f"[scorers.{name}] needs at least two setups to compare")


def setup_pairs(spec: d.ExperimentSpec, c: d.ABScorer, ranking: list[str]) -> list[list[str]]:
    """The pairs of setups to compare, the better-ranked (or the listed first) setup first."""
    if c.between == "top":
        return [list(p) for p in itertools.combinations(ranking[: c.top], 2)]
    names = d.setup_names(spec)
    if c.between == "baseline":
        base = next(iter(d.baselines(spec).values()))
        return [[sid, base] for sid in ranking if sid != base]
    ids = list(dict.fromkeys(names[n] for n in c.between))
    return [list(p) for p in itertools.combinations(ids, 2)]


def usable(folder: Path) -> dict[tuple[str, str], dict[int, str]]:
    """(case, setup) -> sample index -> sample id, for the samples a person may see: not failed, not
    outside or unknown to the run conditions."""
    out: dict[tuple[str, str], dict[int, str]] = {}
    for p in sorted(folder.glob("outputs/*/*/*/result.json")):
        r = read_json(p)
        if not r.get("error") and environment_status(r) not in EXCLUDED:
            out.setdefault((r["case"], r["setup"]), {})[int(r["sample"])] = r["sample_id"]
    return out


def matches(a: dict[int, str], b: dict[int, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The pairs of one case: the same sample index where both setups have it, and the samples left over
    (one setup failed a sample the other has) paired in order."""
    common = sorted(set(a) & set(b))
    rest = zip(sorted(set(a) - set(common)), sorted(set(b) - set(common)), strict=False)
    return [(a[k], b[k]) for k in common], [(a[i], b[j]) for i, j in rest]


def _options(a: dict[int, str], b: dict[int, str], rng: random.Random) -> list[tuple[str, str]]:
    """A case's pairs in a seeded order, the same-index pairs last (they are taken first, from the end)."""
    same, rest = matches(a, b)
    rng.shuffle(same)
    rng.shuffle(rest)
    return rest + same


def draw(
    pair: list[str],
    cases: list[str],
    found: dict[tuple[str, str], dict[int, str]],
    n: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Up to n pairs of one pair of setups, spread over the cases as evenly as possible (round robin over
    the cases in a seeded order), each with a seeded left and right."""
    a, b = pair
    per_case = {c: _options(found.get((c, a), {}), found.get((c, b), {}), rng) for c in cases}
    order = list(cases)
    rng.shuffle(order)
    picked: list[dict[str, Any]] = []
    while len(picked) < n and any(per_case[c] for c in order):
        for c in order:
            if per_case[c] and len(picked) < n:
                sa, sb = per_case[c].pop()
                left, right = (sa, sb) if rng.random() < 0.5 else (sb, sa)
                picked.append({"pair": pair, "case": c, "left": left, "right": right})
    return picked


def ready(folder: Path, plan: dict[str, Any]) -> bool:
    """The pairs are available once every case has its selection (generation and scoring are done)."""
    return all((folder / "outputs" / c / "selection.json").is_file() for c in plan["cases"])


def fixed(
    folder: Path, spec: d.ExperimentSpec, plan: dict[str, Any], ranking: list[str] | None
) -> dict[str, Any] | None:
    """`ab_plan.json` for the current plan, drawn and written the first time the pairs are available;
    None while they are not (or the experiment has no A/B criterion)."""
    current = read_json(folder / AB_PLAN)
    if current and current["definition_hash"] == plan["definition_hash"]:
        return current
    criteria = spec.ab_scorers()
    if not criteria or ranking is None or not ready(folder, plan):
        return None
    found = usable(folder)
    drawn: dict[str, Any] = {}
    for name, c in criteria.items():
        rng = random.Random(f"{spec.seed}:ab:{name}")  # noqa: S311 - a reproducible draw, not a secret
        pairs = setup_pairs(spec, c, ranking)
        items = [x for p in pairs for x in draw(p, plan["cases"], found, c.pairs, rng)]
        rng.shuffle(items)  # the pairs of setups mixed, so no run of picks is about one pair
        drawn[name] = {"setups": pairs, "pairs": items}
    out = {"definition_hash": plan["definition_hash"], "drawn_at": now(), "criteria": drawn}
    write_json(folder / AB_PLAN, out)
    return out


# -- picks ---------------------------------------------------------------------------------------------


def load(folder: Path) -> list[dict[str, Any]]:
    """Every line of `ab.jsonl`: picks and undos."""
    path = folder / AB
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
    )


def picks(folder: Path, name: str) -> list[tuple[int, dict[str, Any]]]:
    """The picks of criterion `name` that are not undone, with their line index."""
    lines = load(folder)
    undone = {line["undo"] for line in lines if "undo" in line}
    return [
        (i, line)
        for i, line in enumerate(lines)
        if "undo" not in line and i not in undone and line.get("criterion") == name
    ]


def planned(folder: Path, spec: d.ExperimentSpec, name: str) -> list[dict[str, Any]] | None:
    """The fixed pairs of criterion `name`; None while they wait for the run."""
    plan = read_json(folder / "plan.json")
    if plan is None:
        return None
    res: dict[str, Any] = read_json(folder / "results" / "results.json") or {}
    drawn = fixed(folder, spec, plan, res.get("ranking"))
    return None if drawn is None else drawn["criteria"][name]["pairs"]


def judged(pairs: list[dict[str, Any]], made: list[tuple[int, dict[str, Any]]]) -> dict[int, str]:
    """Plan index -> the choice made for it."""
    index = {(p["left"], p["right"]): i for i, p in enumerate(pairs)}
    found = ((index.get((line["left"], line["right"])), line["choice"]) for _, line in made)
    return {i: choice for i, choice in found if i is not None}


def next_pair(folder: Path, spec: d.ExperimentSpec, name: str) -> dict[str, Any]:
    """The next pair to judge, without setup names: `{"state": "waiting" | "done" | "pair", ...}`."""
    c = criterion(spec, name)
    pairs = planned(folder, spec, name)
    if pairs is None:
        return {"state": "waiting"}
    done = judged(pairs, picks(folder, name))
    todo = [i for i in range(len(pairs)) if i not in done]
    if not todo:
        return {"state": "done", "judged": len(done), "total": len(pairs)}
    p = pairs[todo[0]]
    left = read_json(_sample(folder, p["left"]) / "result.json")
    right = read_json(_sample(folder, p["right"]) / "result.json")
    return {
        "state": "pair",
        "index": todo[0],
        "question": c.question,
        "allow_tie": c.allow_tie,
        "case": p["case"],
        "case_fields": left.get("judge_view", left.get("case_fields", {})),  # never a model's override
        "left": {"data": left.get("data"), "files": sorted(left.get("files", {}))},
        "right": {"data": right.get("data"), "files": sorted(right.get("files", {}))},
        "judged": len(done),
        "total": len(pairs),
        "can_undo": bool(done),
    }


def _sample(folder: Path, sample_id: str) -> Path:
    case, setup, k = sample_id.rsplit("__", 2)
    return folder / "outputs" / case / setup / k


def _pair(folder: Path, spec: d.ExperimentSpec, name: str, index: int) -> dict[str, Any]:
    pairs = planned(folder, spec, name)
    if pairs is None:
        raise ConfigError(f"the pairs of {name!r} are not drawn yet: they wait for the run to finish")
    if not 0 <= index < len(pairs):
        raise ConfigError(f"no pair {index} for {name!r} (there are {len(pairs)})")
    return pairs[index]


def add(folder: Path, spec: d.ExperimentSpec, name: str, index: int, choice: str) -> None:
    """Record a pick for the pair at `index` of the criterion's plan: "left", "right" or "tie"."""
    c = criterion(spec, name)
    if choice not in CHOICES or (choice == "tie" and not c.allow_tie):
        allowed = CHOICES if c.allow_tie else SIDES
        raise ConfigError(f"a pick for {name!r} is one of {list(allowed)}, not {choice!r} (see allow_tie)")
    p = _pair(folder, spec, name, index)
    if (p["left"], p["right"]) in {(line["left"], line["right"]) for _, line in picks(folder, name)}:
        raise ConfigError(f"pair {index} of {name!r} is already picked; undo it first")
    line = {"criterion": name, "pair": p["pair"], "case": p["case"], "left": p["left"], "right": p["right"]}
    _append(folder, line | {"choice": choice, "at": now()})


def undo(folder: Path, spec: d.ExperimentSpec, name: str) -> None:
    """Remove the last pick of criterion `name` (by appending an undo line)."""
    criterion(spec, name)
    made = picks(folder, name)
    if not made:
        raise ConfigError(f"no pick of {name!r} to undo")
    _append(folder, {"undo": made[-1][0]})


def _append(folder: Path, line: dict[str, Any]) -> None:
    with (folder / AB).open("a") as f:
        f.write(json.dumps(line) + "\n")


def file(
    folder: Path, spec: d.ExperimentSpec, name: str, index: int, *, side: str, relative: str
) -> Path | None:
    """A file of one side of a pair, served by the pair's index (the path would name the setup)."""
    criterion(spec, name)
    if side not in SIDES:
        return None
    base = (_sample(folder, _pair(folder, spec, name, index)[side]) / "files").resolve()
    target = (base / relative).resolve()
    return target if target.is_relative_to(base) and target.is_file() else None
