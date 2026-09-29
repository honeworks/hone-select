"""An experiment's definition (design change 0009): `experiment.toml`, its test cases and its setups."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hone_select.errors import ConfigError

DEFINITION = "experiment.toml"
HASHED = ("experiment.toml", "cases", "prompts", "scripts")  # what defines an experiment (its hash)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerateSpec(_Model):
    """`[generate]`: the subject under test (design change 0009 §2a)."""

    kind: Literal["prompt", "python", "command"]
    client: str | None = None  # prompt: "module:factory" returning a TextClient
    client_args: dict[str, Any] = Field(default_factory=dict[str, Any])
    prompt: str = "{prompt}"  # prompt: a template, or "{prompt}" to take the file named by the factor
    system: str | None = None
    output: Literal["text", "json"] = "text"
    params: list[str] | None = (
        None  # prompt: factors passed to the model call (default: all but model/prompt)
    )
    function: str | None = None  # python: "module:function"
    command: list[str] | None = None  # command: argv with {case.*} {setup.*} {setup_json} {workdir} {seed}
    wrap: list[str] = Field(default_factory=list[str])
    env: dict[str, str] = Field(default_factory=dict[str, str])
    timeout: float = Field(default=1800.0, gt=0)
    retries: int = Field(default=0, ge=0)
    keep_files: Literal["all", "small", "none"] = "all"
    after_group: str | None = None  # "module:function" called when the `run.order` factor changes


class DesignSpec(_Model):
    kind: Literal["full", "one_at_a_time", "list"] = "full"


class CriteriaSpec(_Model):
    gates: list[str] = Field(default_factory=list[str])
    scorers: list[str] = Field(default_factory=list[str])
    weights: dict[str, float] = Field(default_factory=dict[str, float])
    measure: dict[str, Literal["lower", "higher"]] = Field(
        default_factory=dict[str, Literal["lower", "higher"]]
    )
    compare: list[list[str]] = Field(
        default_factory=list[list[str]]
    )  # scorer pairs whose agreement is reported


class RunSpec(_Model):
    order: str | None = "model"  # run all cells of one value of this factor before the next
    concurrency: int = Field(default=1, ge=1, le=1)  # v0.1 runs one sample at a time


class BudgetSpec(_Model):
    money_usd: float | None = Field(default=None, gt=0)
    seconds: float | None = Field(default=None, gt=0)


class HumanScorer(_Model):
    kind: Literal["human"]
    question: str
    scale: list[int] = Field(default_factory=lambda: [1, 5], min_length=2, max_length=2)
    min_ratings: int = Field(default=1, ge=1)  # per setup, before the criterion counts as complete


class ExperimentSpec(_Model):
    title: str
    question: str = ""
    cases: str | list[str] | dict[str, str] = "cases/"
    samples: int = Field(default=1, ge=1)
    seed: int = 0
    registry: list[str] = Field(default_factory=list[str])
    generate: GenerateSpec
    factors: dict[str, list[Any]] = Field(default_factory=dict[str, list[Any]])
    design: DesignSpec = DesignSpec()
    baseline: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    setup: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])  # design "list"
    criteria: CriteriaSpec = CriteriaSpec()
    judges: dict[str, dict[str, Any]] = Field(default_factory=dict[str, dict[str, Any]])
    scorers: dict[str, dict[str, Any]] = Field(default_factory=dict[str, dict[str, Any]])
    budget: BudgetSpec = BudgetSpec()
    run: RunSpec = RunSpec()

    def human_scorers(self) -> dict[str, HumanScorer]:
        return {n: HumanScorer.model_validate(s) for n, s in self.scorers.items() if s.get("kind") == "human"}


def load(folder: Path) -> ExperimentSpec:
    """Read and validate `experiment.toml` in `folder`."""
    path = folder / DEFINITION
    if not path.is_file():
        raise ConfigError(f"no {DEFINITION} in {str(folder)!r}")
    try:
        spec = ExperimentSpec.model_validate(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from e
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"{path}: {problems}") from e
    _check(spec)
    return spec


def _check(spec: ExperimentSpec) -> None:
    g = spec.generate
    needed = {"prompt": g.client, "python": g.function, "command": g.command}[g.kind]
    if not needed:
        field = {"prompt": "client", "python": "function", "command": "command"}[g.kind]
        raise ConfigError(f"[generate] kind = {g.kind!r} needs `{field}`")
    if spec.design.kind == "list" and not spec.setup:
        raise ConfigError('design kind = "list" needs [[setup]] entries')
    for base in spec.baseline:
        unknown = set(base) - set(spec.factors) - {"name"}
        if unknown:
            raise ConfigError(f"baseline {base.get('name')!r} sets {sorted(unknown)}, which are not factors")


def setup_id(setup: dict[str, Any]) -> str:
    """A short, stable, readable id for a setup (folder name): values joined, plus a hash."""
    values = "-".join(re.sub(r"[^A-Za-z0-9.]+", "", str(v))[:16] for v in setup.values()) or "default"
    digest = hashlib.sha256(json.dumps(setup, sort_keys=True, default=str).encode()).hexdigest()[:6]
    return f"{values[:60]}-{digest}"


def setups(spec: ExperimentSpec) -> list[dict[str, Any]]:
    """Every setup to run, baselines first, without duplicates."""
    names = list(spec.factors)
    bases = [{k: v for k, v in b.items() if k != "name"} for b in spec.baseline]
    if spec.design.kind == "list":
        grid = [dict(s) for s in spec.setup]
    elif spec.design.kind == "full" or not bases:
        grid = [dict(zip(names, combo, strict=True)) for combo in itertools.product(*spec.factors.values())]
    else:  # one_at_a_time around the first baseline
        grid = [{**bases[0], name: level} for name in names for level in spec.factors[name]]
    complete = [_fill(s, spec) for s in bases + grid]
    unique: dict[str, dict[str, Any]] = {}
    for s in complete:
        unique.setdefault(setup_id(s), s)
    return list(unique.values())


def _fill(setup: dict[str, Any], spec: ExperimentSpec) -> dict[str, Any]:
    """A setup with every factor set (a missing factor takes its first level), in factor order."""
    return {name: setup.get(name, levels[0]) for name, levels in spec.factors.items()} | {
        k: v for k, v in setup.items() if k not in spec.factors and k != "name"
    }


def baselines(spec: ExperimentSpec) -> dict[str, str]:
    """Baseline name -> setup id."""
    return {
        str(b.get("name", f"baseline{i + 1}")): setup_id(_fill(b, spec)) for i, b in enumerate(spec.baseline)
    }


def definition_hash(folder: Path) -> str:
    """sha256 over the files that define the experiment (the definition, cases, prompts, scripts)."""
    h = hashlib.sha256()
    for name in HASHED:
        path = folder / name
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for f in files:
            if f.is_file():
                h.update(str(f.relative_to(folder)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()
