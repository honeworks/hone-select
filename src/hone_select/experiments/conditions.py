"""The machine's state for run conditions (design change 0010 §2, §14): the built-in readings (CPU busy share
from `/proc/stat` over a window, `MemAvailable`, GPU 0 from `nvidia-smi`) and the probe's snapshot.

Every source is injectable through `Sources` (the `/proc` root, the `nvidia-smi` command, the clock, the
sleep, the poll interval and the probe), so tests never read the real machine. A value that cannot be read
is left out of the reading (never 0); why is in `reading["errors"]`.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

GIB = 1024 * 1024  # kB (meminfo) and MiB (nvidia-smi) per GB
NVIDIA_TIMEOUT = 10.0
GPU_QUERY = (
    "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
    "--format=csv,noheader,nounits",
)
APPS_QUERY = ("--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits")


@dataclass(frozen=True)
class Sources:
    """Where the readings come from. `probe`, when set, is used instead of resolving `[conditions] probe`."""

    proc: Path = Path("/proc")
    nvidia_smi: tuple[str, ...] = ("nvidia-smi",)
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    poll: float = 10.0  # seconds between two checks while waiting
    window: float = 1.0  # seconds of the CPU window when `max_cpu_load` is declared
    probe: Any = None

    def stamp(self) -> str:
        return datetime.fromtimestamp(self.clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


DEFAULT = Sources()
SHORT_WINDOW = 0.1  # the CPU window when no condition needs it: every sample still gets a cheap reading


def _cpu_times(proc: Path) -> tuple[int, int] | None:
    """(busy, total) jiffies of all cores from the first line of `/proc/stat`."""
    try:
        fields = [int(v) for v in (proc / "stat").read_text().splitlines()[0].split()[1:9]]
    except (OSError, ValueError, IndexError):
        return None
    if len(fields) < 5:
        return None
    idle = fields[3] + fields[4]  # idle + iowait
    return sum(fields) - idle, sum(fields)


def cpu_busy(src: Sources, window: float) -> float | None:
    """The share of all cores busy (0..1) over `window` seconds; None when it cannot be read."""
    first = _cpu_times(src.proc)
    src.sleep(window)
    second = _cpu_times(src.proc)
    if first is None or second is None or second[1] <= first[1]:
        return None
    return round((second[0] - first[0]) / (second[1] - first[1]), 3)


def free_ram_gb(proc: Path) -> float | None:
    """`MemAvailable` from `/proc/meminfo`, in GB."""
    try:
        for line in (proc / "meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / GIB, 2)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _nvidia(src: Sources, query: tuple[str, ...]) -> list[list[str]]:
    """Rows of an `nvidia-smi` CSV query; raises OSError / SubprocessError / ValueError when it fails."""
    done = subprocess.run(  # noqa: S603 - a fixed query of the NVIDIA driver tool
        [*src.nvidia_smi, *query],
        capture_output=True,
        text=True,
        timeout=NVIDIA_TIMEOUT,
        check=True,
    )
    return [[v.strip() for v in line.split(",")] for line in done.stdout.strip().splitlines() if line.strip()]


def nvidia_gpu(src: Sources) -> tuple[dict[str, Any] | None, str | None]:
    """GPU 0 from `nvidia-smi`: (reading, None) or (None, why it could not be read)."""
    try:
        rows = _nvidia(src, GPU_QUERY)
        index, name, total, used, free, util = rows[0][:6]
        gpu: dict[str, Any] = {
            "index": int(index),
            "name": name,
            "total_gb": round(float(total) / 1024, 2),
            "used_gb": round(float(used) / 1024, 2),
            "free_gb": round(float(free) / 1024, 2),
            "utilization_pct": float(util),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return None, f"nvidia-smi: {type(e).__name__}: {e}"
    try:
        apps = _nvidia(src, APPS_QUERY)
        gpu["processes"] = [
            {"pid": int(pid), "name": proc_name, "memory_gb": round(float(mem) / 1024, 2)}
            for pid, proc_name, mem in (row[:3] for row in apps)
        ]
    except (OSError, subprocess.SubprocessError, ValueError):
        gpu["processes"] = []
    return gpu, None


def probe_gpu(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """GPU 0 from a probe snapshot, in the reading's shape; None when the probe could not read it."""
    gpus = snapshot.get("gpus")
    if not gpus:
        return None
    g = cast(Mapping[str, Any], gpus[0])
    return {
        "index": g.get("index", 0),
        "name": g.get("name"),
        "total_gb": g.get("memory_total_gb"),
        "used_gb": g.get("memory_used_gb"),
        "free_gb": g.get("memory_free_gb"),
        "utilization_pct": g.get("utilization_pct"),
        "processes": list(g.get("processes") or []),
    }


def snapshot(probe: Any) -> tuple[Mapping[str, Any] | None, str | None]:
    """The probe's snapshot, or (None, the error): a probe that raises is an unknown reading, not a crash."""
    if probe is None:
        return None, None
    try:
        return cast(Mapping[str, Any], probe.snapshot()), None
    except Exception as e:  # any failure of the probe is an unknown reading
        return None, f"{type(e).__name__}: {e}"


def read(src: Sources, probe: Any, *, window: float, lock: str | None) -> dict[str, Any]:
    """One reading of the machine: CPU, RAM, GPU 0 (the probe's, else `nvidia-smi`), the probe's model
    state and the lock. Values that cannot be read are left out; the reasons are in `errors`."""
    errors: dict[str, str] = {}
    reading: dict[str, Any] = {"at": src.stamp(), "cpu_busy": cpu_busy(src, window)}
    reading["free_ram_gb"] = free_ram_gb(src.proc)
    snap, error = snapshot(probe)
    if error:
        errors["probe"] = error
    gpu = probe_gpu(snap) if snap is not None else None
    if gpu is None:
        gpu, why = nvidia_gpu(src)
        if why:
            errors["gpu"] = why
    reading["gpu"] = gpu
    if snap is not None:
        for key in ("loaded_models", "servers", "leases"):
            reading[key] = list(snap.get(key) or [])
        reading["probe_gpu_lock"] = snap.get("gpu_lock")
    if lock is not None:
        reading["gpu_lock"] = lock
    reading = {k: v for k, v in reading.items() if v is not None}
    if errors:
        reading["errors"] = errors
    return reading
