"""Run conditions on the real machine (design change 0010 §14): one reading of /proc, nvidia-smi and the
lock state, checked for its shape. Run through scripts/gpu-lock.sh, which holds the machine-wide lock and
sets HONE_GPU_LOCK_HELD=1, so the reading sees the lock as held by the parent process."""

import os

import pytest

from hone_select.experiments import checks, conditions, gpulock
from hone_select.experiments.definition import ConditionsSpec

pytestmark = pytest.mark.gpu


def test_ac30_a_real_reading_has_the_documented_shape() -> None:
    if os.environ.get("HONE_GPU_LOCK_HELD") != "1":
        pytest.skip("run through scripts/gpu-lock.sh (HONE_GPU_LOCK_HELD=1)")
    reading = conditions.read(conditions.DEFAULT, None, window=1.0, lock=gpulock.BY_PARENT)
    assert 0.0 <= reading["cpu_busy"] <= 1.0
    assert reading["free_ram_gb"] > 0
    if "gpu" not in reading:
        pytest.skip(f"no GPU reading here: {reading.get('errors')}")
    gpu = reading["gpu"]
    assert gpu["total_gb"] > 0
    assert 0 <= gpu["free_gb"] <= gpu["total_gb"]
    assert isinstance(gpu["processes"], list)
    checks.free_for_run(reading, [])
    c = ConditionsSpec(max_cpu_load=1.0, min_free_vram_gb=0, gpu_lock=True)
    assert {v["state"] for v in checks.judge(c, reading, [], None).values()} == {"ok"}
