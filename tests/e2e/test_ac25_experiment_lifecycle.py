"""AC-25: an experiment is created with a template, planned (every cell, estimates, the exact commands),
approved or denied, cannot start unless approved for its current definition, and the CLI does all of it."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hone_select import ConfigError, HoneSelectError
from hone_select.cli import app
from hone_select.experiments import Project, start
from hone_select.experiments import definition as d

from .experiment_helpers import PYTHON_EXPERIMENT, project

pytestmark = pytest.mark.e2e


def test_ac25_new_creates_folder_and_template(tmp_path: Path) -> None:
    p = Project(tmp_path)
    first, second = p.new("Open-weight writing!"), p.new("Second")
    assert first.name == "E0001-open-weight-writing"
    assert second.name == "E0002-second"
    for sub in ("cases/cases.toml", "prompts/plain.md", "experiment.toml"):
        assert (first / sub).is_file()
    spec = d.load(first)  # the template is a valid definition
    assert spec.title == "Open-weight writing!"
    assert p.eids() == ["E0001", "E0002"]
    assert p.status("E0001")["status"] == "draft"


def test_ac25_plan_expands_every_cell_and_shows_what_runs(tmp_path: Path) -> None:
    p, folder = project(tmp_path)
    plan = p.plan("E0001")
    assert plan["outputs"] == 2 * 6 * 2  # cases x setups (3 models x 2 temperatures) x samples
    assert len(plan["setups"]) == 6
    assert plan["baselines"] == {"today": d.setup_id({"model": "small", "temperature": 0.0})}
    assert plan["commands"] == ["python: subjects:write(case, setup, ctx)"]
    assert plan["estimate"]["seconds"] is None  # no pilot: unknown, never invented
    assert json.loads((folder / "plan.json").read_text())["definition_hash"] == d.definition_hash(folder)
    assert p.status("E0001")["status"] == "proposed"


def test_ac25_pilot_measures_one_sample(tmp_path: Path) -> None:
    p, _ = project(tmp_path)
    plan = p.plan("E0001", pilot=True)
    assert plan["pilot"]["error"] is None
    assert plan["estimate"]["seconds"] > 0
    assert p.status("E0001")["done"] == 0  # the pilot sample is not an output


def test_ac25_only_an_approved_current_plan_starts(tmp_path: Path) -> None:
    p, folder = project(tmp_path)
    with pytest.raises(HoneSelectError, match="draft"):
        start(p, "E0001")
    with pytest.raises(HoneSelectError, match="only a proposed experiment"):
        p.review("E0001", "approved")
    p.plan("E0001")
    with pytest.raises(HoneSelectError, match="proposed"):
        start(p, "E0001")
    p.review("E0001", "denied", "too big", by="owner")
    assert p.status("E0001")["status"] == "denied"
    p.plan("E0001")  # a new plan of the same definition is proposed again
    assert p.status("E0001")["status"] == "denied"  # the decision on this definition still stands
    (folder / "experiment.toml").write_text(PYTHON_EXPERIMENT.replace("samples = 2", "samples = 1"))
    status = p.status("E0001")
    assert status["status"] == "draft"
    assert status["changed_since_plan"] is True
    p.plan("E0001")
    assert p.status("E0001")["status"] == "proposed"
    entry = p.review("E0001", "approved", "smaller now", by="owner")
    assert entry["definition_hash"] == d.definition_hash(folder)
    assert p.status("E0001")["status"] == "approved"
    assert [r["decision"] for r in json.loads((folder / "review.json").read_text())] == ["denied", "approved"]


def test_ac25_bad_definitions_are_clear_errors(tmp_path: Path) -> None:
    p, folder = project(tmp_path)
    (folder / "experiment.toml").write_text('title = "x"\n[generate]\nkind = "python"\n')
    with pytest.raises(ConfigError, match="needs `function`"):
        p.plan("E0001")
    (folder / "experiment.toml").write_text(
        PYTHON_EXPERIMENT.replace('scorers = ["length"]', 'scorers = ["nope"]')
    )
    with pytest.raises(ConfigError, match="'nope' is neither"):
        p.plan("E0001")
    (folder / "experiment.toml").write_text("title = [")
    with pytest.raises(ConfigError, match="not valid TOML"):
        p.plan("E0001")
    with pytest.raises(HoneSelectError, match="no experiment 'E0009'"):
        p.status("E0009")


def test_ac25_cli_runs_the_whole_lifecycle(tmp_path: Path) -> None:
    project(tmp_path)
    run = CliRunner()
    at = ["--project", str(tmp_path)]
    out = run.invoke(app, ["experiments", "plan", "E0001", *at])
    assert out.exit_code == 0, out.output
    assert "24 outputs" in out.output
    assert run.invoke(app, ["experiments", "deny", "E0001", "--note", "no", *at]).exit_code == 0
    assert "denied" in run.invoke(app, ["experiments", "status", "E0001", *at]).output
    (tmp_path / "experiments" / "E0001-writers" / "cases" / "cases.toml").write_text(
        '[[case]]\nid = "keeper"\ntopic = "the lighthouse keeper"\n'
    )
    assert run.invoke(app, ["experiments", "plan", "E0001", *at]).exit_code == 0
    assert run.invoke(app, ["experiments", "approve", "E0001", "--note", "ok", *at]).exit_code == 0
    started = run.invoke(app, ["experiments", "start", "E0001", *at])
    assert started.exit_code == 0, started.output
    assert "completed" in started.output
    listed = json.loads(run.invoke(app, ["experiments", "list", "--json", *at]).output)
    assert listed[0]["status"] == "completed"
    assert "best setup" in run.invoke(app, ["experiments", "report", "E0001", *at]).output
    assert run.invoke(app, ["experiments", "stop", "E0001", *at]).exit_code == 0
    created = run.invoke(app, ["experiments", "new", "Another one", *at])
    assert "E0002-another-one" in created.output
    unplanned = run.invoke(app, ["experiments", "report", "E0002", *at])
    assert unplanned.exit_code != 0
