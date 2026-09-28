"""Resolver for ``client = "hone_models:decision"`` in ``[judges.<name>]`` (extra ``hone-select[models]``).

Registered as the ``hone.decision_clients`` entry point ``hone_models:decision`` (design/decisions.md D-002).
"""

from __future__ import annotations

import importlib
from typing import Any

from hone_select.errors import ConfigError
from hone_select.ports import DecisionClient


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
