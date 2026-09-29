"""AC-31: a busy reading before a sample makes the run wait (run.json `waiting` with the reasons, status
`waiting`, `start` refused); a good reading continues it; STOP ends a wait; `wait_timeout` stops the run with
`stopped_because`; `on_violation = "stop"` stops at once and `start` resumes; `record_only` runs and marks;
waiting time is in no sample's seconds and not in the budget."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hone_select import HoneSelectError
from hone_select.cli import app
from hone_select.experiments import Project, start

from .condition_helpers import BUSY, CONDITIONS_EXPERIMENT, QUIET, FakeMachine, ready, results_of, run_json

pytestmark = pytest.mark.e2e

CPU = "max_cpu_load = 0.5"


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def test_ac31_a_busy_machine_makes_the_run_wait_then_continue(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY, BUSY, QUIET])
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=CPU), machine.sources())
    machine.readings = 0  # the plan took a reading
    seen: dict[str, Any] = {}

    def look(_: FakeMachine) -> None:
        if seen:
            return
        seen["run"] = run_json(folder)
        seen["status"] = Project(tmp_path).status("E0001")
        with pytest.raises(HoneSelectError, match="already waiting"):
            start(p, "E0001", sources=machine.sources())
        seen["cli"] = (
            CliRunner().invoke(app, ["experiments", "status", "E0001", "--project", str(tmp_path)]).output
        )

    machine.on_poll.append(look)
    status = start(p, "E0001", sources=machine.sources())
    assert seen["run"]["state"] == "waiting"
    assert seen["run"]["waiting"]["reasons"] == ["max_cpu_load: 0.90 of the CPU busy, limit 0.5"]
    waiting = seen["run"]["waiting"]
    assert (_at(waiting["until"]) - _at(waiting["since"])).total_seconds() == 1800  # wait_timeout
    assert seen["status"]["status"] == "waiting"
    assert "waiting" in seen["cli"]
    assert "(max_cpu_load: 0.90 of the CPU busy, limit 0.5)" in seen["cli"]
    assert status["status"] == "completed"
    run = run_json(folder)
    assert run["state"] == "completed"
    assert "waiting" not in run
    assert [w["outcome"] for w in run["waits"]] == ["conditions met"]
    assert run["stopped_because"] is None
    assert all(r["environment"]["status"] == "ok" for r in results_of(folder))
    # 20 fake seconds of waiting are in no sample's time
    assert all(r["measurements"]["seconds"] < 10 for r in results_of(folder))


def test_ac31_stop_ends_a_wait(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY])
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=CPU), machine.sources())

    def stop_at_the_third_poll(m: FakeMachine) -> None:
        if m.polls == 3:
            (folder / "STOP").write_text("now")

    machine.on_poll.append(stop_at_the_third_poll)
    status = start(p, "E0001", sources=machine.sources())
    assert status["status"] == "stopped"
    run = run_json(folder)
    assert run["waits"][0]["outcome"] == "stopped"
    assert run["stopped_because"] is None  # a person stopped it, not the conditions
    assert machine.polls == 3
    assert results_of(folder) == []


def test_ac31_wait_timeout_stops_the_run(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY])
    exp = CONDITIONS_EXPERIMENT.format(conditions=f"{CPU}\nwait_timeout = 60")
    p, folder = ready(tmp_path, exp, machine.sources())
    status = start(p, "E0001", sources=machine.sources())
    assert status["status"] == "stopped"
    run = run_json(folder)
    assert run["stopped_because"] == ["max_cpu_load: 0.90 of the CPU busy, limit 0.5"]
    assert run["waits"][0]["outcome"] == "timed out"
    assert machine.polls == 6  # 60 s in 10 s polls
    out = CliRunner().invoke(app, ["experiments", "status", "E0001", "--project", str(tmp_path)]).output
    assert "(stopped: max_cpu_load" in out


def test_ac31_stop_mode_stops_at_once_and_start_resumes(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [QUIET, QUIET, BUSY])  # the plan, the first check, then busy
    exp = CONDITIONS_EXPERIMENT.format(conditions=f'{CPU}\non_violation = "stop"')
    p, folder = ready(tmp_path, exp, machine.sources())
    status = start(p, "E0001", sources=machine.sources())
    assert status["status"] == "stopped"
    assert run_json(folder)["stopped_because"] == ["max_cpu_load: 0.90 of the CPU busy, limit 0.5"]
    assert machine.polls == 0  # no waiting
    assert len(list(folder.glob("outputs/*/*/*/outside-1.json"))) == 1  # the sample after which it was busy
    assert results_of(folder) == []
    quiet = FakeMachine(tmp_path / "later")
    assert start(p, "E0001", sources=quiet.sources())["status"] == "completed"
    done = results_of(folder)
    assert len(done) == 2
    assert sorted(r["environment"]["attempt"] for r in done) == [1, 2]  # the set-aside sample ran once more


def test_ac31_record_only_runs_and_marks(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY])
    exp = CONDITIONS_EXPERIMENT.format(conditions=f'{CPU}\non_violation = "record_only"')
    p, folder = ready(tmp_path, exp, machine.sources())
    assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    assert machine.polls == 0
    done = results_of(folder)
    assert [r["environment"]["status"] for r in done] == ["outside", "outside"]
    assert not list(folder.glob("outputs/*/*/*/outside-1.json"))


def test_ac31_waiting_is_not_in_the_time_budget(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path, [BUSY, BUSY, QUIET])
    exp = CONDITIONS_EXPERIMENT.format(conditions=CPU) + "[budget]\nseconds = 10\n"
    p, folder = ready(tmp_path, exp, machine.sources())
    assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    assert run_json(folder)["waits"]  # it waited 20 fake seconds, more than the budget
