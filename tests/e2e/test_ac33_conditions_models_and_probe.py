"""AC-33: the needed models per subject reach the probe's `prepare` (a group change unloads the previous
model); what `prepare` answered is in the environment; `blocked_by` waits, `if_busy = "unload"` unloads
anyway; unload errors are outside (unknown for a server that did not answer); a missing model makes the
sample cold unless `warm_up` loads it; `min_free_vram_gb` does not count the needed models; `models_on_gpu`
marks a partly offloaded model; `gpus: None` falls back to nvidia-smi, then unknown; a server that cannot
tell and a raising probe are unknown; an unmeasurable declared check refuses `start`; the hone_models probe
without hone-models is a ConfigError naming the extra."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hone_select import ConfigError, HoneSelectError
from hone_select.experiments import start
from hone_select.testing import FakeMachineProbe, check_machine_probe

from .condition_helpers import (
    CONDITIONS_EXPERIMENT,
    PROMPT_EXPERIMENT,
    FakeMachine,
    ready,
    results_of,
    run_json,
)
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

LEFTOVER = {
    "server": "ollama",
    "name": "llama3.1:8b",
    "model_id": "llama3.1-8b",
    "size_gb": 5.0,
    "vram_gb": 5.0,
}
PROBE = 'probe = "fake"\non_violation = "record_only"'


def _model(mid: str, size: float = 4.0, vram: float = 4.0) -> dict[str, Any]:
    return {"server": "ollama", "name": f"{mid}:q4", "model_id": mid, "size_gb": size, "vram_gb": vram}


def _run(tmp_path: Path, conditions: str, probe: Any, *, prompt: bool = True, nvidia: bool = True) -> Path:
    machine = FakeMachine(tmp_path)
    exp = (PROMPT_EXPERIMENT if prompt else CONDITIONS_EXPERIMENT).format(conditions=conditions)
    p, folder = ready(tmp_path, exp, machine.sources(probe, nvidia=nvidia))
    start(p, "E0001", sources=machine.sources(probe, nvidia=nvidia))
    return folder


def _by_model(folder: Path) -> dict[str, dict[str, Any]]:
    return {r["params"]["model"]: r["environment"] for r in results_of(folder)}


def _prepares(probe: FakeMachineProbe) -> list[list[str]]:
    return [c["needed"] for c in probe.calls if c["call"] == "prepare"]


def test_ac33_prepare_gets_the_prompt_model_and_unloads_the_rest(tmp_path: Path) -> None:
    probe = FakeMachineProbe(loaded=[LEFTOVER])
    folder = _run(tmp_path, f"only_needed_models = true\n{PROBE}", probe)
    # before tiny, after tiny = before big (the group change unloads tiny; nothing loads it in the fake)
    assert _prepares(probe) == [["tiny"], ["big"]]
    env = _by_model(folder)
    assert env["tiny"]["prepared"]["unloaded"] == [{"server": "ollama", "name": "llama3.1:8b"}]
    assert env["tiny"]["prepared"]["missing"] == ["tiny"]
    assert env["tiny"]["cold"] is True  # not loaded before the sample, and no warm-up
    assert env["tiny"]["status"] == "ok"
    assert all(c.get("if_busy", "block") == "block" for c in probe.calls)


def test_ac33_python_subjects_need_the_declared_models_or_none(tmp_path: Path) -> None:
    probe = FakeMachineProbe()
    _run(
        tmp_path,
        f'only_needed_models = true\nmodels = ["{{setup.model}}-gguf"]\n{PROBE}',
        probe,
        prompt=False,
    )
    assert _prepares(probe) == [["small-gguf"], ["large-gguf"]]
    (tmp_path / "none").mkdir()
    bare = FakeMachineProbe(loaded=[LEFTOVER])
    _run(tmp_path / "none", f"only_needed_models = true\n{PROBE}", bare, prompt=False)
    assert _prepares(bare) == [[], []]  # a render with no model loaded at all
    assert bare.loaded == []


def test_ac33_blocked_by_waits_and_if_busy_unload_unloads(tmp_path: Path) -> None:
    lease = {"name": "gpu:tts", "pid": 5120}
    probe = FakeMachineProbe(loaded=[LEFTOVER], blocked_by=[lease])
    machine = FakeMachine(tmp_path)
    machine.on_poll.append(lambda m: setattr(probe, "blocked_by", None))
    exp = PROMPT_EXPERIMENT.format(conditions='only_needed_models = true\nprobe = "fake"')
    p, folder = ready(tmp_path, exp, machine.sources(probe))
    assert start(p, "E0001", sources=machine.sources(probe))["status"] == "completed"
    wait = run_json(folder)["waits"][0]
    assert wait["reasons"] == ["only_needed_models: blocked by lease gpu:tts, pid 5120"]
    assert wait["outcome"] == "conditions met"
    (tmp_path / "unload").mkdir()
    anyway = FakeMachineProbe(loaded=[LEFTOVER], blocked_by=[lease])
    folder = _run(
        tmp_path / "unload", 'only_needed_models = true\nif_busy = "unload"\nprobe = "fake"', anyway
    )
    assert all(c["if_busy"] == "unload" for c in anyway.calls if c["call"] == "prepare")
    env = _by_model(folder)["tiny"]
    assert env["prepared"]["blocked_by"] == [lease]
    assert env["prepared"]["unloaded"] == [{"server": "ollama", "name": "llama3.1:8b"}]
    assert env["status"] == "ok"


def test_ac33_unload_errors_are_outside_or_unknown(tmp_path: Path) -> None:
    probe = FakeMachineProbe(loaded=[LEFTOVER], errors={"llama3.1:8b": "HTTP 500"})
    env = _by_model(_run(tmp_path, f"only_needed_models = true\n{PROBE}", probe))["tiny"]
    assert env["checks"]["only_needed_models"]["state"] == "outside"
    assert "llama3.1:8b could not be unloaded: HTTP 500" in env["checks"]["only_needed_models"]["reason"]
    (tmp_path / "down").mkdir()
    down = FakeMachineProbe()
    machine = FakeMachine(tmp_path / "down")
    exp = PROMPT_EXPERIMENT.format(conditions=f"only_needed_models = true\n{PROBE}")
    p, folder = ready(tmp_path / "down", exp, machine.sources(down))

    def server_goes_down(_: FakeMachine) -> None:  # during the check before tiny, after its prepare
        down.loaded.append(LEFTOVER)
        down.errors = {"llama3.1:8b": "connection refused"}
        down.snapshots = [{"servers": [{"server": "ollama", "running": False, "error": "refused"}]}]

    machine.on_reading[1] = server_goes_down
    start(p, "E0001", sources=machine.sources(down))
    env = _by_model(folder)["big"]
    assert env["prepared"]["errors"][0]["error"] == "connection refused"
    assert env["status"] != "ok"  # the unknown reading before it; unit tests pin unknown vs outside


def test_ac33_warm_up_loads_a_missing_model(tmp_path: Path) -> None:
    probe = FakeMachineProbe(loads={})
    env = _by_model(_run(tmp_path, f"only_needed_models = true\nwarm_up = true\n{PROBE}", probe))
    assert env["tiny"]["cold"] is False
    assert env["tiny"]["warm_up"][0]["loaded"] is True
    assert {"call": "load", "model_id": "tiny"} in probe.calls
    (tmp_path / "comfy").mkdir()
    unsupported = FakeMachineProbe(loads={"tiny": {"loaded": None, "error": "not supported"}, "big": {}})
    env = _by_model(
        _run(tmp_path / "comfy", f"only_needed_models = true\nwarm_up = true\n{PROBE}", unsupported)
    )
    assert env["tiny"]["cold"] is True
    assert env["big"]["cold"] is False


def test_ac33_a_cold_sample_is_counted_but_not_its_speed(tmp_path: Path) -> None:
    probe = FakeMachineProbe()
    folder = _run(tmp_path, f"only_needed_models = true\n{PROBE}", probe)
    res = json.loads((folder / "results" / "results.json").read_text())
    for s in res["setups"].values():
        assert s["samples"] == 1  # counted
        assert s["measurements"].get("seconds") is None  # every sample loaded its model: no speed
    assert res["conditions"]["cold"] == 2


def test_ac33_free_vram_does_not_count_the_needed_models(tmp_path: Path) -> None:
    gpu = {"index": 0, "memory_total_gb": 8.0, "memory_free_gb": 1.0, "utilization_pct": 3, "processes": []}
    probe = FakeMachineProbe([{"gpus": [gpu]}], loaded=[_model("tiny", 7.0, 6.6), _model("big", 7.0, 6.6)])
    env = _by_model(_run(tmp_path, f"min_free_vram_gb = 7\n{PROBE}", probe))
    assert env["tiny"]["before"]["gpu"]["free_for_run_gb"] == 7.6  # 1.0 free + tiny's 6.6
    assert env["tiny"]["checks"]["min_free_vram_gb"]["state"] == "ok"


def test_ac33_models_on_gpu_marks_a_partly_offloaded_model(tmp_path: Path) -> None:
    probe = FakeMachineProbe(loaded=[_model("tiny", 7.1, 5.0), _model("big")])
    env = _by_model(_run(tmp_path, f"models_on_gpu = true\n{PROBE}", probe))
    assert env["tiny"]["checks"]["models_on_gpu"]["state"] == "outside"
    assert "tiny (70% on the GPU)" in env["tiny"]["checks"]["models_on_gpu"]["reason"]
    assert env["big"]["checks"]["models_on_gpu"] == {
        "state": "ok",
        "before": {"big": 1.0},
        "after": {"big": 1.0},
        "limit": None,
    }
    (tmp_path / "warm").mkdir()
    warm = FakeMachineProbe(loads={"tiny": {"size_gb": 7.1, "vram_gb": 5.0}, "big": {}})
    env = _by_model(_run(tmp_path / "warm", f"models_on_gpu = true\nwarm_up = true\n{PROBE}", warm))
    assert env["tiny"]["checks"]["models_on_gpu"]["state"] == "outside"  # seen at warm-up, before the sample


def test_ac33_no_gpu_from_the_probe_falls_back_to_nvidia_smi_then_unknown(tmp_path: Path) -> None:
    probe = FakeMachineProbe([{"gpus": None}])
    env = _by_model(_run(tmp_path, f"min_free_vram_gb = 4\n{PROBE}", probe))
    assert env["tiny"]["before"]["gpu"]["free_gb"] == 7.5  # the fake nvidia-smi
    assert env["tiny"]["checks"]["min_free_vram_gb"]["state"] == "ok"
    (tmp_path / "none").mkdir()
    machine = FakeMachine(tmp_path / "none")
    exp = PROMPT_EXPERIMENT.format(conditions=f"min_free_vram_gb = 4\n{PROBE}")
    p, _ = ready(tmp_path / "none", exp, machine.sources(FakeMachineProbe([{"gpus": None}]), nvidia=False))
    with pytest.raises(HoneSelectError, match="min_free_vram_gb: GPU memory cannot be read"):
        start(p, "E0001", sources=machine.sources(FakeMachineProbe([{"gpus": None}]), nvidia=False))


def test_ac33_a_server_that_cannot_tell_is_unknown(tmp_path: Path) -> None:
    servers = [{"server": "ollama", "running": True, "error": None}]
    probe = FakeMachineProbe([{"servers": servers}])
    machine = FakeMachine(tmp_path)
    exp = PROMPT_EXPERIMENT.format(conditions=f"only_needed_models = true\nmodels_on_gpu = true\n{PROBE}")
    p, folder = ready(tmp_path, exp, machine.sources(probe))
    timeout = [{"server": "comfyui", "running": None, "error": "timeout"}]
    machine.on_reading[2] = lambda m: probe.snapshots.append(
        {"servers": timeout}
    )  # from the check after tiny
    start(p, "E0001", sources=machine.sources(probe))
    env = _by_model(folder)
    assert env["tiny"]["checks"]["only_needed_models"]["state"] == "unknown"
    assert "comfyui cannot tell what it holds (timeout)" in env["tiny"]["checks"]["models_on_gpu"]["reason"]


def test_ac33_a_raising_probe_is_unknown_and_the_run_waits(tmp_path: Path) -> None:
    probe = FakeMachineProbe()
    machine = FakeMachine(tmp_path)
    exp = PROMPT_EXPERIMENT.format(conditions='models_on_gpu = true\nprobe = "fake"')
    p, folder = ready(tmp_path, exp, machine.sources(probe))
    machine.on_reading[2] = lambda m: setattr(probe, "raises", RuntimeError("probe down"))
    machine.on_poll.append(lambda m: setattr(probe, "raises", None))
    assert start(p, "E0001", sources=machine.sources(probe))["status"] == "completed"
    wait = run_json(folder)["waits"][0]
    assert wait["reasons"] == ["models_on_gpu: the probe failed: RuntimeError: probe down"]
    assert len(list(folder.glob("outputs/*/*/*/outside-1.json"))) == 1  # unknown after it: run again


def test_ac33_an_unmeasurable_check_refuses_start(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path)
    exp = CONDITIONS_EXPERIMENT.format(conditions="max_gpu_utilization_pct = 20")
    p, folder = ready(tmp_path, exp, machine.sources(nvidia=False))
    with pytest.raises(HoneSelectError, match="cannot be measured.*install the NVIDIA driver tools"):
        start(p, "E0001", sources=machine.sources(nvidia=False))
    assert run_json(folder)["stopped_because"] == [
        "max_gpu_utilization_pct: GPU utilization cannot be read (no nvidia-smi and no probe)"
    ]
    assert results_of(folder) == []


def test_ac33_a_raising_prepare_at_the_start_refuses(tmp_path: Path) -> None:
    probe = FakeMachineProbe()
    machine = FakeMachine(tmp_path)
    exp = PROMPT_EXPERIMENT.format(conditions='only_needed_models = true\nprobe = "fake"')
    p, _ = ready(tmp_path, exp, machine.sources(probe))
    probe.raises = KeyError("unknown registry id 'tiny'")
    with pytest.raises(HoneSelectError, match="prepare failed"):
        start(p, "E0001", sources=machine.sources(probe))


def test_ac33_the_fake_probe_passes_the_contract() -> None:
    check_machine_probe(FakeMachineProbe(loaded=[LEFTOVER]))


def test_ac33_the_hone_models_probe_needs_the_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_models.machine", None)  # hone-models without its probe
    p, _ = project(tmp_path, CONDITIONS_EXPERIMENT.format(conditions='probe = "hone_models:machine"'))
    with pytest.raises(ConfigError, match=r"install hone-select\[models\]"):
        p.plan("E0001")
    (tmp_path / "other").mkdir()
    p, _ = project(tmp_path / "other", CONDITIONS_EXPERIMENT.format(conditions='probe = "nope"'))
    with pytest.raises(ConfigError, match="probe 'nope' is not installed; available: .*hone_models:machine"):
        p.plan("E0001")
