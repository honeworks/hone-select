"""Generation experiments: compare image, music or video models, each asked its own way, on what they can do.

What: an experiment that compares two song models with a `generate` subject: one model gets its own
prompt (`[generate.per_model]`), one case needs a 60-second song that only one model can make, and the guides
say what each model can take. The plan lists the cell that is not run; the results compare the models on
the cases both can do and mark the setup that was asked differently.

How: `[generate] kind = "generate"` with `client = "<module>:<factory>"` (in a real project
`client = "hone_models:music"`, extra `models`); `guides = "<module>:<factory>"` (by default
`hone_models:guides`); a case's `needs = ["duration_s >= 60"]`; `project.plan(...)` checks every cell against
the guides; `start(...)` runs the applicable cells, each through `client(model).generate(...)`.

Why: generation models are steered differently and some cannot do what a case asks; one prompt for every
model measures the prompt's fit, and a case a model cannot do counted as a failure makes it look worse at
everything it can do. The plan shows both before anything runs.
"""

import json
import sys
import tempfile
from pathlib import Path

from hone_select.experiments import Project, start

root = Path(tempfile.mkdtemp())
sys.path.insert(0, str(root))  # the fake client, the guides and the scorer live in the project

(root / "songs.py").write_text("""
from dataclasses import dataclass, field

from hone_select import scorer
from hone_select.testing import FakeModelGuides


@dataclass
class Result:  # what hone-select reads of hone-models' MediaResult
    files: list = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None
    elapsed_s: float = 2.0
    cost_usd: float | None = None
    license: str | None = "Apache-2.0"
    commercial_use: bool | None = True


class Singer:  # stands in for mk.music(model)
    def __init__(self, model):
        self.model = model

    def generate(self, prompt, *, out, seed=None, timeout_s=None, trace=None, **inputs):
        out.write_text(f"{self.model} sings '{prompt}' for {inputs['duration_s']} s")
        free = self.model == "tags-model"  # the license comes from the model's registry entry
        return Result(files=[out], license="Apache-2.0" if free else "CC-BY-NC-4.0", commercial_use=free)


def music(model):
    return Singer(model)


def guides():  # stands in for hone_models:guides
    return FakeModelGuides({
        "tags-model": {"prompt": "comma-separated tags", "max_duration_s": 600},
        "short-model": {"prompt": "one sentence", "max_duration_s": 47, "commercial_use": False},
    })


@scorer("tagged")
def tagged(c):
    return 0.9 if "," in open(c.files["take.txt"]).read() else 0.6
""")

project = Project(root)
folder = project.new("Song models")
(folder / "experiment.toml").write_text("""
title = "Song models"
question = "Which song model, each asked its own way?"
registry = ["songs"]
[generate]
kind = "generate"
client = "songs:music"
guides = "songs:guides"
prompt = "{style} song about {topic}"
output = "take.txt"
inputs = { duration_s = "{case.seconds}" }
[generate.per_model."tags-model"]
prompt = "{style}, {topic}, female vocals"
[factors]
model = ["tags-model", "short-model"]
[[baseline]]
name = "today"
model = "short-model"
[criteria]
scorers = ["tagged"]
""")
(folder / "cases" / "cases.toml").write_text("""
[[case]]
id = "jingle"
style = "upbeat pop"
topic = "a ferry"
seconds = 30
[[case]]
id = "ballad"
style = "slow ballad"
topic = "a lighthouse"
seconds = 90
needs = ["duration_s >= 60"]
""")

plan = project.plan("E0001")
for cell in plan["applicability"]["not_applicable"]:
    print("not run:", cell["case"], cell["unmet"])
project.review("E0001", "approved", note="go", by="example")
start(project, "E0001")

res = json.loads((folder / "results" / "results.json").read_text())
for sid in res["ranking"]:
    s = res["setups"][sid]
    asked = " (asked differently)" if s["asked_differently"] else ""
    print(f"{s['params']['model']}{asked}: total {s['total']['mean']}, {s['applicable']['cases']} of 2 cases")
tags = next(sid for sid, s in res["setups"].items() if s["params"]["model"] == "tags-model")
print("against the baseline, on shared cases:", res["baselines"]["today"][tags])
print(res["could_not"], res["models"]["short-model"])

assert plan["applicability"]["not_applicable"][0]["unmet"] == [
    "short-model: duration_s >= 60 (max_duration_s 47)"
]
assert res["setups"][tags]["asked_differently"] and res["best"] == tags
assert res["baselines"]["today"][tags]["shared_cases"] == 1
assert res["models"]["short-model"]["commercial_use"] is False
