"""AC-10: CommandScorer runs an executable (JSON in, JSON out); failures become Score(None, error)."""

import sys
from pathlib import Path

import pytest

from hone_select import Candidate, CommandScorer, Engine, generator

pytestmark = pytest.mark.e2e

GOOD = """
import json, sys
c = json.load(sys.stdin)["candidate"]
print(json.dumps({"value": len(c["data"]) / 10, "reason": "length", "confidence": 0.9, "details": {"id": c["id"]}}))
"""


def script(tmp_path: Path, name: str, body: str) -> list[str]:
    path = tmp_path / name
    path.write_text(body)
    return [sys.executable, str(path)]


def test_ac10_command_scorer_parses_json(tmp_path: Path) -> None:
    length = CommandScorer("length", script(tmp_path, "good.py", GOOD))
    ranked = Engine('[score]\ncascade = [{ scorers = ["length"] }]', registry=[length]).score(
        [Candidate.of("abc"), Candidate.of("abcdef")]
    )
    top = ranked[0]
    assert top.candidate.data == "abcdef"
    score = top.scores["length"]
    assert score.value == pytest.approx(0.6)
    assert (score.reason, score.confidence, score.details) == ("length", 0.9, {"id": top.candidate.id})


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            "import sys\nsys.stderr.write('model file missing')\nsys.exit(3)",
            "exit code 3: model file missing",
        ),
        ("print('not json')", "bad JSON on stdout"),
        ("print('{\"value\": 7}')", "value 7.0 is outside 0..1"),
        ("import time\ntime.sleep(5)", "timed out after 0.5s"),
    ],
)
def test_ac10_failures_are_none_with_error(tmp_path: Path, body: str, error: str) -> None:
    broken = CommandScorer("broken", script(tmp_path, "bad.py", body), timeout_s=0.5)
    ranked = Engine('[score]\ncascade = [{ scorers = ["broken"] }]', registry=[broken]).score(
        [Candidate.of("x")]
    )
    score = ranked[0].scores["broken"]
    assert score.value is None
    assert error in score.error


def test_ac10_missing_executable(tmp_path: Path) -> None:
    missing = CommandScorer("missing", [str(tmp_path / "nope")])
    assert "could not run" in missing(Candidate.of("x")).error


def test_ac10_config_defined_command_scorer(tmp_path: Path) -> None:
    command = script(tmp_path, "good.py", GOOD)
    config = f"""
[score]
cascade = [{{ scorers = ["tests_pass"] }}]
[scorers.tests_pass]
kind = "command"
command = {command!r}
cost = 20
timeout_s = 9
version = "2"
""".replace("'", '"')
    engine = Engine(config)
    ranked = engine.score([Candidate.of("abcd")])
    assert ranked[0].total == pytest.approx(0.4)
    component = engine.components["tests_pass"]
    assert isinstance(component.fn, CommandScorer)
    assert (component.cost, component.version, component.fn.timeout_s) == (20, "2", 9)


ECHO = """
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"value": 0, "details": payload}))
"""


def test_ac10_stdin_payload_and_zero_is_not_none(tmp_path: Path) -> None:
    echo = CommandScorer("echo", script(tmp_path, "echo.py", ECHO))
    candidate = Candidate.of({"lyrics": "la"}, meta={"model": "m"})
    score = echo(candidate)
    assert score.value == 0.0
    assert score.details == {
        "candidate": {"id": candidate.id, "data": {"lyrics": "la"}, "files": {}, "meta": {"model": "m"}}
    }


@pytest.mark.parametrize(
    ("stdout", "error"),
    [
        ('{"value": null}', "command returned no value"),
        ("null", "bad JSON on stdout"),
        ("0.9", "bad JSON on stdout"),
    ],
)
def test_ac10_no_value_is_an_explicit_error(tmp_path: Path, stdout: str, error: str) -> None:
    scorer = CommandScorer("s", script(tmp_path, "s.py", f"print({stdout!r})"))
    score = scorer(Candidate.of("x"))
    assert score.value is None
    assert error in score.error


def test_ac10_failure_is_in_the_decision_trace(tmp_path: Path) -> None:
    @generator()
    def gen(task, v):
        return "x"

    broken = CommandScorer("broken", script(tmp_path, "bad.py", "import sys; sys.exit(1)"))
    config = '[generate]\nn = 1\n[score]\ncascade = [{ scorers = ["broken"] }]'
    result = Engine(config, registry=[gen, broken]).run("t")
    assert any(e["event"] == "score_error" and e["scorer"] == "broken" for e in result.decision)


def test_ac10_timeout_includes_stderr(tmp_path: Path) -> None:
    body = "import sys, time\nsys.stderr.write('loading model'); sys.stderr.flush()\ntime.sleep(5)"
    slow = CommandScorer("slow", script(tmp_path, "slow.py", body), timeout_s=0.5)
    assert "loading model" in slow(Candidate.of("x")).error
