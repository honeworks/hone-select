"""AC-30: `[conditions]` is validated; definition mistakes are ConfigErrors that say what to change; plan.json
shows the declared conditions, the needed models per setup and a reading now with what the run would do;
plan unloads nothing; editing a condition makes the experiment a draft; a pilot on a busy machine is
refused."""

import json
from pathlib import Path

import pytest

from hone_select import ConfigError, HoneSelectError
from hone_select.experiments import definition as d
from hone_select.testing import FakeMachineProbe

from .condition_helpers import BUSY, CONDITIONS_EXPERIMENT, PROMPT_EXPERIMENT, FakeMachine, ready
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

LEFTOVER = {
    "server": "ollama",
    "name": "llama3.1:8b",
    "model_id": "llama3.1-8b",
    "size_gb": 5.0,
    "vram_gb": 5.0,
}


@pytest.mark.parametrize(
    ("conditions", "message"),
    [
        ("max_cpu_laod = 0.5", "max_cpu_laod"),
        ("max_cpu_load = 2", "max_cpu_load"),
        ('on_violation = "retry"', "on_violation"),
        ("wait_timeout = 0", "wait_timeout"),
        ('if_busy = "maybe"', "if_busy"),
        ("only_needed_models = true", "only_needed_models needs a probe"),
        ("models_on_gpu = true", "models_on_gpu needs a probe"),
        ('models = ["{setup.nothing}"]', "names no factor or case field"),
        ('models = ["{case.missing}"]', "names no factor or case field"),
    ],
)
def test_ac30_definition_mistakes(tmp_path: Path, conditions: str, message: str) -> None:
    p, _ = project(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=conditions))
    with pytest.raises(ConfigError, match=message):
        p.plan("E0001")


def test_ac30_vram_of_a_prompt_subject_needs_a_probe(tmp_path: Path) -> None:
    p, _ = project(tmp_path, PROMPT_EXPERIMENT.format(conditions="min_free_vram_gb = 7"))
    with pytest.raises(ConfigError, match="min_free_vram_gb with a prompt subject needs a probe"):
        p.plan("E0001")


def test_ac30_gpu_lock_and_a_gpu_lock_wrap_are_refused(tmp_path: Path) -> None:
    exp = CONDITIONS_EXPERIMENT.format(conditions="gpu_lock = true").replace(
        'function = "subjects:write"', 'function = "subjects:write"\nwrap = ["scripts/gpu-lock.sh"]'
    )
    p, _ = project(tmp_path, exp)
    with pytest.raises(ConfigError, match=r"remove gpu-lock.sh from \[generate\] wrap"):
        p.plan("E0001")


def test_ac30_the_plan_shows_the_conditions_and_unloads_nothing(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path)
    probe = FakeMachineProbe([{"gpus": None}], loaded=[LEFTOVER])
    conditions = (
        "max_cpu_load = 0.5\nmin_free_ram_gb = 64\nmin_free_vram_gb = 7\nonly_needed_models = true\n"
        'probe = "fake"\ngpu_lock = true\nmodels = ["{setup.model}-gguf"]'
    )
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=conditions), machine.sources(probe))
    plan = json.loads((folder / "plan.json").read_text())["conditions"]
    assert plan["declared"]["max_cpu_load"] == 0.5
    assert plan["declared"]["on_violation"] == "wait"
    assert plan["declared"]["gpu_lock"].endswith("gpu.lock")  # $HONE_GPU_LOCK, a temp file in tests
    assert plan["needed_models"] == {
        d.setup_id({"model": "small"}): ["small-gguf"],
        d.setup_id({"model": "large"}): ["large-gguf"],
    }
    checks = plan["now"]["checks"]
    assert checks["max_cpu_load"]["state"] == "ok"
    assert checks["min_free_ram_gb"] == {
        "state": "outside",
        "value": 20.0,
        "limit": 64.0,
        "reason": "min_free_ram_gb: 20.0 GB available, needs 64",
    }
    assert checks["min_free_vram_gb"]["state"] == "ok"  # gpus: None -> hone-select's own nvidia-smi
    assert checks["only_needed_models"]["state"] == "outside"
    assert checks["only_needed_models"]["value"] == ["llama3.1:8b"]
    assert checks["gpu_lock"]["state"] == "ok"  # free: the run will hold it
    assert plan["now"]["would"].startswith("wait: min_free_ram_gb")
    assert plan["now"]["would"].endswith("(prepare would unload llama3.1:8b)")
    assert all(c["call"] != "prepare" for c in probe.calls)  # plan unloads nothing
    assert probe.loaded == [LEFTOVER]
    assert p.status("E0001")["status"] == "approved"
    toml = folder / "experiment.toml"
    toml.write_text(toml.read_text().replace("max_cpu_load = 0.5", "max_cpu_load = 0.6"))
    assert p.status("E0001")["status"] == "draft"  # the conditions are part of the definition hash


def test_ac30_unknown_readings_show_in_the_plan(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path)
    exp = CONDITIONS_EXPERIMENT.format(conditions="max_gpu_utilization_pct = 20")
    _, folder = ready(tmp_path, exp, machine.sources(nvidia=False))
    now = json.loads((folder / "plan.json").read_text())["conditions"]["now"]
    assert now["checks"]["max_gpu_utilization_pct"]["state"] == "unknown"
    assert now["would"].startswith("refuse to start (cannot measure)")


def test_ac30_no_conditions_no_section(tmp_path: Path) -> None:
    p, _ = project(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=""))
    assert "conditions" not in p.plan("E0001")


def test_ac30_a_pilot_on_a_busy_machine_is_refused(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY])
    p, _ = project(tmp_path, CONDITIONS_EXPERIMENT.format(conditions="max_cpu_load = 0.5"))
    with pytest.raises(HoneSelectError, match="the pilot is refused.*max_cpu_load"):
        p.plan("E0001", pilot=True, sources=machine.sources())
    quiet = FakeMachine(tmp_path / "quiet")
    plan = p.plan("E0001", pilot=True, sources=quiet.sources())
    assert plan["pilot"]["error"] is None
