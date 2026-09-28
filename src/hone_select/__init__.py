"""hone-select: generate, score and select the best of N candidates with pluggable code or prompt scorers."""

from hone_select._tracing import current_trace
from hone_select._version import __version__
from hone_select.command import CommandScorer
from hone_select.config import SelectionConfig
from hone_select.engine import Engine
from hone_select.errors import BudgetExceeded, ConfigError, HoneSelectError, PortError
from hone_select.normalize import from_1_5, inverse, linear, sigmoid
from hone_select.ports import PORTS_VERSION
from hone_select.prompt import PromptGate, PromptPairwise, PromptScorer
from hone_select.registry import gate, generator, pairwise, scorer
from hone_select.types import Candidate, GateResult, Result, Score, Scored, Variation

__all__ = [
    "PORTS_VERSION",
    "BudgetExceeded",
    "Candidate",
    "CommandScorer",
    "ConfigError",
    "Engine",
    "GateResult",
    "HoneSelectError",
    "PortError",
    "PromptGate",
    "PromptPairwise",
    "PromptScorer",
    "Result",
    "Score",
    "Scored",
    "SelectionConfig",
    "Variation",
    "__version__",
    "current_trace",
    "from_1_5",
    "gate",
    "generator",
    "inverse",
    "linear",
    "pairwise",
    "scorer",
    "sigmoid",
]
