"""Resolvers for hone-models (extra ``hone-select[models]``): ``client = "hone_models:decision"`` in
``[judges.<name>]`` (the ``hone.decision_clients`` entry point, design/decisions.md D-002) and
``probe = "hone_models:machine"`` in an experiment's ``[conditions]`` (the ``hone.machine_probes`` entry
point, design change 0010 §6). ``hone_models`` is imported only when called.
"""

from __future__ import annotations

import importlib
from typing import Any

from hone_select.errors import ConfigError
from hone_select.ports import DecisionClient, MachineProbe


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
