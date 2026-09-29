"""A person's ratings for `kind = "human"` criteria (design change 0009 §2b), blind to the setup.

Ratings are lines in `ratings.jsonl`; the next output to rate comes in a fixed random order per criterion,
skipping failed samples and outputs already rated for that criterion.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from hone_select.errors import ConfigError
from hone_select.experiments import definition as d
from hone_select.experiments.project import now, read_json

RATINGS = "ratings.jsonl"


def load(folder: Path) -> list[dict[str, Any]]:
    path = folder / RATINGS
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
    )


def rateable(folder: Path) -> list[dict[str, Any]]:
    return [
        r
        for r in (read_json(p) for p in sorted(folder.glob("outputs/*/*/*/result.json")))
        if not r.get("error")
    ]


def next_output(folder: Path, spec: d.ExperimentSpec, criterion: str) -> dict[str, Any] | None:
    """The next output to rate for `criterion`, without its setup; None when all are rated."""
    human = spec.human_scorers()
    if criterion not in human:
        raise ConfigError(f"{criterion!r} is not a human criterion of this experiment ({sorted(human)})")
    done = {r["sample_id"] for r in load(folder) if r["criterion"] == criterion}
    todo = [r for r in rateable(folder) if r["sample_id"] not in done]
    if not todo:
        return None
    random.Random(f"{spec.seed}:{criterion}").shuffle(todo)  # noqa: S311 - a stable order, not a secret  # the same order every time, not the run order
    r = todo[0]
    return {
        "sample_id": r["sample_id"],
        "case": r["case"],
        "case_fields": r.get("case_fields", {}),
        "data": r.get("data"),
        "files": sorted(r.get("files", {})),
        "file_base": f"outputs/{r['case']}/{r['setup']}/s{r['sample']}/files",
        "question": human[criterion].question,
        "scale": human[criterion].scale,
        "remaining": len(todo),
        "total": len(todo) + len(done),
    }


def add(
    folder: Path, spec: d.ExperimentSpec, sample_id: str, criterion: str, value: int, *, by: str = "owner"
) -> None:
    human = spec.human_scorers()
    if criterion not in human:
        raise ConfigError(f"{criterion!r} is not a human criterion of this experiment")
    low, high = human[criterion].scale
    if not low <= value <= high:
        raise ConfigError(f"a {criterion} rating is between {low} and {high}, not {value}")
    if sample_id not in {r["sample_id"] for r in rateable(folder)}:
        raise ConfigError(f"no rateable output {sample_id!r}")
    line = {"sample_id": sample_id, "criterion": criterion, "value": value, "by": by, "at": now()}
    with (folder / RATINGS).open("a") as f:
        f.write(json.dumps(line) + "\n")


def normalized(folder: Path, spec: d.ExperimentSpec) -> dict[str, dict[str, float]]:
    """sample id -> criterion -> mean rating scaled to 0..1."""
    human = spec.human_scorers()
    sums: dict[tuple[str, str], list[float]] = {}
    for r in load(folder):
        if r["criterion"] in human:
            low, high = human[r["criterion"]].scale
            sums.setdefault((r["sample_id"], r["criterion"]), []).append((r["value"] - low) / (high - low))
    out: dict[str, dict[str, float]] = {}
    for (sid, crit), values in sums.items():
        out.setdefault(sid, {})[crit] = sum(values) / len(values)
    return out
