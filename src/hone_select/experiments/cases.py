"""Test cases (design change 0009 §1, §2a): fields and files, from a folder or from an earlier experiment."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from hone_select.errors import ConfigError

Case = dict[str, Any]  # {"id": str, "fields": {...}, "files": {name: absolute path}}


def load(folder: Path, source: str | list[str] | dict[str, str], experiments: Path) -> list[Case]:
    """The experiment's cases, in a stable order. `experiments` is the project's experiments folder."""
    if isinstance(source, dict):
        return from_experiment(experiments, source.get("from", ""), source.get("keep", "winners"))
    paths = [folder / s for s in ([source] if isinstance(source, str) else source)]
    cases: dict[str, Case] = {}
    for path in paths:
        for case in _from_path(path):
            if case["id"] in cases:
                raise ConfigError(f"two test cases have the id {case['id']!r}")
            cases[case["id"]] = case
    if not cases:
        raise ConfigError(
            f"no test cases in {[str(p) for p in paths]}: add cases.toml or one folder per case"
        )
    return list(cases.values())


def _from_path(path: Path) -> list[Case]:
    if path.is_file():
        return _from_toml(path)
    if not path.is_dir():
        raise ConfigError(f"cases path {str(path)!r} does not exist")
    listed = {
        c["id"]: c for c in (_from_toml(path / "cases.toml") if (path / "cases.toml").is_file() else [])
    }
    for sub in sorted(p for p in path.iterdir() if p.is_dir()):
        case = listed.setdefault(sub.name, {"id": sub.name, "fields": {}, "files": {}})
        case["files"] |= {f.name: str(f.resolve()) for f in sorted(sub.iterdir()) if f.is_file()}
    return list(listed.values())


def _from_toml(path: Path) -> list[Case]:
    try:
        rows = tomllib.loads(path.read_text()).get("case", [])
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from e
    cases: list[Case] = []
    for row in rows:
        if "id" not in row:
            raise ConfigError(f"{path}: every [[case]] needs an id")
        if any(c["id"] == str(row["id"]) for c in cases):
            raise ConfigError(f"{path}: two test cases have the id {str(row['id'])!r}")
        cases.append(
            {"id": str(row["id"]), "fields": {k: v for k, v in row.items() if k != "id"}, "files": {}}
        )
    return cases


def from_experiment(experiments: Path, eid: str, keep: str) -> list[Case]:
    """Cases made from an earlier experiment's outputs: its case winners (`keep = "winners"`) or every
    sample (`"all"`). Each new case has the source case's fields, the source setup, its data and files."""
    matches = sorted(experiments.glob(f"{eid}-*")) + (
        [experiments / eid] if (experiments / eid).is_dir() else []
    )
    if not matches:
        raise ConfigError(f"cases from {eid!r}: no such experiment in {str(experiments)!r}")
    source = matches[0]
    results = source / "results" / "results.json"
    if keep == "winners" and not results.is_file():
        raise ConfigError(f"cases from {eid!r}: it has no results yet (run it, then `report`)")
    winners = set(json.loads(results.read_text()).get("winners", {}).values()) if keep == "winners" else None
    cases: list[Case] = []
    for result in sorted((source / "outputs").glob("*/*/*/result.json")):
        r = json.loads(result.read_text())
        if r.get("error") or (winners is not None and r["sample_id"] not in winners):
            continue
        files = {p.name: str(p.resolve()) for p in sorted((result.parent / "files").glob("*")) if p.is_file()}
        fields = dict(r.get("case_fields", {})) | {
            "source_case": r["case"],
            "source_setup": r["setup"],
            "input": r.get("data"),
        }
        cases.append({"id": r["sample_id"], "fields": fields, "files": files})
    if not cases:
        raise ConfigError(f"cases from {eid!r}: no usable outputs (keep = {keep!r})")
    return cases
