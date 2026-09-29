"""The name -> Component registry: decorators for plain functions, scorer objects, `[scorers.*]` config
sections, and `[judges.*]` clients loaded through the ``hone.decision_clients`` entry point."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Literal, get_args

from hone_select._tracing import current_trace
from hone_select.command import CommandScorer
from hone_select.config import JudgeConfig, PromptScorerConfig, SelectionConfig
from hone_select.errors import ConfigError
from hone_select.ports import DecisionClient
from hone_select.prompt import PromptScorer, judge_model

Kind = Literal["generator", "gate", "scorer", "pairwise"]
KINDS: tuple[Kind, ...] = get_args(Kind)


@dataclass(frozen=True, slots=True)
class Component:
    """A named callable plus what the engine needs to know about it. Calling it calls ``fn``, with
    ``trace=current_trace()`` when ``fn`` takes a ``trace`` keyword (design/current.md §7.1, §7.7), so
    the spans of another package's scorer join the selection's trace."""

    kind: Kind
    name: str
    fn: Callable[..., Any]
    cost: float = 1.0
    version: str = "1"
    judge_model: str = ""  # model id of an LLM judge; part of the cache key and the self-judging check

    def __call__(self, *args: Any) -> Any:
        if takes_trace(self.fn):
            return self.fn(*args, trace=current_trace())
        return self.fn(*args)


def takes_trace(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` accepts a ``trace=`` keyword argument."""
    try:
        parameter = inspect.signature(fn).parameters.get("trace")
    except (TypeError, ValueError):  # some builtins have no signature
        return False
    keyword = (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return parameter is not None and parameter.kind in keyword


def _decorator(kind: Kind, name: str | None, cost: float, version: str) -> Callable[..., Component]:
    def wrap(fn: Callable[..., Any]) -> Component:
        return Component(kind, name or fn.__name__, fn, float(cost), str(version))

    return wrap


def generator(name: str | None = None, *, cost: float = 0.0) -> Callable[..., Component]:
    """Mark ``fn(task, variation) -> Candidate | Any`` as the generator (non-Candidates are wrapped)."""
    return _decorator("generator", name, cost, "1")


def gate(name: str | None = None, *, cost: float = 0.0, version: str = "1") -> Callable[..., Component]:
    """Mark ``fn(candidate) -> bool | GateResult`` as a gate; a failed gate rejects the candidate."""
    return _decorator("gate", name, cost, version)


def scorer(name: str | None = None, *, cost: float = 1.0, version: str = "1") -> Callable[..., Component]:
    """Mark ``fn(candidate) -> float | Score | None`` as a scorer (value 0..1, higher is better)."""
    return _decorator("scorer", name, cost, version)


def pairwise(name: str | None = None, *, cost: float = 10.0, version: str = "1") -> Callable[..., Component]:
    """Mark ``fn(a, b) -> "a" | "b" | "tie" | (choice, confidence)`` as a pairwise judge."""
    return _decorator("pairwise", name, cost, version)


def as_component(obj: Any) -> Component:
    """Accept a decorated function, a built-in scorer object, or any callable (read as a scorer).

    Plain callables may expose ``kind``, ``name``, ``cost``, ``version`` and ``judge_model`` attributes
    (design/current.md §7.7); missing ones get the scorer defaults.
    """
    if isinstance(obj, Component):
        return obj
    if not callable(obj):
        raise ConfigError(f"registry item {obj!r} is not callable; pass decorated functions or scorers")
    kind = getattr(obj, "kind", "scorer")
    if kind not in KINDS:
        raise ConfigError(f"registry item {obj!r} has unknown kind {kind!r}; use one of {list(KINDS)}")
    name = getattr(obj, "name", None) or getattr(obj, "__name__", None)
    if not name:
        raise ConfigError(f"registry item {obj!r} has no name; give it a `name` attribute")
    return Component(
        kind=kind,
        name=str(name),
        fn=obj,
        cost=float(getattr(obj, "cost", 1.0)),
        version=str(getattr(obj, "version", "1")),
        judge_model=str(getattr(obj, "judge_model", "") or judge_model(getattr(obj, "judge", None))),
    )


def module_items(module_name: str) -> list[Any]:
    """Every decorated function or scorer object defined at the top level of ``module_name`` (imported from
    ``sys.path``): what ``--registry`` and an experiment's ``registry = [...]`` load."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        raise ConfigError(f"cannot import registry module {module_name!r}: {e}; run from its folder") from e
    return [
        value
        for value in vars(module).values()
        if isinstance(value, Component)
        or (not isinstance(value, type) and callable(value) and getattr(value, "kind", None) in KINDS)
    ]


def load_registry(items: Iterable[Any]) -> dict[str, Component]:
    """Name -> component. Names must be unique across all kinds."""
    registry: dict[str, Component] = {}
    for item in items:
        component = as_component(item)
        if component.name in registry:
            raise ConfigError(f"two registry items are named {component.name!r}; names must be unique")
        registry[component.name] = component
    return registry


def config_scorers(config: SelectionConfig) -> list[PromptScorer | CommandScorer]:
    """The scorers defined in ``[scorers.<name>]`` sections (no code needed)."""
    scorers: list[PromptScorer | CommandScorer] = []
    for name, c in config.scorers.items():
        if isinstance(c, PromptScorerConfig):
            scorers.append(
                PromptScorer(
                    name,
                    c.criteria,
                    c.judge,
                    output=c.output,
                    scale=c.scale,
                    anchors=c.anchors,
                    field=c.field,
                    images_from=c.images_from,
                    cost=c.cost,
                    version=c.version,
                )
            )
        else:
            scorers.append(
                CommandScorer(name, c.command, cost=c.cost, timeout_s=c.timeout_s, version=c.version)
            )
    return scorers


def load_judge(name: str, config: JudgeConfig) -> DecisionClient:
    """Build ``[judges.<name>]`` through the ``hone.decision_clients`` entry point named by ``client``."""
    found = entry_points(group="hone.decision_clients", name=config.client)
    if not found:
        available = sorted(ep.name for ep in entry_points(group="hone.decision_clients"))
        raise ConfigError(
            f"judges.{name}: no decision client {config.client!r} is installed; available: "
            f"{available}. Install its package (e.g. hone-select[models]) or pass judges={{...}}"
        )
    factory = next(iter(found)).load()
    return factory(**(config.model_extra or {}))


def bind_judges(
    items: Iterable[Any], judges: dict[str, DecisionClient], config: SelectionConfig
) -> list[Any]:
    """Replace judge names (``judge="local"``) with clients from ``judges`` or ``[judges.*]``.

    Clients built from config are added to ``judges`` so every scorer shares one client per name.
    """
    bound: list[Any] = []
    for item in items:
        judge = getattr(item, "judge", None)
        # built-in prompt scorers are dataclass instances, so ``dataclasses.replace`` can swap the judge
        if isinstance(judge, str) and dataclasses.is_dataclass(item) and not isinstance(item, type):
            if judge not in judges:
                if judge not in config.judges:
                    raise ConfigError(
                        f"scorer {getattr(item, 'name', item)!r} uses judge {judge!r}, which is "
                        f"neither in judges={{...}} nor a [judges.{judge}] config section"
                    )
                judges[judge] = load_judge(judge, config.judges[judge])
            bound.append(dataclasses.replace(item, judge=judges[judge]))
        else:
            bound.append(item)
    return bound
