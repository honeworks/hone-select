# Experiments
*Design: [change 0009](../design/changes/0009-experiments.md), run conditions [change 0010](../design/changes/0010-run-conditions.md), model-aware experiments [change 0011](../design/changes/0011-model-aware-experiments.md). Examples: [`experiments.py`](../examples/experiments.py), [`generation_experiment.py`](../examples/generation_experiment.py).*

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
| `generate` | `client = "hone_models:image"` (`:music`, `:video`, or any `module:factory`), `prompt`, `inputs`, `output` | `generate(prompt, out=<workdir>/<output>, seed=, timeout_s=, trace=, **inputs)` | the result's files, `elapsed_s`, `cost_usd`, `error` and `error_kind` (see [Generation experiments](#generation-experiments-per-model-asks-and-needs)) |

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
- `ab`: per A/B criterion and pair of setups, the picks and the win rate (see
  [A/B](#ab-pick-the-better-of-two-blind));
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

## Generation experiments, per-model asks and needs

Image, music and video models are compared with the same plan, approval, run and results as text models,
and each model is asked in the form it is best asked and only for what it can do.

```toml
[generate]
kind = "generate"
client = "hone_models:music"          # hone_models:image | :music | :video (extra `models`), or "module:factory"
prompt = "{prompt}"                   # the factor `prompt` picks a file from prompts/, as for prompt subjects
inputs = { lyrics = "{case.lyrics}", duration_s = "{setup.duration_s}", references = ["{case.files[front.png]}"] }
output = "take.flac"                  # the file name inside the sample's folder
timeout = 1800
# guides = "hone_models:guides"       # where the model guides come from (the default for hone_models clients)

[generate.per_model."ace-step-1.5-xl-turbo"]   # this model, in every case
prompt = "{tags}"                              # its own prompt (a case field `tags`)
inputs = { key = "{case.key}" }                # merged over [generate] inputs

[factors]
model = ["ace-step-1.5-xl-turbo", "songgeneration-v2-medium", "heartmula-3b"]
prompt = ["short.md", "detailed.md"]
duration_s = [60, 150]
```

- **The call.** Each sample calls `client(model).generate(prompt, out=..., seed=<the sample's seed>,
  timeout_s=<timeout>, trace=..., **inputs)`. Placeholders are filled as in `command`; an input that is one
  placeholder keeps its type (`duration_s` stays a number), and a value that names a case file becomes a
  `Path`, so hone-models sends it as a file. The result's files are the output (with hashes, sizes,
  dimensions, durations); `elapsed_s` and `cost_usd` are its measurements (`cost_estimated` says the cost is
  hone-models' flat-price estimate); `license` and `commercial_use` go into the candidate's meta.
- **Failures.** A `result.error` is a failed sample with its `error_kind` (`out_of_memory`, `refused`,
  `invalid_input`, `no_output`, `failed`). With `[conditions]` declared, `out_of_memory` means the machine
  was short: the sample is `outside` and runs once more, as any sample outside the conditions.
- **Sessions.** A client with `session()` keeps its model loaded while samples of the same model follow each
  other (`run.order = "model"`, the default) and frees it when the model changes and at the end.
- **How each model is asked.** A case can ask one model differently: a `[case.per_model."<model>"]` table
  replaces fields of that case for that model. A prompt file in `prompts/<model>/<file>` is used for that
  model instead of `prompts/<file>`. The plan's `asked` shows, per setup, the resolved prompt and inputs, the
  overrides and the cases asked differently; such setups are marked **asked differently** in the plan, the
  results and the dashboard, so nobody reads them as "same prompt, different model". An override that
  names no factor or case field, or a model the experiment does not run, is refused by `plan`. Per-model
  overrides work for `prompt` subjects too.
- **Judges stay blind.** Judges and scorers see the output and the case's shared fields, never an
  override, a setup or a guide; `judge_view = ["scene"]` in a case limits them to those fields. A generation
  output's data is `{"case": <what judges see>, "files": [...]}`.

**Needs.** A case may say what it needs: `needs = ["camera angle"]` (a feature of a model's guide),
`["references"]` (an input), `["duration_s >= 60"]` or `["references >= 3"]` (limits). The factors
`duration_s` and `size` add a need per value (`duration_s = 150`). `plan` checks every cell against the
model's guide:

- a model whose guide does not list the feature or input, or whose limits exclude the value, cannot do the
  cell: it is **not applicable**, not run, listed in the plan with the unmet need (`heartmula-3b: no feature
  'camera angle'`), and neither a failure nor a zero in the results;
- a limit the guide does not declare is `need_unknown`: the cell runs and its samples say so; without any
  guide source every need is `need_unknown` and nothing is skipped;
- each setup's numbers are over its applicable cases, with the count (`2 of 3; 1 not applicable`); the
  ranking uses each setup's own mean; baseline differences, wins and losses, and factor levels use only the
  cases both sides (every level) can do, and say how many; `summary.md` lists what each model could not
  do.

**Model guides.** With `client = "hone_models:*"` (or `guides = "<name>"`: a `hone.model_guides` entry
point or a `module:factory`), `plan` reads each model's guide once and stores it in `plan.json` `models`: its
summary, prompt advice, inputs, features with examples and source, limits, license, `commercial_use`,
whether it is installed and the install command. The run uses the stored guides. A model reported not
installed is listed in the plan with `hone-models models install <id>`, and `start` refuses until it is
installed (or removed from the factors). A prompt subject can use `{model_guide}` (the guide as text) and a
python subject `ctx.model_guide` (a dict, or `None`): the guide of the setup's `target_model`, else of its
`model`, so an experiment can test which chat model writes the best prompt for an image model. The results
carry each model's `license` and `commercial_use`; `summary.md` marks non-commercial models and never
removes them. Without hone-select[models], `guides = "hone_models:guides"` is a `ConfigError` naming the
extra.

```python
Path("studio.py").write_text("""
from dataclasses import dataclass, field

from hone_select import scorer
from hone_select.testing import FakeModelGuides


@dataclass
class Result:  # what hone-select reads of hone-models' MediaResult
    files: list = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None
    elapsed_s: float = 1.0
    cost_usd: float | None = None
    license: str = "Apache-2.0"
    commercial_use: bool = True


class Painter:  # stands in for mk.image(model)
    def __init__(self, model):
        self.model = model

    def generate(self, prompt, *, out, seed=None, timeout_s=None, trace=None, **inputs):
        out.write_text(f"{self.model}: {prompt} {inputs}")
        return Result(files=[out])


def image(model):
    return Painter(model)


def guides():  # stands in for hone_models:guides
    return FakeModelGuides({
        "angles-model": {"features": [{"name": "camera angle", "how": "the camera_angle input"}]},
        "plain-model": {"features": []},
    })


@scorer("follows")
def follows(c):
    return 0.9 if "camera_angle" in open(c.files["shot.txt"]).read() else 0.6
""")

angles = project.new("Camera angles")
(angles / "experiment.toml").write_text("""
title = "Camera angles"
question = "Which image model follows a camera angle?"
registry = ["studio"]
[generate]
kind = "generate"
client = "studio:image"
guides = "studio:guides"
prompt = "{scene}"
output = "shot.txt"
[generate.per_model."angles-model"]
inputs = { camera_angle = "{case.angle}" }
[factors]
model = ["angles-model", "plain-model"]
[criteria]
scorers = ["follows"]
""")
(angles / "cases" / "cases.toml").write_text("""
[[case]]
id = "rooftop"
scene = "a girl on a rooftop at night"
angle = "low_angle"
needs = ["camera angle"]
[[case]]
id = "harbour"
scene = "a harbour"
angle = "top_down"
[case.per_model."plain-model"]
scene = "a harbour, seen from above"
""")
plan = project.plan("E0003")
print(plan["applicability"]["not_applicable"][0]["unmet"])  # ["plain-model: no feature 'camera angle'"]
project.review("E0003", "approved", note="go")
start(project, "E0003")
res = json.loads((angles / "results" / "results.json").read_text())
plain = next(s for s in res["setups"].values() if s["params"]["model"] == "plain-model")
assert plain["applicable"]["cases"] == 1 and plain["asked_differently"]
assert "`plain-model` could not do: camera angle (1 of 2 cases)" in res["could_not"]
```

## Approving, rating and reading in the dashboard

`hone-select dashboard` (from the project folder, or `--project PATH`) adds an **Experiments** page:
the list with status and progress; one experiment with its plan (setups, outputs, estimate, the exact
commands), **Approve / Deny** with a note while it is proposed, its reviews, results tables per setup,
factor and baseline, and every sample with its output, files (images, audio and video play inline),
log and run conditions; a **Run conditions** card with the declared conditions, the plan's reading, the
last reading and the waits; a **Models** card (in Plan and Definition) with each model's guide, whether
it is installed, how each setup's model is asked and the cells not run; a `waiting` badge with its reason; **Rate** for each human criterion, one output at a time, blind to the setup; and **A/B** for each
`ab` criterion, two outputs side by side, blind (see [A/B](#ab-pick-the-better-of-two-blind)). After rating
or picking, run `hone-select experiments report E0001` to include the ratings and picks in the results.

One run at a time: `start` refuses an experiment that is running or waiting (stop it first) or completed (run
`report` to recompute its results). A run that crashed or was killed shows as stopped, and `start` resumes it.

A plan is approved for one definition: editing `experiment.toml`, `cases/`, `prompts/` or `scripts/` makes
the experiment a draft again, and it needs a new plan and a new approval before it can start.

## A/B: pick the better of two, blind

A rating says how good one output is; an A/B pick says which of two is better, which is faster to decide and
more reliable when two setups are close. An `ab` criterion shows a person two outputs for the same case, one
from each setup, without saying which is which, and counts the picks.

```toml
[scorers.owner_pick]
kind = "ab"
question = "Which premise would you rather see as a video?"
between = "top"        # "top": the best setups by the automatic total (default)
                       # or a list of setup ids, baseline names or [[setup]] names: ["hemmingway-1-1.0-3f2a1c", "today"]
                       # or "baseline": every setup against the first baseline
top = 2                # with between = "top": how many setups (2 = one pair; 3 = three pairs)
pairs = 20             # pairs to judge per pair of setups (default 20)
allow_tie = true       # default true
```

An `ab` criterion is not part of the automatic total, and it need not be listed in `[criteria] scorers`.
Unknown names in `between` fail at `plan` time.

- **Pairs.** A pair is the same case from both setups, the sample with the same index when both have it;
  failed samples and samples outside the run conditions are never shown. The pairs are spread over the cases
  as evenly as possible (8 cases and 20 pairs: 2 or 3 per case), and which setup is on the left is random
  per pair, seeded by the experiment's `seed`. They are drawn once, when the run is done and scored (the
  best setups are known then), and fixed in `ab_plan.json`, so stopping and coming back continues the same
  list. When fewer pairs exist than `pairs`, all of them are used.
- **The screen.** The Overview has an **A/B** button next to **Rate** for each `ab` criterion: the question,
  the two outputs side by side (text, JSON, images, audio and video), **Left**, **Tie** (when allowed) and
  **Right**, also on the keys ←, T and →, the progress and an undo of the last pick. Nothing on it names a
  model, a setup or a prompt. Before the run is done it says "A/B: waiting for the run".
- **Storage.** `ab.jsonl`, one line per pick: `{"criterion", "pair": [setup_a, setup_b], "case", "left",
  "right", "choice": "left" | "right" | "tie", "at"}` (`left` and `right` are sample ids); an undo appends
  `{"undo": <line index>}`.
- **Results.** `hone-select experiments report` adds `ab` to `results.json`: per criterion and pair of
  setups (the one with more wins first), `sides` with the wins, losses and ties of each, the `win_rate`
  without ties with a 95 % Wilson interval (`low`, `high`), `clear` when the interval excludes 50 %,
  `judged`, `planned`, `requested`, `complete` when every planned pair has a pick, and the winner per case
  (`cases`). `summary.md` gets a line per pair:

```text
A/B (owner_pick): hemmingway-1-1.0-3f2a1c beats styletune-31b-1.0-9c0d4e, 14-5 with 1 tie, win rate 74 % (51-88 %), clear.
```

The same picks from Python, as the dashboard makes them:

```python
from hone_select.experiments import ab, report
from hone_select.experiments import definition

pick = project.new("Story length, A/B")
definition_text = (folder / "experiment.toml").read_text()
(pick / "experiment.toml").write_text(
    definition_text.replace('title = "Story length"', 'title = "Pick a length"\nsamples = 2')
    + '[scorers.owner_pick]\nkind = "ab"\nquestion = "Which story is better?"\npairs = 4\n'
)
(pick / "cases" / "cases.toml").write_text(
    '[[case]]\nid = "keeper"\ntopic = "The keeper"\n[[case]]\nid = "ferry"\ntopic = "The ferryman"\n'
)
eid = pick.name.split("-")[0]
project.plan(eid)
project.review(eid, "approved", note="four pairs")
start(project, eid)

spec = definition.load(pick)
while (shown := ab.next_pair(pick, spec, "owner_pick"))["state"] == "pair":
    longer = "left" if len(str(shown["left"]["data"])) >= len(str(shown["right"]["data"])) else "right"
    ab.add(pick, spec, "owner_pick", shown["index"], longer)  # the person prefers the longer story
res = report(pick, spec, json.loads((pick / "plan.json").read_text()))
[pair] = res["ab"]["owner_pick"]["pairs"]
assert pair["complete"] and pair["sides"][pair["setups"][0]]["wins"] == 4
print([line for line in (pick / "results" / "summary.md").read_text().splitlines() if "A/B (" in line][0])
```
