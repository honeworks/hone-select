"""AC-32: every sample's result.json has an `environment` (before / after readings, checks, status), also
without `[conditions]` (`not_checked`); a sample outside after it ran is set aside as `outside-1.json` and run
once more, a second outside run is kept and marked; outside and unknown samples are left out of every number
and of the selection, counted per setup and factor level with the reasons; `report --include-outside`
counts them and says so; old result.json files without an environment count as before."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hone_select.cli import app
from hone_select.experiments import definition as d
from hone_select.experiments import report, start

from .condition_helpers import BUSY, CONDITIONS_EXPERIMENT, QUIET, FakeMachine, ready, results_of
from .experiment_helpers import CASES, approved

pytestmark = pytest.mark.e2e

CPU = "max_cpu_load = 0.5"
SMALL, LARGE = d.setup_id({"model": "small"}), d.setup_id({"model": "large"})


def test_ac32_every_sample_has_an_environment_also_without_conditions(tmp_path: Path) -> None:
    p, folder = approved(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=""))
    start(p, "E0001")
    for r in results_of(folder):
        env = r["environment"]
        assert env["status"] == "not_checked"
        assert env["checks"] == {}
        assert env["before"]["free_ram_gb"] == 16.0  # the quiet fake machine of the default suite
        assert "gpu" not in env["before"]  # no nvidia-smi: left out, never 0
        assert env["after"]["at"]
        assert env["attempt"] == 1
        assert env["cold"] is False


def test_ac32_readings_and_checks_are_recorded(tmp_path: Path) -> None:
    machine = FakeMachine(tmp_path)
    exp = CONDITIONS_EXPERIMENT.format(
        conditions=f"{CPU}\nmin_free_vram_gb = 4\nmax_gpu_utilization_pct = 20"
    )
    p, folder = ready(tmp_path, exp, machine.sources())
    start(p, "E0001", sources=machine.sources())
    env = results_of(folder)[0]["environment"]
    assert env["status"] == "ok"
    assert env["before"]["cpu_busy"] == 0.05
    assert env["before"]["gpu"]["free_gb"] == 7.5
    assert env["before"]["gpu"]["free_for_run_gb"] == 7.5  # no probe: the plain free memory
    assert env["before"]["gpu"]["processes"] == [{"pid": 3310, "name": "ollama", "memory_gb": 0.29}]
    assert env["checks"]["max_cpu_load"] == {"state": "ok", "before": 0.05, "after": 0.05, "limit": 0.5}
    assert set(env["checks"]) == {"max_cpu_load", "min_free_vram_gb", "max_gpu_utilization_pct"}


def test_ac32_an_outside_sample_is_set_aside_and_run_once_more(tmp_path: Path) -> None:
    # readings: the plan, before s1, after s1 (busy: set aside), a poll (quiet), after the re-run, ...
    machine = FakeMachine(tmp_path, [QUIET, QUIET, BUSY, QUIET])
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=CPU), machine.sources())
    assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    aside = list(folder.glob("outputs/*/*/*/outside-1.json"))
    assert len(aside) == 1
    first = json.loads(aside[0].read_text())
    assert first["environment"]["status"] == "outside"
    assert first["environment"]["checks"]["max_cpu_load"]["after"] == 0.9
    again = json.loads(aside[0].with_name("result.json").read_text())
    assert (again["environment"]["status"], again["environment"]["attempt"]) == ("ok", 2)


def test_ac32_a_second_outside_run_is_kept_and_marked(tmp_path: Path) -> None:
    # every reading after a sample is busy, every poll quiet: both attempts end outside
    machine = FakeMachine(tmp_path, [QUIET, QUIET, BUSY, QUIET, BUSY, QUIET, BUSY, QUIET, BUSY])
    p, folder = ready(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=CPU), machine.sources())
    assert start(p, "E0001", sources=machine.sources())["status"] == "completed"
    kept = results_of(folder)
    assert [(r["environment"]["status"], r["environment"]["attempt"]) for r in kept] == [("outside", 2)] * 2
    res = json.loads((folder / "results" / "results.json").read_text())
    assert res["setups"][SMALL]["samples"] == 0
    assert res["setups"][SMALL]["outside"] == 1
    summary = (folder / "results" / "summary.md").read_text()
    assert "no samples in conditions" in summary


def _record_only(tmp_path: Path) -> tuple[Path, FakeMachine]:
    # cells: keeper/small, ferry/small, keeper/large, ferry/large; reading 3 is after keeper/large and
    # before ferry/large, so both large samples ran outside the conditions
    machine = FakeMachine(tmp_path, [QUIET, QUIET, QUIET, QUIET, BUSY, QUIET])
    exp = CONDITIONS_EXPERIMENT.format(conditions=f'{CPU}\non_violation = "record_only"')
    p, folder = ready(tmp_path, exp, machine.sources(), CASES)
    start(p, "E0001", sources=machine.sources())
    return folder, machine


def test_ac32_outside_samples_are_left_out_of_every_number(tmp_path: Path) -> None:
    folder, _ = _record_only(tmp_path)
    marks = {r["setup"]: r["environment"]["status"] for r in results_of(folder)}
    assert marks == {SMALL: "ok", LARGE: "outside"}
    res = json.loads((folder / "results" / "results.json").read_text())
    large = res["setups"][LARGE]
    assert (large["samples"], large["outside"], large["unknown"]) == (0, 2, 0)
    assert large["total"]["mean"] is None
    assert large["wins"] == 0
    assert res["factors"]["model"]["large"]["outside"] == 2
    assert res["best"] == SMALL  # large writes longer, but its samples are not counted
    assert res["conditions"]["excluded"] == 2
    assert res["conditions"]["reasons"] == {"max_cpu_load": 2}
    for case in ("keeper", "ferry"):
        selection = json.loads((folder / "outputs" / case / "selection.json").read_text())
        assert all(sid.endswith(("__s0",)) and LARGE not in sid for sid in selection["samples"])
        assert all(LARGE in sid for sid in selection["outside"])  # scored apart, never candidates
    summary = (folder / "results" / "summary.md").read_text().splitlines()
    assert summary[2] == (
        "2 of 4 samples ran outside the run conditions and are not counted (2 × max_cpu_load)."  # noqa: RUF001
    )


def test_ac32_include_outside_counts_them_and_says_so(tmp_path: Path) -> None:
    folder, _ = _record_only(tmp_path)
    out = CliRunner().invoke(
        app, ["experiments", "report", "E0001", "--include-outside", "--project", str(tmp_path)]
    )
    assert out.exit_code == 0, out.output
    assert "they are counted here" in out.output
    res = json.loads((folder / "results" / "results.json").read_text())
    assert res["setups"][LARGE]["samples"] == 2
    assert res["setups"][LARGE]["total"]["mean"] is not None
    assert res["best"] == LARGE
    assert res["conditions"]["include_outside"] is True
    assert "they are counted here" in (folder / "results" / "summary.md").read_text()


def test_ac32_old_results_without_an_environment_count_as_before(tmp_path: Path) -> None:
    p, folder = approved(tmp_path, CONDITIONS_EXPERIMENT.format(conditions=""))
    start(p, "E0001")
    before = json.loads((folder / "results" / "results.json").read_text())
    for path in folder.glob("outputs/*/*/*/result.json"):
        r = json.loads(path.read_text())
        del r["environment"]
        path.write_text(json.dumps(r))
    res = report(folder, d.load(folder), json.loads((folder / "plan.json").read_text()))
    assert res["setups"][LARGE]["samples"] == 2
    assert res["conditions"]["excluded"] == 0
    assert res["setups"] == before["setups"]  # the same numbers
