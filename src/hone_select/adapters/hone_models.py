"""Resolvers for hone-models (extra ``hone-select[models]``): ``client = "hone_models:decision"`` in
``[judges.<name>]`` (the ``hone.decision_clients`` entry point, design/decisions.md D-002) and
``probe = "hone_models:machine"`` in an experiment's ``[conditions]`` (the ``hone.machine_probes`` entry
point, design change 0010 §6) and ``guides = "hone_models:guides"`` in ``[generate]`` (the
``hone.model_guides`` entry point, design change 0011 §5). ``hone_models`` is imported only when called.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, cast

from hone_select.errors import ConfigError
from hone_select.ports import DecisionClient, MachineProbe, ModelGuides


def decision(model: str, **options: Any) -> DecisionClient:
    """``hone_models.decision(model, **options)``; ``hone_models`` is imported only when called."""
    try:
        hone_models = importlib.import_module("hone_models")
    except ModuleNotFoundError as e:
        raise ConfigError(
            'judge client "hone_models:decision" needs hone-models; install hone-select[models] '
            "or pass judges={...} to Engine"
        ) from e
    return hone_models.decision(model, **options)


def machine() -> MachineProbe:
    """``hone_models.machine.Machine()``: the model state for experiment run conditions."""
    try:
        module = importlib.import_module("hone_models.machine")
    except ModuleNotFoundError as e:
        raise ConfigError(
            'probe "hone_models:machine" needs hone-models with its machine probe; install '
            "hone-select[models] (or update hone-models), or remove `probe` from [conditions]"
        ) from e
    return module.Machine()


def guides() -> ModelGuides:
    """``hone_models.guide(model_id)`` as a ``ModelGuides`` source: what each model can take."""
    try:
        hone_models = importlib.import_module("hone_models")
    except ModuleNotFoundError as e:
        raise ConfigError(
            'guides "hone_models:guides" needs hone-models; install hone-select[models], or remove `guides` '
            "from [generate] (then every need of a case is need_unknown)"
        ) from e
    if not hasattr(hone_models, "guide"):
        raise ConfigError(
            'guides "hone_models:guides" needs a hone-models with model guides (its change 0015); update '
            "hone-models, or remove `guides` from [generate]"
        )
    return _Guides(hone_models)


class _Guides:
    def __init__(self, hone_models: Any) -> None:
        self.mk = hone_models
        errors = getattr(hone_models, "errors", None)
        self.unknown: type[Exception] = getattr(errors, "ConfigError", LookupError)

    def guide(self, model_id: str) -> Mapping[str, Any] | None:
        try:
            found = self.mk.guide(model_id)
        except self.unknown:  # an id the registry does not know
            return None
        return None if found is None else _as_json(found, model_id)


def _as_json(guide: Any, model_id: str) -> dict[str, Any]:
    """hone-models' ``ModelGuide.as_dict()`` (or a plain mapping), with its text; ``install`` becomes the
    one command a person runs (``hone-models models install <id>``) and the steps it prints are kept as
    ``install_commands``."""
    out = dict(cast(Mapping[str, Any], guide)) if isinstance(guide, Mapping) else dict(guide.as_dict())
    as_text = getattr(guide, "as_text", None)
    if callable(as_text):
        out.setdefault("text", str(as_text()))
    steps = out.get("install")
    if isinstance(steps, list):
        out["install_commands"] = steps
    if not isinstance(steps, str):
        out["install"] = f"hone-models models install {model_id}"
    return out
