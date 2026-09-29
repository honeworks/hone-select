# Experiments
*Design: [change 0009](../design/changes/0009-experiments.md). Example: [`experiments.py`](../examples/experiments.py).*

An **experiment** compares setups over a fixed set of test cases and tells you which setup is best, and
separately which model, which parameter value and which prompt. The subject can be a prompt to a model,
your own Python function, or any program (Blender, an exporter, a fixer). hone-select creates the
experiment's folder, makes a plan with every run and an estimate, waits for a person to approve it, runs
it (resuming where it stopped), scores each test case as an ordinary selection, and writes the results.

```text
hone-select experiments new "Open-weight writing"    ->  experiments/E0001-open-weight-writing/
edit experiment.toml, cases/, prompts/ (or scripts/)
hone-select experiments plan E0001 [--pilot]          ->  plan.json            status: proposed
approve in the dashboard, or: experiments approve E0001  ->  review.json      status: approved
hone-select experiments start E0001                   ->  outputs/, results/   status: completed
hone-select dashboard                                 ->  the Experiments page: plan, decision, results
```

## The folder

```text
experiments/E0001-open-weight-writing/
  experiment.toml     the definition (what is tested, compared and measured)
  cases/              cases.toml ([[case]] id + fields) and / or one folder of input files per case
  prompts/            prompt files, when the subject is a prompt
  scripts/            scripts that belong to the experiment
  plan.json           every setup, the number of outputs, the estimate, the exact commands, the definition hash
  review.json         every approval and denial: who, when, the note, the definition hash
  run.json            the current or last run: running, stopped or completed
  outputs/<case>/<setup>/s<k>/result.json   data, measurements, error, log of one sample
  outputs/<case>/<setup>/s<k>/files/        the files it produced
  outputs/<case>/selection.json             the case's selection: every sample's scores, gates, the winner
  ratings.jsonl       ratings given in the dashboard (human criteria)
  results/results.json, results/summary.md
```

Commit it all: the experiment and its outputs are the evidence for a decision. hone-select writes no
`.gitignore`; ignore outputs in your project if you do not want them in git.

## The definition

```toml
title = "Open-weight writing"
question = "Which model, temperature and prompt write the best premises?"
cases = "cases/"
samples = 2                       # outputs per setup and case (seeds seed, seed + 1, ...)
registry = ["myproject.criteria"] # modules with @scorer / @gate functions

[generate]                        # the subject: kind = "prompt" | "python" | "command"
kind = "prompt"
client = "hone_models:text"       # "module:factory"; called with the setup's model
prompt = "{prompt}"               # the file in prompts/ named by the prompt factor (itself a template)
output = "json"

[factors]                         # what is compared; the subject receives each setup
model = ["gemma4-12b", "hemmingway-1"]
temperature = [0.7, 1.0]
prompt = ["plain.md", "guided.md"]

[design]
kind = "full"                     # every combination (default) | one_at_a_time | list ([[setup]] entries)

[[baseline]]                      # results show every setup's difference to it
name = "today"
model = "gemma4-12b"
temperature = 0.7
prompt = "plain.md"

[criteria]
gates = ["valid_json"]
scorers = ["creativity", "owner_like"]
measure = { seconds = "lower" }   # a measurement as a criterion
compare = [["creativity", "creativity_fable"]]   # report how much two scorers agree

[scorers.creativity]              # the scorers, judges and budgets of a selection.toml
kind = "prompt"
judge = "astra"
criteria = "The premise surprises and still makes sense."

[scorers.owner_like]              # rated by a person in the dashboard, blind to the setup
kind = "human"
question = "Would you watch this?"
scale = [1, 5]

[budget]
money_usd = 20                    # the run stops at the first limit (start it again to go on)

[run]
order = "model"                   # every sample of one model before the next (fewer model loads)
```

### The subject

| Kind | Declare | It receives | It returns |
|---|---|---|---|
| `prompt` | `client = "module:factory"`, `prompt`, `system`, `output`, `params` | the case fields and the setup, as template values | the model's text, or JSON (`output = "json"`) |
| `python` | `function = "module:function"` | `(case, setup, ctx)`; `ctx.workdir` is its own folder, `ctx.seed`, `ctx.case_files` | data, or a `Candidate` with `meta["measurements"]` |
| `command` | `command = [...]` with `{case.*}`, `{setup.*}`, `{setup_json}`, `{workdir}`, `{seed}`, `{experiment}`, `{root}` | the arguments, and the same as JSON on stdin | files written to `{workdir}`; optionally a last stdout line `{"data": ..., "measurements": {...}}` |

Every sample records its time, peak memory, exit code (commands), the files it produced and its log. A
failure (an exception, a non-zero exit, a timeout, output that is not JSON) is a result: the sample is
rejected by the implicit `ran_ok` gate and counted in its setup's error and pass rates. `timeout`,
`retries` (for `TransientError`, exit code 75, or any prompt-client error), `wrap` (for example
`["scripts/gpu-lock.sh"]`), `env` and `keep_files` (`all`, `small`, `none`) are options of `[generate]`.
Python subjects and commands run from the project root; python subjects run in their own process.

### Cases

`cases/cases.toml` lists `[[case]]` entries (`id` and any fields); a folder `cases/<id>/` adds that case's
input files. `cases = { from = "E0001", keep = "winners" }` (or `"all"`) makes an earlier experiment's
outputs the cases: their data, files, source setup and source case.

## Run it

A small experiment that runs as written: a python subject, two factors, a code scorer.

```python
from pathlib import Path

from hone_select.experiments import Project, start

Path("stories.py").write_text("""
from hone_select import scorer


def write(case, setup, ctx):
    return f"{case['fields']['topic']}, " + "and then " * setup["length"] + "the end."


@scorer("detail")
def detail(c):
    return min(1.0, len(c.data) / 120)
""")

project = Project(".")
folder = project.new("Story length")
(folder / "experiment.toml").write_text("""
title = "Story length"
question = "Which length writes the most detailed stories?"
registry = ["stories"]
[generate]
kind = "python"
function = "stories:write"
[factors]
length = [1, 3, 6]
[[baseline]]
name = "short"
length = 1
[criteria]
scorers = ["detail"]
""")
(folder / "cases" / "cases.toml").write_text('[[case]]\nid = "keeper"\ntopic = "The keeper"\n')

plan = project.plan("E0001")
assert plan["outputs"] == 3
project.review("E0001", "approved", note="small and cheap")  # or Approve in the dashboard
status = start(project, "E0001")
assert status["status"] == "completed"

results = (folder / "results" / "summary.md").read_text()
assert "**Best setup:**" in results
print(results.splitlines()[4])  # the best setup and its params
```

## Results

`results/results.json` (and the same as `summary.md`) holds, per **setup**, per **factor level** (for
example every `model`, averaged over the other factors) and against each **baseline**:

- `total`: the mean total with a 95 % interval (per case first, then over cases; seeded bootstrap);
- `pass_rate`, `errors`, `wins` (cases where it had the winning sample), mean per criterion, mean per
  measurement (seconds, peak memory, output bytes, your own), money;
- `human`: the mean rating per human criterion, how many outputs are rated, and whether `min_ratings` is
  reached;
- against a baseline: the difference in total with its interval, and `clear` when the interval excludes 0;
- `agreement`: the mean difference between scorer pairs listed in `compare`.

Each case's selection is also in the span store with `hone.run_id = <EID>` and `hone.item = <case>`, so the
dashboard's Runs page and hone-lens show every candidate, score and reason.

## Approving, rating and reading in the dashboard

`hone-select dashboard` (from the project folder, or `--project PATH`) adds an **Experiments** page:
the list with status and progress; one experiment with its plan (setups, outputs, estimate, the exact
commands), **Approve / Deny** with a note while it is proposed, its reviews, results tables per setup,
factor and baseline, and every sample with its output, files (images, audio and video play inline) and
log; and **Rate** for each human criterion, one output at a time, blind to the setup. After rating, run
`hone-select experiments report E0001` to include the ratings in the results.

A plan is approved for one definition: editing `experiment.toml`, `cases/`, `prompts/` or `scripts/` makes
the experiment a draft again, and it needs a new plan and a new approval before it can start.
