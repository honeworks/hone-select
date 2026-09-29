# Experiments
*Design: [change 0009](../design/changes/0009-experiments.md), run conditions [change 0010](../design/changes/0010-run-conditions.md). Example: [`experiments.py`](../examples/experiments.py).*

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
  run.json            the current or last run: running, waiting, stopped or completed; its waits
  outputs/<case>/<setup>/s<k>/result.json   data, measurements, error, log and environment of one sample
  outputs/<case>/<setup>/s<k>/outside-1.json  a first attempt that ran outside the run conditions
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
rejected by the implicit `ran_ok` gate and counted in its setup's error and pass rates. A configuration
mistake is different: a prompt `client` that cannot be built stops the run with a `ConfigError`.
Options of `[generate]`: `timeout`; `retries`, for transient failures only (a python or prompt subject
raises `hone_select.experiments.TransientError`, a command exits with code 75); `wrap` (for example
`["scripts/gpu-lock.sh"]`); `env`; `keep_files` (`all`, `small`, `none`). Python subjects and commands run
from the project root; python subjects run in their own process.

**Secrets:** name them in `env` with `$VAR` (`env = { API_KEY = "$OPENAI_API_KEY" }`): the subject gets the
value from your environment and the definition never contains it. Logs and errors are scrubbed of
anything that looks like a key, and the dashboard shows the definition with the values of secret-named
keys as `***`.

**Cost** is what a subject reports (`cost_usd` in a command's reply, a `Candidate`'s meta, or a prompt
client's usage). A sample that reports none has an unknown cost (`null`), never $0; money budgets count the
known costs.

### Cases

`cases/cases.toml` lists `[[case]]` entries (`id` and any fields); a folder `cases/<id>/` adds that case's
input files. `cases = { from = "E0001", keep = "winners" }` (or `"all"`) makes an earlier experiment's
outputs the cases: their data, files, source setup and source case.

## Run it

A small experiment that runs as written: a python subject, two factors, a code scorer.

```python
import json
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

## Run conditions

A busy machine corrupts results without saying so: a model left loaded by another program pushes the model
under test partly onto the CPU, a render next door slows every timing. `[conditions]` declares the state of
the machine an experiment needs; hone-select checks it before the first sample, between every two samples
and after the last one (about one second each), and records the machine's state with every sample.

```toml
[conditions]                  # optional; every key is optional, a key that is not set is not checked
max_cpu_load = 0.5            # share of all CPU cores busy, measured over one second between samples
min_free_ram_gb = 8           # available RAM (MemAvailable)
min_free_vram_gb = 7          # free memory on GPU 0, not counting the models this experiment needs
max_gpu_utilization_pct = 20  # GPU 0 busy between samples
only_needed_models = true     # unload every other model before a sample (needs `probe`)
models_on_gpu = true          # the needed models must be fully in VRAM, not partly on the CPU (needs `probe`)
models = ["{setup.model}"]    # the models a python or command subject needs (placeholders as in `command`)
gpu_lock = true               # hold the machine-wide GPU lock for the whole run ($HONE_GPU_LOCK or
                              # /tmp/honeworks-gpu.lock; or a path)
if_busy = "block"             # another process holds a GPU lease or the lock: block (unload nothing, wait)
                              # | unload (unload the unneeded models anyway)
warm_up = true                # load a needed model before its first sample, so the sample is not cold
on_violation = "wait"         # wait (up to wait_timeout, then stop) | stop | record_only
wait_timeout = 1800           # seconds one wait may last
probe = "hone_models:machine" # the model state, from hone-models (hone-select[models])
```

- **The needed models** are the setup's `model` for a prompt subject, and `models` (or none) for python and
  command subjects. With `only_needed_models`, the probe unloads every other model before each sample, so a
  change of model group needs no `after_group` hook.
- **When a condition does not hold** before a sample, the run waits (`run.json` shows `waiting` and the
  reasons; `status` prints the first one; `experiments stop` ends the wait), stops (`stop`), or runs anyway
  and marks the sample (`record_only`). A sample that ran outside the conditions is set aside as
  `outside-1.json` and run once more when they hold again; a second run outside is kept and marked.
- **A value that cannot be measured** (no `nvidia-smi`, no `/proc`, a probe that fails) is `unknown`, never
  `ok`: at start a declared check that cannot be measured refuses the run with what to do; later it counts
  like a violation.
- **The GPU lock** (`gpu_lock`) is taken once and held for every sample, wait and the scoring; other jobs
  that use `scripts/gpu-lock.sh` wait for the run. Subjects see `HONE_GPU_LOCK_HELD=1`. Do not combine it
  with `wrap = ["scripts/gpu-lock.sh"]`: the plan refuses that (the wrapper would wait for the run itself).
- **Each sample's `environment`** in `result.json` has the readings before and after it (CPU, RAM, GPU 0,
  loaded models, the lock), each condition's state, what the probe unloaded, whether it was `cold` (its
  time includes loading the model) and its attempt. Its `status` is `ok`, `outside`, `unknown`, or
  `not_checked` when the experiment declares no conditions.
- **The results** leave `outside` and `unknown` samples out of every number and out of each case's
  selection, count them per setup and factor level, and say so in the first line of `summary.md`. A cold
  sample counts, but its `seconds` do not. `hone-select experiments report E0001 --include-outside` counts
  them anyway (for a `record_only` experiment, or to see the effect).
- **The plan** shows the declared conditions, the models each setup needs and a reading of the machine now
  with what `start` would do; it unloads nothing. `plan --pilot` is refused on a busy machine. The conditions
  are part of the definition: changing one needs a new plan and approval.

The CPU and RAM readings come from `/proc` (Linux); GPU readings from the probe or `nvidia-smi`.

```python
conditioned = project.new("Story length, on a quiet machine")
(conditioned / "experiment.toml").write_text(
    (folder / "experiment.toml").read_text().replace('title = "Story length"', 'title = "Quiet stories"')
    + '[conditions]\nmin_free_ram_gb = 0.5\ngpu_lock = "gpu-demo.lock"\non_violation = "stop"\n'
)
(conditioned / "cases" / "cases.toml").write_text('[[case]]\nid = "keeper"\ntopic = "The keeper"\n')
plan = project.plan("E0002")
print(plan["conditions"]["now"]["would"])  # "start" on a machine with half a GB free
project.review("E0002", "approved", note="quiet machine only")
start(project, "E0002")
sample = next((conditioned / "outputs").glob("*/*/*/result.json"))
environment = json.loads(sample.read_text())["environment"]
assert environment["status"] == "ok"
assert environment["before"]["gpu_lock"] == "held by this run"
```

## Approving, rating and reading in the dashboard

`hone-select dashboard` (from the project folder, or `--project PATH`) adds an **Experiments** page:
the list with status and progress; one experiment with its plan (setups, outputs, estimate, the exact
commands), **Approve / Deny** with a note while it is proposed, its reviews, results tables per setup,
factor and baseline, and every sample with its output, files (images, audio and video play inline),
log and run conditions; a **Run conditions** card with the declared conditions, the plan's reading, the
last reading and the waits; a `waiting` badge with its reason; and **Rate** for each human criterion, one output at a time, blind to the setup. After rating, run
`hone-select experiments report E0001` to include the ratings in the results.

One run at a time: `start` refuses an experiment that is running or waiting (stop it first) or completed (run
`report` to recompute its results). A run that crashed or was killed shows as stopped, and `start` resumes it.

A plan is approved for one definition: editing `experiment.toml`, `cases/`, `prompts/` or `scripts/` makes
the experiment a draft again, and it needs a new plan and a new approval before it can start.
