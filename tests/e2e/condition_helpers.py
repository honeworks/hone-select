"""A scripted machine for run-condition tests (design change 0010 §14): fake /proc files, a fake nvidia-smi,
a fake clock whose sleeps pass instantly, and states that change per reading or per poll."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hone_select.experiments import Project
from hone_select.experiments.conditions import SHORT_WINDOW, Sources

from .experiment_helpers import project

QUIET = {"cpu": 0.05, "ram_gb": 20.0, "gpu_free_gb": 7.5, "gpu_util": 1.0, "nvidia": "ok"}
BUSY = {**QUIET, "cpu": 0.9}

SMI = """#!{python}
import json, sys
state = json.load(open({state!r}))
if state["nvidia"] != "ok":
    print("NVIDIA-SMI has failed", file=sys.stderr)
    sys.exit(9)
if any(a.startswith("--query-gpu") for a in sys.argv):
    used = 8192 - state["gpu_free_gb"] * 1024
    print(f"0, Fake GPU, 8192, {{used:.0f}}, {{state['gpu_free_gb'] * 1024:.0f}}, {{state['gpu_util']:.0f}}")
else:
    print("3310, ollama, 300")
"""

# two setups (small, large), each case once: small's samples run first (run.order = "model")
CONDITIONS_EXPERIMENT = """
title = "Writers"
question = "Which model writes longest?"
registry = ["subjects"]
[generate]
kind = "python"
function = "subjects:write"
[factors]
model = ["small", "large"]
[criteria]
scorers = ["length"]
measure = {{ words = "higher" }}
[conditions]
{conditions}
"""

PROMPT_EXPERIMENT = """
title = "Prompts"
question = "Which model?"
[generate]
kind = "prompt"
client = "fake_client:text"
prompt = "Write about {{topic}}."
[factors]
model = ["tiny", "big"]
[conditions]
{conditions}
"""

ONE_CASE = '[[case]]\nid = "keeper"\ntopic = "the lighthouse keeper"\n'


class FakeMachine:
    """`states[i]` is the machine during reading i (the last repeats); `on_poll` runs at every poll while a
    run waits, `on_reading[i]` when reading i starts. Sleeps advance the fake clock only."""

    def __init__(self, tmp: Path, states: list[dict[str, Any]] | None = None, *, window: float = 1.0) -> None:
        self.dir = tmp / "machine"
        (self.dir / "proc").mkdir(parents=True, exist_ok=True)
        self.states = states or [QUIET]
        self.window, self.now, self.readings, self.polls = window, 1_800_000_000.0, 0, 0
        self.busy, self.total = 1000, 10000
        self.on_poll: list[Callable[[FakeMachine], None]] = []
        self.on_reading: dict[int, Callable[[FakeMachine], None]] = {}
        self.smi = self.dir / "nvidia-smi"
        self.smi.write_text(SMI.format(python=sys.executable, state=str(self.dir / "state.json")))
        self.smi.chmod(self.smi.stat().st_mode | stat.S_IEXEC)
        self._apply(self.states[0])

    def _apply(self, state: dict[str, Any]) -> None:
        self.state = {**QUIET, **state}
        (self.dir / "state.json").write_text(json.dumps(self.state))
        mem = int(self.state["ram_gb"] * 1024 * 1024)
        (self.dir / "proc" / "meminfo").write_text(f"MemTotal: 33554432 kB\nMemAvailable: {mem} kB\n")
        idle = self.total - self.busy
        (self.dir / "proc" / "stat").write_text(f"cpu  {self.busy} 0 0 {idle} 0 0 0 0\n")

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if seconds in (self.window, SHORT_WINDOW):  # the CPU window of one reading
            if self.readings in self.on_reading:
                self.on_reading[self.readings](self)
            state = self.states[min(self.readings, len(self.states) - 1)]
            self.readings += 1
            ticks = 2400
            self.busy += int(ticks * {**QUIET, **state}["cpu"])
            self.total += ticks
            self._apply(state)
        else:
            self.polls += 1
            for hook in self.on_poll:
                hook(self)

    def sources(self, probe: Any = None, *, nvidia: bool = True) -> Sources:
        smi = (str(self.smi),) if nvidia else (str(self.dir / "missing-nvidia-smi"),)
        return Sources(
            proc=self.dir / "proc",
            nvidia_smi=smi,
            clock=lambda: self.now,
            sleep=self.sleep,
            poll=10.0,
            window=self.window,
            probe=probe,
        )


def ready(tmp: Path, experiment: str, src: Sources, cases: str = ONE_CASE) -> tuple[Project, Path]:
    """An approved experiment, planned with `src`."""
    p, folder = project(tmp, experiment, cases)
    p.plan("E0001", sources=src)
    p.review("E0001", "approved", "go", by="tester")
    return p, folder


def results_of(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(folder.glob("outputs/*/*/*/result.json"))]


def run_json(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "run.json").read_text())


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
