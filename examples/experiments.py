"""Experiments: compare setups over test cases, with a plan, an approval and results.

What: an experiment that compares two "writers" (a python subject) and two lengths over two test cases,
scored by a code scorer, with a baseline; then which setup, which writer and which length win.

How: `Project(folder).new(title)` creates `experiments/E0001-.../`; write `experiment.toml` and the cases;
`project.plan("E0001")` expands every setup and writes the plan (status: proposed); a person approves it
(`project.review(..., "approved")`, the CLI `experiments approve` or the dashboard's Approve button);
`start(project, "E0001")` runs every sample, scores each case as a selection and writes `results/`.

Why: a comparison of models, parameters, prompts or programs is repeated work in every project; an
experiment makes it one declared file, shows the plan before anything expensive runs, resumes after a
stop, and keeps the evidence (outputs, scores, reasons, results) in the project. The subject can also be
a prompt to a model or any command (`[generate] kind = "prompt" | "command"`), see docs/experiments.md.
"""

import json
import sys
import tempfile
from pathlib import Path

from hone_select.experiments import Project, start

root = Path(tempfile.mkdtemp())
sys.path.insert(0, str(root))  # the registry module lives in the project

(root / "writers.py").write_text("""
from hone_select import scorer


def write(case, setup, ctx):
    filler = {"plain": "then", "vivid": "then the lantern guttered and"}[setup["writer"]]
    return f"{case['fields']['topic']}: " + (filler + " ") * setup["length"] + "the end."


@scorer("detail")
def detail(c):
    return min(1.0, len(c.data) / 200)
""")

project = Project(root)
folder = project.new("Writers and lengths")
(folder / "experiment.toml").write_text("""
title = "Writers and lengths"
question = "Which writer and length give the most detailed stories?"
registry = ["writers"]
[generate]
kind = "python"
function = "writers:write"
[factors]
writer = ["plain", "vivid"]
length = [2, 4]
[[baseline]]
name = "today"
writer = "plain"
length = 2
[criteria]
scorers = ["detail"]
""")
(folder / "cases" / "cases.toml").write_text(
    '[[case]]\nid = "keeper"\ntopic = "The keeper"\n[[case]]\nid = "ferry"\ntopic = "The ferryman"\n'
)

plan = project.plan("E0001")
state = project.status("E0001")["status"]
print(f"plan: {plan['outputs']} outputs over {len(plan['setups'])} setups; status {state}")
project.review("E0001", "approved", note="cheap, go", by="example")
status = start(project, "E0001")
print("status:", status["status"], f"{status['done']}/{status['outputs']}")

res = json.loads((folder / "results" / "results.json").read_text())
best = res["setups"][res["best"]]
print("best setup:", best["params"], "total", best["total"]["mean"])
for factor, levels in res["factors"].items():
    print(factor, {level: s["total"]["mean"] for level, s in levels.items()})

assert best["params"] == {"writer": "vivid", "length": 4}
assert res["factors"]["writer"]["vivid"]["total"]["mean"] > res["factors"]["writer"]["plain"]["total"]["mean"]
assert all(v["diff"] > 0 for v in res["baselines"]["today"].values())
