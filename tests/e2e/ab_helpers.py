"""An experiment with an A/B criterion (design change 0012 tests): three models, three cases, three samples;
the large model fails on case c2's second sample."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hone_select.experiments import Project, start
from hone_select.experiments import definition as d

from .experiment_helpers import approved

FAILING = """
from pathlib import Path

from subjects import write


def write_or_fail(case, setup, ctx):
    if case["id"] == "c2" and setup["model"] == "large" and Path(ctx.workdir).parent.name == "s1":
        raise ValueError("the large model is down")
    return write(case, setup, ctx)
"""

AB_EXPERIMENT = """
title = "Writers"
question = "Which model writes best?"
samples = 3
registry = ["subjects"]
[generate]
kind = "python"
function = "ab_subjects:write_or_fail"
[factors]
model = ["small", "medium", "large"]
[[baseline]]
name = "today"
model = "small"
[criteria]
scorers = ["length"]
[scorers.owner_pick]
kind = "ab"
question = "Which story would you rather read?"
{ab}
"""

AB_CASES = "".join(f'[[case]]\nid = "c{i}"\ntopic = "topic {i}"\n' for i in (1, 2, 3))
LARGE, MEDIUM, SMALL = (d.setup_id({"model": m}) for m in ("large", "medium", "small"))


def ab_run(tmp: Path, ab: str = 'between = "top"\ntop = 2\npairs = 6') -> tuple[Project, Path]:
    """A completed run of the A/B experiment."""
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "ab_subjects.py").write_text(FAILING)
    p, folder = approved(tmp, AB_EXPERIMENT.format(ab=ab), AB_CASES)
    start(p, "E0001")
    return p, folder


def ab_plan(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "ab_plan.json").read_text())


def result_of(folder: Path, sample_id: str) -> dict[str, Any]:
    case, setup, k = sample_id.split("__")
    return json.loads((folder / "outputs" / case / setup / k / "result.json").read_text())
