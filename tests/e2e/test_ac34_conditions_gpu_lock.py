"""AC-34: with `gpu_lock`, no other process can take the GPU lock during the run (samples, waits and
scoring) and can right after; the holder file is written and removed; a run whose lock is held elsewhere
waits (visible in run.json) and stops at `wait_timeout`; with HONE_GPU_LOCK_HELD=1 at start the lock is not
taken again; subjects receive HONE_GPU_LOCK_HELD=1; the lock is released when the process is killed.

The lock is a file in the test's temporary folder ($HONE_GPU_LOCK, set by tests/conftest.py)."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hone_select.experiments import start
from hone_select.experiments.gpulock import release, try_lock

from .condition_helpers import BUSY, CONDITIONS_EXPERIMENT, QUIET, FakeMachine, ready, results_of, run_json

pytestmark = pytest.mark.e2e

TRY_LOCK = """
import fcntl, json, os, sys
json.loads(sys.stdin.read())
fd = os.open(os.environ["HONE_GPU_LOCK"], os.O_RDWR | os.O_CREAT)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    free = True
except BlockingIOError:
    free = False
holder = open(os.environ["HONE_GPU_LOCK"] + ".holder").read()
print(json.dumps({"data": {"free": free, "held": os.environ.get("HONE_GPU_LOCK_HELD"), "holder": holder}}))
"""

SCORING = """
import fcntl, os
from hone_select import scorer

SEEN = []


@scorer("lock_is_taken")
def lock_is_taken(c):
    fd = os.open(os.environ["HONE_GPU_LOCK"], os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return 0.0
    except BlockingIOError:
        return 1.0
    finally:
        os.close(fd)
"""


def _lock() -> Path:
    return Path(os.environ["HONE_GPU_LOCK"])


def _command_experiment(tmp: Path, conditions: str) -> str:
    (tmp / "try_lock.py").write_text(TRY_LOCK)
    (tmp / "lockcheck.py").write_text(SCORING)
    return (
        CONDITIONS_EXPERIMENT.format(conditions=conditions)
        .replace(
            'kind = "python"\nfunction = "subjects:write"',
            f'kind = "command"\ncommand = ["{sys.executable}", "try_lock.py"]',
        )
        .replace('registry = ["subjects"]', 'registry = ["lockcheck"]')
        .replace('scorers = ["length"]\nmeasure = { words = "higher" }', 'scorers = ["lock_is_taken"]')
    )


def test_ac34_the_run_holds_the_lock_from_start_to_scoring(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [QUIET, BUSY, QUIET])  # the plan, a busy first check, then quiet
    exp = _command_experiment(tmp_path, "gpu_lock = true\nmax_cpu_load = 0.5")
    p, folder = ready(tmp_path, exp, machine.sources())
    during_wait: list[bool] = []
    holders: list[str] = []

    def look(_: FakeMachine) -> None:
        fd = try_lock(_lock())
        during_wait.append(fd is None)
        if fd is not None:
            release(fd, _lock())
        holders.append(Path(f"{_lock()}.holder").read_text())

    machine.on_poll.append(look)
    assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    assert during_wait == [True]  # nobody else got the lock while the run waited
    assert holders[0].split()[0] == tmp_path.name  # <project folder> <pid> <time> <experiment>
    assert holders[0].split()[1] == str(os.getpid())
    assert holders[0].split()[-1] == "E0001"
    for r in results_of(folder):
        assert r["data"]["free"] is False  # a subject cannot take it either
        assert r["data"]["held"] == "1"  # and is told that the lock is held
        assert r["environment"]["before"]["gpu_lock"] == "held by this run"
    selection = json.loads((folder / "outputs" / "keeper" / "selection.json").read_text())
    assert all(s["scores"]["lock_is_taken"]["value"] == 1.0 for s in selection["samples"].values())  # scoring
    fd = try_lock(_lock())  # free right after the run
    assert fd is not None
    release(fd, _lock())
    assert not Path(f"{_lock()}.holder").exists()
    assert "HONE_GPU_LOCK_HELD" not in os.environ


def test_ac34_a_lock_held_elsewhere_makes_the_run_wait_then_stop(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path)
    p, folder = ready(
        tmp_path,
        CONDITIONS_EXPERIMENT.format(conditions="gpu_lock = true\nwait_timeout = 30"),
        machine.sources(),
    )
    other = try_lock(_lock())
    assert other is not None
    Path(f"{_lock()}.holder").write_text("hone-flow 5120 2026-10-02T09:00:00+00:00\n")
    seen: list[dict] = []
    machine.on_poll.append(lambda m: seen.append(run_json(folder)))
    try:
        assert start(p, "E0001", sources=machine.sources())["status"] == "stopped"
    finally:
        release(other, _lock())
    reason = "another process holds the GPU lock: hone-flow 5120 2026-10-02T09:00:00+00:00"
    assert seen[0]["state"] == "waiting"
    assert seen[0]["waiting"]["reasons"] == [reason]
    assert run_json(folder)["stopped_because"] == [reason]
    assert results_of(folder) == []


def test_ac34_a_parent_that_holds_the_lock_is_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine = FakeMachine(tmp_path)
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions="gpu_lock = true"), machine.sources())
    parent = try_lock(_lock())  # as scripts/gpu-lock.sh would
    assert parent is not None
    monkeypatch.setenv("HONE_GPU_LOCK_HELD", "1")
    try:
        assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    finally:
        release(parent, _lock())
    assert machine.polls == 0
    assert all(
        r["environment"]["before"]["gpu_lock"] == "held by the parent process" for r in results_of(folder)
    )
    assert all(
        c["gpu_lock"]["state"] == "ok" for r in results_of(folder) for c in [r["environment"]["checks"]]
    )


RUN = """
import sys
from pathlib import Path
from hone_select.experiments import Project, conditions, start
root = Path(sys.argv[1])
conditions.DEFAULT = conditions.Sources(proc=root / "fake-proc", nvidia_smi=(str(root / "none"),))
start(Project(root), "E0001")
"""


def test_ac34_a_killed_run_releases_the_lock(tmp_path: Path) -> None:
    exp = CONDITIONS_EXPERIMENT.format(conditions="gpu_lock = true").replace(
        "subjects:write", "subjects:sleepy"
    )
    machine = FakeMachine(tmp_path)
    p, _ = ready(tmp_path, exp, machine.sources())
    proc = subprocess.Popen([sys.executable, "-c", RUN, str(tmp_path)], cwd=tmp_path, env=dict(os.environ))
    holder = Path(f"{_lock()}.holder")
    deadline = time.monotonic() + 30
    while not holder.exists() and time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    assert holder.exists()
    assert try_lock(_lock()) is None  # held by the run
    proc.send_signal(signal.SIGKILL)
    proc.wait(10)
    fd = try_lock(_lock())
    assert fd is not None  # the kernel released it
    release(fd, _lock())
    assert p.status("E0001")["status"] == "stopped"  # the dead process's run
