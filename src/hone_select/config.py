"""The selection config: TOML -> Pydantic models, plus checks against the registry."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hone_select.errors import ConfigError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JudgeConfig(BaseModel):
    """``[judges.<name>]``: ``client`` is an entry-point name; the other keys go to its factory."""

    model_config = ConfigDict(extra="allow", frozen=True)
    client: str


class GenerateConfig(_Model):
    n: int = Field(default=4, ge=1)
    vary: dict[str, str | list[Any]] = Field(default_factory=dict[str, str | list[Any]])
    max_concurrency: int = Field(default=1, ge=1)  # v0.1 generates sequentially (design/decisions.md D-005)

    @field_validator("vary")
    @classmethod
    def _check_vary(cls, vary: dict[str, str | list[Any]]) -> dict[str, str | list[Any]]:
        for key, values in vary.items():
            if key == "seed" and values == "increment":
                continue
            if isinstance(values, list) and values:
                if key == "seed" and not all(isinstance(v, int) for v in values):
                    raise ValueError("vary.seed must be 'increment' or a list of integers")
                continue
            hint = " or 'increment'" if key == "seed" else ""
            raise ValueError(f"vary.{key} must be a non-empty list{hint}")
        return vary


class DedupConfig(_Model):
    method: Literal["exact", "embedding", "off"] = "exact"
    threshold: float = Field(default=0.95, ge=0.0, le=1.0)


class CascadeStage(_Model):
    scorers: list[str] = Field(min_length=1)
    keep_top: int | None = Field(default=None, ge=1)


class ScoreConfig(_Model):
    gates: list[str] = Field(default_factory=list[str])
    cascade: list[CascadeStage] = Field(default_factory=list[CascadeStage])
    weights: dict[str, Annotated[float, Field(ge=0)]] = Field(default_factory=dict[str, float])
    aggregate: Literal["weighted_mean", "min", "geometric", "weighted_mean_with_floor"] = "weighted_mean"
    floor: float = Field(default=0.0, ge=0.0, le=1.0)
    missing: Literal["renormalize", "zero", "reject"] = "renormalize"


class PromptScorerConfig(_Model):
    kind: Literal["prompt"]
    judge: str
    criteria: str | list[str]
    output: Literal["score", "yes_no"] = "score"
    scale: tuple[float, float] = (1, 5)
    anchors: dict[str, str] | None = None
    field: str | None = None
    images_from: str | list[str] | None = None
    cost: float = 5.0
    version: str = "1"


class CommandScorerConfig(_Model):
    kind: Literal["command"]
    command: list[str] = Field(min_length=1)
    cost: float = 20.0
    timeout_s: float = 60.0
    version: str = "1"


ScorerConfig = Annotated[PromptScorerConfig | CommandScorerConfig, Field(discriminator="kind")]


class SelectConfig(_Model):
    policy: Literal["argmax", "first_above", "pairwise_tournament"] = "argmax"
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    tie_margin: float = Field(default=0.0, ge=0.0)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    escalate: Literal["pairwise", "none"] = "none"
    pairwise: str | None = None
    max_biased_pairwise: int | None = Field(default=None, ge=1)  # design change 0003
    fallback: Literal["best_rejected", "first_valid", "none"] = "none"


class BudgetConfig(_Model):
    max_cost: float | None = None
    max_seconds: float | None = None
    max_money_usd: float | None = None


class RecordConfig(_Model):
    sink: Literal["sqlite", "jsonl", "none"] = "sqlite"
    path: str | None = None
    capture_content: bool = True


class SelectionConfig(_Model):
    """The whole ``selection.toml``. Every section is optional."""

    judges: dict[str, JudgeConfig] = Field(default_factory=dict[str, JudgeConfig])
    generate: GenerateConfig = GenerateConfig()
    dedup: DedupConfig = DedupConfig()
    score: ScoreConfig = ScoreConfig()
    scorers: dict[str, ScorerConfig] = Field(default_factory=dict[str, ScorerConfig])
    select: SelectConfig = SelectConfig()
    budget: BudgetConfig = BudgetConfig()
    record: RecordConfig = RecordConfig()


def load_config(source: SelectionConfig | str | Path) -> SelectionConfig:
    """Accept a config object, a path to a ``.toml`` file, or TOML text.

    A ``str`` is read as a path only when it is a single line ending in ``.toml``.
    """
    if isinstance(source, SelectionConfig):
        return source
    if isinstance(source, Path) or ("\n" not in source and source.endswith(".toml")):
        path = Path(source)
        if not path.is_file():
            raise ConfigError(f"config file {str(path)!r} not found")
        source = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config is not valid TOML: {e}") from None
    return parse_config(data)


def parse_config(data: Mapping[str, Any]) -> SelectionConfig:
    """Validate a dict (parsed TOML) into a ``SelectionConfig``; errors become ``ConfigError``."""
    try:
        return SelectionConfig.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"invalid config: {problems}") from None


def check_config(config: SelectionConfig, names_by_kind: Mapping[str, set[str]]) -> None:
    """Check that every name the config uses exists in the registry, and policy options fit together."""
    scorers, gates, pairwise = names_by_kind["scorer"], names_by_kind["gate"], names_by_kind["pairwise"]
    for name in config.score.gates:
        _require(name, gates, "gate", "score.gates")
    used = [name for stage in config.score.cascade for name in stage.scorers]
    for name in used:
        _require(name, scorers, "scorer", "score.cascade")
    for name in config.score.weights:
        if name not in used:
            raise ConfigError(f"weight given for {name!r}, which is not in score.cascade {sorted(set(used))}")
    select = config.select
    if select.policy == "first_above" and select.threshold is None:
        raise ConfigError("select.policy = 'first_above' needs select.threshold (e.g. threshold = 0.8)")
    if select.policy == "pairwise_tournament" or select.escalate == "pairwise":
        if select.pairwise is None:
            raise ConfigError("select.pairwise must name a pairwise judge for this policy / escalate setting")
        _require(select.pairwise, pairwise, "pairwise judge", "select.pairwise")


def _require(name: str, known: set[str], what: str, where: str) -> None:
    if name not in known:
        raise ConfigError(
            f"unknown {what} {name!r} in {where}; registered: {sorted(known)}. "
            f"Pass it in registry=[...]"
            + (f" or define it under [scorers.{name}]" if what == "scorer" else "")
        )
