"""Public fakes for every port and contract checkers, for your tests and ours."""

from hone_select._records import MemorySink
from hone_select.testing import contracts
from hone_select.testing.contracts import check_machine_probe, check_model_guides
from hone_select.testing.fakes import (
    FakeDecisionClient,
    FakeEmbedder,
    FakeMachineProbe,
    FakeModelGuides,
    FakeTextClient,
)

__all__ = [
    "FakeDecisionClient",
    "FakeEmbedder",
    "FakeMachineProbe",
    "FakeModelGuides",
    "FakeTextClient",
    "MemorySink",
    "check_machine_probe",
    "check_model_guides",
    "contracts",
]
