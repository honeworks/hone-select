"""Builders for throw-away experiment projects (design change 0009 tests)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from hone_select.experiments import Project

SUBJECTS = '''
import json
import time
from pathlib import Path

from hone_select import Candidate, gate, scorer
from hone_select.experiments import TransientError


def write(case, setup, ctx):
    """A story whose length grows with the model's rank and the temperature (a known best setup)."""
    words = {"small": 3, "medium": 6, "large": 9}[setup["model"]] + int(setup.get("temperature", 0) * 4)
    (ctx.workdir / "story.txt").write_text(case["fields"]["topic"] + " " + "word " * words)
    return Candidate.of(f"{case['fields']['topic']} " + "word " * words, meta={"measurements": {"words": words}})


def fail_on_large(case, setup, ctx):
    if setup["model"] == "large":
        raise ValueError("the large model is down")
    return "ok " * 3


FLAKY = {"calls": 0}


def flaky(case, setup, ctx):
    marker = Path(ctx.workdir).parent / "attempts"
    tries = int(marker.read_text()) + 1 if marker.exists() else 1
    marker.write_text(str(tries))
    if tries < 2:
        raise TransientError("try again")
    return f"worked after {tries}"


def sleepy(case, setup, ctx):
    time.sleep(5)
    return "late"


def not_json(case, setup, ctx):
    return {1, 2}


@scorer("length")
def length(c):
    return min(1.0, len(str(c.data)) / 80)


@gate("not_empty")
def not_empty(c):
    return bool(str(c.data).strip())
'''

SCRIPT = """
import json, sys, pathlib
p = json.loads(sys.stdin.read())
size = int(p["setup"]["size"])
if p["setup"].get("crash"):
    print("boom", file=sys.stderr)
    sys.exit(3)
(pathlib.Path(p["workdir"]) / "frame.txt").write_text("x" * size)
print("some log line")
print(json.dumps({"data": {"size": size}, "measurements": {"pixels": size * size}}))
"""

CLIENT = """
from hone_select.testing import FakeTextClient


def text(model, **kwargs):
    reply = {"tiny": "a short reply", "big": '{"premise": "a long and careful reply"}'}[model]
    return FakeTextClient([reply] * 50, model=model)
"""

PYTHON_EXPERIMENT = """
title = "Writers"
question = "Which model and temperature write longest?"
samples = 2
registry = ["subjects"]
[generate]
kind = "python"
function = "subjects:write"
[factors]
model = ["small", "medium", "large"]
temperature = [0.0, 1.0]
[[baseline]]
name = "today"
model = "small"
temperature = 0.0
[criteria]
gates = ["not_empty"]
scorers = ["length"]
measure = { words = "higher" }
"""

CASES = '[[case]]\nid = "keeper"\ntopic = "the lighthouse keeper"\n[[case]]\nid = "ferry"\ntopic = "the ferryman"\n'


def project(tmp: Path, experiment: str = PYTHON_EXPERIMENT, cases: str = CASES) -> tuple[Project, Path]:
    """A project with subjects.py, scripts/render.py, fake_client.py and one experiment E0001."""
    (tmp / "subjects.py").write_text(textwrap.dedent(SUBJECTS))
    (tmp / "fake_client.py").write_text(textwrap.dedent(CLIENT))
    (tmp / "scripts").mkdir(exist_ok=True)
    (tmp / "scripts" / "render.py").write_text(textwrap.dedent(SCRIPT))
    if str(tmp) not in sys.path:
        sys.path.insert(0, str(tmp))  # the parent imports the registry module too
    p = Project(tmp)
    folder = p.new("Writers")
    (folder / "experiment.toml").write_text(textwrap.dedent(experiment))
    (folder / "cases" / "cases.toml").write_text(cases)
    return p, folder


def approved(tmp: Path, experiment: str = PYTHON_EXPERIMENT, cases: str = CASES) -> tuple[Project, Path]:
    p, folder = project(tmp, experiment, cases)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    return p, folder
