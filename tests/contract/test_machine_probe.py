"""The MachineProbe port (design change 0010 §6): the fake passes the contract checker, the checker rejects
broken shapes, the fake's prepare / load behave as documented, and the hone_models resolver."""

import sys
import types
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.adapters import hone_models as resolver
from hone_select.ports import MachineProbe
from hone_select.testing import FakeMachineProbe, check_machine_probe

LEFTOVER = {
    "server": "ollama",
    "name": "llama3.1:8b",
    "model_id": "llama3.1-8b",
    "size_gb": 5.0,
    "vram_gb": 5.0,
}
NEEDED = {"server": "ollama", "name": "gemma4:12b", "model_id": "gemma4-12b", "size_gb": 7.1, "vram_gb": 7.1}


def test_the_fake_passes_the_contract() -> None:
    fake = FakeMachineProbe(loaded=[LEFTOVER])
    check_machine_probe(fake)
    probe: MachineProbe = fake
    assert probe.snapshot()["gpus"][0]["memory_total_gb"] == 8.0


class Broken(FakeMachineProbe):
    def __init__(self, **override: Any) -> None:
        super().__init__()
        self.override = override

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), **self.override}


@pytest.mark.parametrize(
    "override",
    [
        {"gpus": "one"},
        {"servers": [{"server": "ollama", "running": "yes"}]},
        {"loaded_models": [{"name": 3}]},
        {"leases": ["gpu:tts"]},
        {"gpu_lock": "held"},
    ],
)
def test_the_checker_rejects_broken_snapshots(override: dict[str, Any]) -> None:
    with pytest.raises(AssertionError):
        check_machine_probe(Broken(**override))


def test_the_checker_rejects_a_broken_prepare() -> None:
    class BadPrepare(FakeMachineProbe):
        def prepare(self, needed, *, if_busy="block"):  # type: ignore[override]
            return {"unloaded": "everything"}

    with pytest.raises(AssertionError):
        check_machine_probe(BadPrepare())
    with pytest.raises(AssertionError):
        check_machine_probe(types.SimpleNamespace(snapshot=lambda: [], prepare=lambda needed: {}))


def test_prepare_unloads_what_is_not_needed_and_reports_missing() -> None:
    fake = FakeMachineProbe(loaded=[LEFTOVER, NEEDED])
    out = fake.prepare(["gemma4-12b", "qwen3-8b"], if_busy="block")
    assert out["unloaded"] == [{"server": "ollama", "name": "llama3.1:8b"}]
    assert out["missing"] == ["qwen3-8b"]
    assert out["need_gb"] == 7.1
    assert [m["name"] for m in fake.snapshot()["loaded_models"]] == ["gemma4:12b"]
    assert fake.calls[0] == {"call": "prepare", "needed": ["gemma4-12b", "qwen3-8b"], "if_busy": "block"}


def test_prepare_blocked_and_errors() -> None:
    fake = FakeMachineProbe(loaded=[LEFTOVER], blocked_by=[{"lease": "gpu:tts", "pid": 5120}])
    assert fake.prepare([])["unloaded"] == []
    assert fake.prepare([], if_busy="unload")["unloaded"] == [{"server": "ollama", "name": "llama3.1:8b"}]
    stuck = FakeMachineProbe(loaded=[LEFTOVER], errors={"llama3.1:8b": "server error"})
    assert stuck.prepare([])["errors"][0]["error"] == "server error"


def test_load_is_optional_and_scripted() -> None:
    assert not hasattr(FakeMachineProbe(), "load")
    fake = FakeMachineProbe(loads={"comfy": {"loaded": None, "error": "not supported"}})
    assert fake.load("gemma4-12b")["loaded"] is True
    assert fake.load("comfy")["loaded"] is None
    assert [m["model_id"] for m in fake.loaded] == ["gemma4-12b"]


def test_a_raising_fake_raises() -> None:
    fake = FakeMachineProbe(raises=RuntimeError("probe down"))
    with pytest.raises(RuntimeError):
        fake.snapshot()
    with pytest.raises(RuntimeError):
        fake.prepare([])


def test_scripted_snapshots_repeat_the_last() -> None:
    fake = FakeMachineProbe([{"gpus": None}, {"servers": []}])
    assert fake.snapshot()["gpus"] is None
    assert fake.snapshot()["servers"] == []
    assert fake.snapshot()["servers"] == []


def test_the_resolver_names_the_extra_when_hone_models_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_models.machine", None)  # makes the import fail
    with pytest.raises(ConfigError, match=r"hone-select\[models\]"):
        resolver.machine()


def test_the_resolver_builds_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("hone_models.machine")
    module.Machine = FakeMachineProbe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hone_models.machine", module)
    assert isinstance(resolver.machine(), FakeMachineProbe)
