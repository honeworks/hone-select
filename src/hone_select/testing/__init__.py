"""Public fakes for every port and contract checkers, for your tests and ours."""

from hone_select._records import MemorySink
from hone_select.testing import contracts
from hone_select.testing.contracts import check_machine_probe
from hone_select.testing.fakes import FakeDecisionClient, FakeEmbedder, FakeMachineProbe, FakeTextClient

__all__ = [
    "FakeDecisionClient",
    "FakeEmbedder",
    "FakeMachineProbe",
    "FakeTextClient",
    "MemorySink",
    "check_machine_probe",
    "contracts",
]
