"""AC-26: the subject under test is a prompt, a Python function or any command; every sample records its
data, files, time, peak memory, exit code and log, and a failure (exception, exit code, timeout, bad JSON)
is a recorded result, never a crash; transient failures are retried."""

import json
from pathlib import Path

import pytest

from hone_select.experiments import start

from .experiment_helpers import CASES, approved

pytestmark = pytest.mark.e2e


def results(folder: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(folder.glob("outputs/*/*/*/result.json"))]


def experiment(generate: str, factors: str, extra: str = "") -> str:
    return f'title = "t"\nregistry = ["subjects"]\n[generate]\n{generate}\n[factors]\n{factors}\n{extra}'


def test_ac26_python_subject_files_and_measurements(tmp_path: Path) -> None:
    p, folder = approved(tmp_path)
    start(p, "E0001")
    rows = results(folder)
    assert len(rows) == 24
    r = rows[0]
    assert r["error"] is None
    assert r["data"].startswith("the ferryman") or r["data"].startswith("the lighthouse keeper")
    assert set(r["files"]) == {"story.txt"}
    assert r["files"]["story.txt"]["size"] > 0
    m = r["measurements"]
    assert m["words"] >= 3  # the subject's own measurement
    assert m["seconds"] > 0
    assert m["peak_memory_mb"] > 0
    assert m["output_bytes"] == r["files"]["story.txt"]["size"]
    assert (folder / "outputs" / r["case"] / r["setup"] / f"s{r['sample']}" / "files" / "story.txt").is_file()


def test_ac26_command_subject(tmp_path: Path) -> None:
    exp = experiment(
        'kind = "command"\ncommand = ["python3", "{root}/scripts/render.py", "{case.id}", "{setup.size}"]',
        "size = [10, 20]\ncrash = [false, true]",
    )
    p, folder = approved(tmp_path, exp)
    plan = json.loads((folder / "plan.json").read_text())
    assert plan["commands"][0].endswith("render.py keeper 10")  # the exact command, shown before approval
    start(p, "E0001")
    rows = results(folder)
    ok = [r for r in rows if not r["params"]["crash"]]
    crashed = [r for r in rows if r["params"]["crash"]]
    assert all(r["error"] is None for r in ok)
    assert ok[0]["data"] == {"size": ok[0]["params"]["size"]}  # the last stdout line is the JSON reply
    assert ok[0]["measurements"]["pixels"] == ok[0]["params"]["size"] ** 2
    assert ok[0]["measurements"]["exit_code"] == 0
    assert ok[0]["measurements"]["peak_memory_mb"] > 0
    assert "some log line" in ok[0]["log"]
    assert set(ok[0]["files"]) == {"frame.txt"}
    assert all(r["error"].startswith("exit code 3: boom") for r in crashed)
    assert all(r["measurements"]["exit_code"] == 3 for r in crashed)
    assert p.status("E0001")["status"] == "completed"  # failures did not stop the experiment


def test_ac26_prompt_subject(tmp_path: Path) -> None:
    exp = experiment(
        'kind = "prompt"\nclient = "fake_client:text"\nprompt = "{prompt}"\noutput = "json"',
        'model = ["tiny", "big"]\nprompt = ["ask.md"]',
    )
    p, folder = approved(tmp_path, exp.replace('registry = ["subjects"]\n', ""))
    (folder / "prompts" / "ask.md").write_text("Write about {topic}.\n")
    p.plan("E0001")
    p.review("E0001", "approved", "go")
    start(p, "E0001")
    by_model = {r["params"]["model"]: r for r in results(folder)}
    assert by_model["big"]["data"] == {"premise": "a long and careful reply"}
    assert by_model["big"]["error"] is None
    assert by_model["tiny"]["error"] == "the output is not valid JSON"


@pytest.mark.parametrize(
    ("function", "error"),
    [
        ("fail_on_large", "ValueError: the large model is down"),
        ("sleepy", "timeout after 1 s"),
        ("not_json", "the subject returned data that is not JSON"),
    ],
)
def test_ac26_python_failures_are_results(tmp_path: Path, function: str, error: str) -> None:
    exp = experiment(f'kind = "python"\nfunction = "subjects:{function}"\ntimeout = 1', 'model = ["large"]')
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["error"].startswith(error)
    assert p.status("E0001")["status"] == "completed"


def test_ac26_transient_errors_are_retried(tmp_path: Path) -> None:
    exp = experiment('kind = "python"\nfunction = "subjects:flaky"\nretries = 2', 'model = ["m"]')
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["error"] is None
    assert row["data"] == "worked after 2"


def test_ac26_a_command_that_does_not_exist(tmp_path: Path) -> None:
    exp = experiment('kind = "command"\ncommand = ["no-such-program-xyz"]', 'model = ["m"]')
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["error"].startswith("cannot run 'no-such-program-xyz'")


def test_ac26_a_command_timeout_kills_it(tmp_path: Path) -> None:
    exp = experiment('kind = "command"\ncommand = ["sleep", "30"]\ntimeout = 0.5', 'model = ["m"]')
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["error"] == "timeout after 0.5 s"
    assert row["measurements"]["seconds"] < 10
