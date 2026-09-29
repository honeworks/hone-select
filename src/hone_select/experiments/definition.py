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


class PerModelSpec(_Model):
    """`[generate.per_model."<model>"]`: how one model is asked (design change 0011 §2)."""

    prompt: str | None = None  # replaces [generate] prompt for this model
    inputs: dict[str, Any] = Field(default_factory=dict[str, Any])  # merged over [generate] inputs


class GenerateSpec(_Model):
    """`[generate]`: the subject under test (design change 0009 §2a, 0011 §1)."""

    kind: Literal["prompt", "python", "command", "generate"]
    client: str | None = None  # prompt / generate: "module:factory" or an entry-point name
    client_args: dict[str, Any] = Field(default_factory=dict[str, Any])
    prompt: str = "{prompt}"  # prompt: a template, or "{prompt}" to take the file named by the factor
    system: str | None = None
    output: str = "text"  # prompt: "text" | "json"; generate: the output file name in the workdir
    inputs: dict[str, Any] = Field(default_factory=dict[str, Any])  # generate: named inputs, placeholders
    per_model: dict[str, PerModelSpec] = Field(default_factory=dict[str, PerModelSpec])
    guides: str | None = None  # a `hone.model_guides` entry point or "module:factory" (design change 0011)
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


CHECKS = (
    "max_cpu_load",
    "min_free_ram_gb",
    "min_free_vram_gb",
    "max_gpu_utilization_pct",
    "only_needed_models",
    "models_on_gpu",
    "gpu_lock",
)


class ConditionsSpec(_Model):
    """`[conditions]`: the state of the machine the experiment needs (design change 0010 §1). A key that is
    not set is not checked."""

    max_cpu_load: float | None = Field(default=None, ge=0, le=1)  # share of all cores busy over a window
    min_free_ram_gb: float | None = Field(default=None, ge=0)  # MemAvailable
    min_free_vram_gb: float | None = Field(default=None, ge=0)  # GPU 0, not counting the needed models
    max_gpu_utilization_pct: float | None = Field(default=None, ge=0, le=100)
    only_needed_models: bool = False  # unload the other models through the probe
    models_on_gpu: bool = False  # the needed models must be fully in VRAM
    models: list[str] | None = None  # the needed models (placeholders as in `command`)
    gpu_lock: bool | str = False  # hold the machine-wide GPU lock for the whole run (true, or a path)
    if_busy: Literal["block", "unload"] = "block"  # passed to the probe's prepare
    warm_up: bool = False  # load the missing needed models before a sample (the probe's `load`)
    on_violation: Literal["wait", "stop", "record_only"] = "wait"
    wait_timeout: float = Field(default=1800.0, gt=0)  # seconds one wait may last
    probe: str | None = None  # a `hone.machine_probes` entry point, e.g. "hone_models:machine"

    def declared(self) -> list[str]:
        """The conditions this experiment checks, in a fixed order."""
        return [name for name in CHECKS if getattr(self, name) not in (None, False, "")]


class HumanScorer(_Model):
    kind: Literal["human"]
    question: str
    scale: list[int] = Field(default_factory=lambda: [1, 5], min_length=2, max_length=2)
    min_ratings: int = Field(default=1, ge=1)  # per setup, before the criterion counts as complete


class ABScorer(_Model):
    """`kind = "ab"`: a person picks the better of two outputs of one case, blind (design change 0012)."""

    kind: Literal["ab"]
    question: str
    between: Literal["top", "baseline"] | list[str] = "top"  # or setup ids / baseline or setup names
    top: int = Field(default=2, ge=2)  # with between = "top": how many of the best setups
    pairs: int = Field(default=20, ge=1)  # pairs to judge per pair of setups
    allow_tie: bool = True


PERSON = {"human": HumanScorer, "ab": ABScorer}  # criteria a person judges: never in the automatic total


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
    conditions: ConditionsSpec = ConditionsSpec()

    def human_scorers(self) -> dict[str, HumanScorer]:
        return {n: HumanScorer.model_validate(s) for n, s in self.scorers.items() if s.get("kind") == "human"}

    def ab_scorers(self) -> dict[str, ABScorer]:
        return {n: ABScorer.model_validate(s) for n, s in self.scorers.items() if s.get("kind") == "ab"}

    def person_scorers(self) -> set[str]:
        """The names of the criteria a person judges (ratings and A/B)."""
        return {n for n, s in self.scorers.items() if s.get("kind") in PERSON}


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
        raise ConfigError(f"{path}: {_problems(e)}") from e
    for name, s in spec.scorers.items():
        model = PERSON.get(str(s.get("kind")))
        try:
            _ = model.model_validate(s) if model else None
        except ValidationError as e:
            raise ConfigError(f"{path}: [scorers.{name}] {_problems(e)}") from e
    _check(spec)
    return spec


def _problems(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())


def _check(spec: ExperimentSpec) -> None:
    g = spec.generate
    field = {"prompt": "client", "python": "function", "command": "command", "generate": "client"}[g.kind]
    if not getattr(g, field):
        raise ConfigError(f"[generate] kind = {g.kind!r} needs `{field}`")
    if g.kind == "prompt" and g.output not in ("text", "json"):
        raise ConfigError(f'[generate] output of a prompt subject is "text" or "json", not {g.output!r}')
    if g.kind == "generate" and (Path(g.output).is_absolute() or ".." in Path(g.output).parts):
        raise ConfigError(f"[generate] output {g.output!r} must be a file name inside the sample's folder")
    if g.per_model and g.kind not in ("prompt", "generate"):
        raise ConfigError(
            "[generate.per_model] applies to prompt and generate subjects; a python or command subject "
            "reads setup['model'] itself"
        )
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


def setup_names(spec: ExperimentSpec) -> dict[str, str]:
    """Every name a setup can be called by -> its setup id: the ids, the baselines' names and the names of
    `[[setup]]` entries (design change 0012 `between`)."""
    named = {str(s["name"]): setup_id(_fill(s, spec)) for s in spec.setup if "name" in s}
    return {sid: sid for sid in map(setup_id, setups(spec))} | named | baselines(spec)


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
