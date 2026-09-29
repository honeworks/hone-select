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
    assert by_model["big"]["cost_usd"] is None  # the client reported no cost: unknown, not $0


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


TEMPFAIL_SCRIPT = """
import json, sys, pathlib
p = json.loads(sys.stdin.read())
marker = pathlib.Path(p["workdir"]).parent / "attempts"
tries = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(tries))
if p["setup"]["mode"] == "always" or tries < 2:
    sys.exit(75)
print(json.dumps({"data": f"ok after {tries}"}))
"""


@pytest.mark.parametrize(
    ("mode", "data", "error", "tries"),
    [("once", "ok after 2", None, "2"), ("always", None, "exit code 75", "3")],
)
def test_ac26_exit_code_75_is_retried(
    tmp_path: Path, mode: str, data: str | None, error: str | None, tries: str
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tempfail.py").write_text(TEMPFAIL_SCRIPT)
    exp = experiment(
        'kind = "command"\ncommand = ["python3", "{root}/scripts/tempfail.py"]\nretries = 2',
        f'mode = ["{mode}"]',
    )
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["data"] == data
    assert (row["error"] or "").startswith(error or "")
    assert (row["error"] is None) is (error is None)
    assert (next(folder.glob("outputs/*/*/*")) / "attempts").read_text() == tries  # 1 + retries at most


def test_ac26_a_prompt_client_that_cannot_be_built_stops_the_run(tmp_path: Path) -> None:
    from hone_select import ConfigError

    exp = experiment(
        'kind = "prompt"\nclient = "fake_client:text"\nprompt = "hi"', 'model = ["unknown-model"]'
    )
    p, _ = approved(
        tmp_path, exp.replace('registry = ["subjects"]\n', ""), CASES.split('[[case]]\nid = "ferry"')[0]
    )
    with pytest.raises(ConfigError, match="could not be built for 'unknown-model'"):
        start(p, "E0001")
    assert p.status("E0001")["status"] == "stopped"


SECRET = "sk-test" + "PLANTED0123456789"
LEAKY = """
import json, os, sys
json.loads(sys.stdin.read())
print("key is", os.environ["API_KEY"], file=sys.stderr)
print(json.dumps({"data": {"key_length": len(os.environ["API_KEY"])}}))
"""


@pytest.mark.parametrize("form", ["literal", "variable"])
def test_ac26_secrets_stay_out_of_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str) -> None:
    from hone_select.dashboard import make_server
    from hone_select.experiments.web import detail

    monkeypatch.setenv("HONE_TEST_SECRET", SECRET)
    value = SECRET if form == "literal" else "$HONE_TEST_SECRET"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "leaky.py").write_text(LEAKY)
    exp = experiment(
        f'kind = "command"\ncommand = ["python3", "{{root}}/scripts/leaky.py"]\n'
        f'env = {{ API_KEY = "{value}", password = "hunter2-plain" }}',
        'model = ["m"]',
    )
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["data"] == {"key_length": len(SECRET)}  # the subject got the real value
    assert "***" in row["log"]
    written = [q for q in folder.rglob("*") if q.is_file() and q.name != "experiment.toml"]
    assert all(SECRET.encode() not in q.read_bytes() for q in written)
    assert SECRET.encode() not in (tmp_path / ".hone" / "select" / "spans.db").read_bytes()
    served = json.dumps(detail(p, "E0001"))
    assert SECRET not in served
    assert "hunter2-plain" not in served  # no key shape: hidden only because its key is named like a secret
    assert 'password = "***"' in detail(p, "E0001")["definition"]
    if form == "variable":
        assert SECRET not in (folder / "experiment.toml").read_text()
    make_server(tmp_path / ".hone" / "select" / "spans.db", port=0, project=tmp_path).server_close()


FAILING_LEAK = """
import json, os, sys
json.loads(sys.stdin.read())
print("could not log in with", os.environ["API_KEY"], file=sys.stderr)
sys.exit(2)
"""


def test_ac26_a_secret_in_a_failing_subject_error_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HONE_TEST_SECRET", SECRET)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fail.py").write_text(FAILING_LEAK)
    exp = experiment(
        'kind = "command"\ncommand = ["python3", "{root}/scripts/fail.py"]\n'
        'env = { API_KEY = "$HONE_TEST_SECRET" }',
        'model = ["m"]',
    )
    p, folder = approved(tmp_path, exp, CASES.split('[[case]]\nid = "ferry"')[0])
    start(p, "E0001")
    (row,) = results(folder)
    assert row["error"].startswith("exit code 2: could not log in with ***")
    assert SECRET not in row["error"]
    assert SECRET not in row["log"]
