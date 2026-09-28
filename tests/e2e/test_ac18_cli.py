"""AC-18: `hone-select run` on the example config prints the winner, `--json` has a fixed schema, and
`explain` / `show` rebuild the run from the span store alone."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hone_select.cli import app, main

pytestmark = pytest.mark.e2e

EXAMPLE = Path(__file__).parents[2] / "examples" / "cli"
RUN = ["run", "selection.toml", "--task", "task.json", "--registry", "shortest_line"]
runner = CliRunner()


@pytest.fixture(autouse=True)
def in_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(EXAMPLE)
    monkeypatch.setattr(sys, "path", list(sys.path))  # the CLI adds the cwd to sys.path


def test_ac18_run_prints_the_winner() -> None:
    result = runner.invoke(app, RUN)
    assert result.exit_code == 0, result.output
    first, *rest = result.output.splitlines()
    data = json.loads(runner.invoke(app, [*RUN, "--json"]).output)
    winner = data["winner"]
    assert first.startswith(f"winner {winner['candidate']['id']}  total={winner['total']}  run=")
    assert json.loads("\n".join(rest)) == "hello #0"  # the shortest line wins


def test_ac18_no_winner(tmp_path: Path) -> None:
    task = tmp_path / "long.json"
    task.write_text(json.dumps({"topic": "x" * 90}))  # every line fails the not_too_long gate
    result = runner.invoke(app, ["run", "selection.toml", "--task", str(task), "--registry", "shortest_line"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("no winner (run ")
    assert "hone-select explain " in result.output


def test_ac18_json_schema_and_explain_from_db(hone_home: Path) -> None:
    out = runner.invoke(app, [*RUN, "--json", "--seed", "3"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert set(data) == {"run_id", "trace_id", "winner", "ranked", "decision", "budget"}
    assert set(data["winner"]) == {"candidate", "gates", "scores", "total", "rejected", "stage_reached"}
    assert set(data["winner"]["candidate"]) == {"id", "data", "files", "meta"}
    assert data["winner"]["candidate"]["data"] == "hello #0"
    assert data["winner"]["scores"]["shorter_is_better"]["value"] == pytest.approx(0.92)
    assert data["winner"]["candidate"]["meta"]["seed"] == 3
    assert len(data["ranked"]) == 5

    explained = runner.invoke(app, ["explain", data["run_id"]])
    assert explained.exit_code == 0, explained.output
    assert f"1. {data['winner']['candidate']['id']}" in explained.output
    assert "selected" in explained.output

    db = hone_home / "select" / "spans.db"
    shown = runner.invoke(app, ["show", data["run_id"], "--db", str(db), "--json"])
    spans = json.loads(shown.output)
    assert {s["trace_id"] for s in spans} == {data["trace_id"]}
    assert {"hone.select.run", "hone.select.generate", "hone.select.score", "hone.select.decision"} <= {
        s["name"] for s in spans
    }
    text = runner.invoke(app, ["show", data["run_id"]]).output
    assert "hone.select.decision" in text


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["explain", "0000000000000000"], "error: no span store at"),
        (
            ["run", "selection.toml", "--task", "task.json", "--registry", "nope"],
            "error: cannot import registry",
        ),
        (["run", "selection.toml", "--task", "task.json", "--registry", "json"], "error: "),
    ],
)
def test_ac18_errors_are_one_line(
    args: list[str], message: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["hone-select", *args])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith(message)
    assert err.count("\n") == 1


def test_ac18_show_unknown_run_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner.invoke(app, RUN).exit_code == 0  # creates the store
    monkeypatch.setattr(sys, "argv", ["hone-select", "show", "0000000000000000"])
    with pytest.raises(SystemExit):
        main()
    assert capsys.readouterr().err.startswith("error: no decision for run '0000000000000000'")


def test_ac18_bad_task_file(tmp_path: Path) -> None:
    missing = runner.invoke(
        app, ["run", "selection.toml", "--task", "nope.json", "--registry", "shortest_line"]
    )
    assert missing.exit_code == 2  # usage error, no traceback
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = runner.invoke(app, ["run", "selection.toml", "--task", str(bad), "--registry", "shortest_line"])
    assert "is not valid JSON" in str(result.exception)


def test_ac18_python_dash_m_runs_the_cli(tmp_path: Path) -> None:
    import re
    import subprocess

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "hone_select.cli", *args]
        return subprocess.run(command, capture_output=True, text=True, check=False)

    done = cli("--help")
    assert done.returncode == 0, done.stderr
    help_text = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout)  # rich colours help output on CI (GITHUB_ACTIONS)
    for command in ("run", "explain", "show"):
        assert re.search(rf"^\W*{command}\s", help_text, re.M), command
    failed = cli("explain", "nope", "--db", str(tmp_path / "missing.db"))  # through main()'s error handling
    assert failed.returncode == 1
    assert failed.stderr.startswith("error: no span store")
