"""Run conditions (design change 0010): the built-in readings, the judgement of each condition, the lock
path and the guard's edge cases. Nothing here reads the real machine."""

import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from hone_select.experiments import checks, conditions, gpulock
from hone_select.experiments.definition import ConditionsSpec
from hone_select.testing import FakeMachineProbe

QUIET_GPU = {"index": 0, "total_gb": 8.0, "free_gb": 7.0, "utilization_pct": 1.0, "processes": []}


def _src(tmp_path: Path, **kw: Any) -> conditions.Sources:
    return conditions.Sources(proc=tmp_path, nvidia_smi=(str(tmp_path / "none"),), sleep=lambda s: None, **kw)


def test_readings_that_cannot_be_read_are_left_out(tmp_path: Path) -> None:
    reading = conditions.read(_src(tmp_path), None, window=0.1, lock=None)
    assert set(reading) == {"at", "errors"}  # no /proc files, no nvidia-smi: nothing is guessed
    assert reading["errors"]["gpu"].startswith("nvidia-smi: FileNotFoundError")
    (tmp_path / "stat").write_text("cpu  1 2\n")
    (tmp_path / "meminfo").write_text("MemTotal: 1 kB\n")
    assert conditions.cpu_busy(_src(tmp_path), 0.1) is None
    assert conditions.free_ram_gb(tmp_path) is None
    (tmp_path / "meminfo").write_text("MemAvailable: lots kB\n")
    assert conditions.free_ram_gb(tmp_path) is None


def _smi(tmp_path: Path, body: str) -> conditions.Sources:
    script = tmp_path / "smi"
    script.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return conditions.Sources(proc=tmp_path, nvidia_smi=(str(script),))


def test_nvidia_smi_readings(tmp_path: Path) -> None:
    ok = _smi(
        tmp_path,
        'print("0, GPU, 8192, 1024, 7168, 12") if "--query-gpu" in " ".join(sys.argv) else sys.exit(3)',
    )
    gpu, why = conditions.nvidia_gpu(ok)
    assert why is None
    assert gpu == {
        "index": 0,
        "name": "GPU",
        "total_gb": 8.0,
        "used_gb": 1.0,
        "free_gb": 7.0,
        "utilization_pct": 12.0,
        "processes": [],  # the process query failed: an empty list, the GPU reading stands
    }
    bad = _smi(tmp_path, 'print("0, GPU, [N/A], 1, 2, 3")')
    gpu, why = conditions.nvidia_gpu(bad)
    assert gpu is None
    assert why is not None
    assert why.startswith("nvidia-smi: ValueError")


def test_the_probe_gpu_and_a_raising_probe(tmp_path: Path) -> None:
    assert conditions.probe_gpu({"gpus": []}) is None
    reading = conditions.read(
        _src(tmp_path), FakeMachineProbe(raises=OSError("gone")), window=0.1, lock="free"
    )
    assert reading["errors"]["probe"] == "OSError: gone"
    assert reading["gpu_lock"] == "free"
    assert "loaded_models" not in reading


def test_each_condition_is_ok_outside_or_unknown() -> None:
    c = ConditionsSpec(max_cpu_load=0.5, min_free_ram_gb=4, min_free_vram_gb=6, max_gpu_utilization_pct=10)
    busy = {
        "cpu_busy": 0.7,
        "free_ram_gb": 2.0,
        "gpu": {**QUIET_GPU, "free_for_run_gb": 1.0, "utilization_pct": 50},
    }
    busy["gpu"]["processes"] = [{"name": "blender", "memory_gb": 5.5}]
    found = checks.judge(c, busy, [], None)
    assert {n: v["state"] for n, v in found.items()} == dict.fromkeys(found, "outside")
    assert found["min_free_vram_gb"]["reason"] == "min_free_vram_gb: 1.0 GB free, needs 6 (blender 5.5 GB)"
    unknown = checks.judge(c, {}, [], None)
    assert {v["state"] for v in unknown.values()} == {"unknown"}


def test_model_checks() -> None:
    c = ConditionsSpec(only_needed_models=True, models_on_gpu=True, probe="p")
    r: dict[str, Any] = {
        "loaded_models": [],
        "servers": [{"server": "ollama", "running": True}],
        "gpu": QUIET_GPU,
    }
    found = checks.judge(c, r, ["a"], {"need_gb": 9.0})
    assert (
        found["models_on_gpu"]["reason"]
        == "models_on_gpu: the needed models do not fit in the GPU (9.0 GB of 8.0 GB)"
    )
    assert found["only_needed_models"]["state"] == "ok"
    assert checks.judge(c, r, [], {"error": "boom"})["only_needed_models"]["state"] == "unknown"
    assert (
        checks.judge(c, r, [], {"errors": ["bad"]})["only_needed_models"]["reason"]
        == "only_needed_models: bad"
    )
    error = {"server": "ollama", "name": "x", "error": "HTTP 500"}
    assert checks.judge(c, r, [], {"errors": [error]})["only_needed_models"]["state"] == "outside"
    down = {**r, "servers": [{"server": "ollama", "running": False}]}
    assert checks.judge(c, down, [], {"errors": [error]})["only_needed_models"]["state"] == "unknown"
    assert (
        checks.judge(c, {"gpu": QUIET_GPU}, [], None)["models_on_gpu"]["reason"]
        == "models_on_gpu: no probe reading"
    )
    blocked = checks.judge(c, r, [], {"blocked_by": [{"holder": "hone-flow pid 5120"}, "x"]})
    assert blocked["only_needed_models"]["reason"] == "only_needed_models: blocked by hone-flow pid 5120, x"


def test_the_gpu_lock_check() -> None:
    c = ConditionsSpec(gpu_lock=True)
    assert checks.judge(c, {"gpu_lock": "held by this run"}, [], None)["gpu_lock"]["state"] == "ok"
    elsewhere = checks.judge(c, {"gpu_lock": "held by hone-flow 5120"}, [], None)["gpu_lock"]
    assert elsewhere["reason"] == "gpu_lock: another process holds the GPU lock (held by hone-flow 5120)"
    probe_says = {
        "gpu_lock": "held by this run",
        "probe_gpu_lock": {"held": True, "mine": False, "holder": "x 1"},
    }
    assert "holds the GPU lock: x 1" in checks.judge(c, probe_says, [], None)["gpu_lock"]["reason"]
    lease = {"gpu_lock": "held by this run", "leases": [{"name": "gpu:tts", "pid": 5120, "mine": False}]}
    assert checks.judge(c, lease, [], None)["gpu_lock"]["reason"].endswith("lease gpu:tts, pid 5120")


def test_merge_and_status() -> None:
    before = {
        "a": {"state": "ok", "value": 1, "limit": 2},
        "b": {"state": "unknown", "value": None, "reason": "b: ?"},
    }
    after = {
        "a": {"state": "outside", "value": 3, "limit": 2, "reason": "a: 3"},
        "b": {"state": "ok", "value": 1},
    }
    merged = checks.merge(before, after)
    assert merged["a"] == {"state": "outside", "before": 1, "after": 3, "limit": 2, "reason": "a: 3"}
    assert merged["b"]["state"] == "unknown"
    assert checks.status(merged, True) == "outside"
    assert checks.status({"b": merged["b"]}, True) == "unknown"
    assert checks.status({}, False) == "not_checked"


def test_lock_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONE_GPU_LOCK", str(tmp_path / "family.lock"))
    assert gpulock.lock_path(True) == tmp_path / "family.lock"
    assert gpulock.lock_path("/x/y.lock") == Path("/x/y.lock")
    assert gpulock.lock_path(False) is None
    assert gpulock.lock_path("") is None
    path = tmp_path / "l"
    assert gpulock.state_now(path) == "free"
    fd = gpulock.try_lock(path)
    assert fd is not None
    assert gpulock.state_now(path) == "held by unknown holder"
    gpulock.release(fd, path)
    monkeypatch.setenv("HONE_GPU_LOCK_HELD", "1")
    assert gpulock.state_now(path) == "held by the parent process"


def test_the_conditions_declared() -> None:
    assert ConditionsSpec().declared() == []
    assert ConditionsSpec(gpu_lock=True, max_cpu_load=0.1).declared() == ["max_cpu_load", "gpu_lock"]
