# 0011: Model-aware experiments: generation subjects, per-model prompts and cases a model cannot do

## Status

`implemented in 0.1.0` (accepted 2026-09-29, owner: "start implementing"; open questions take the proposed answers; implementation choices in design/decisions.md D-020 to D-026). Builds on [0009](0009-experiments.md) (experiments) and uses hone-models change
[0015 generation models](https://github.com/honeworks/hone-models/blob/main/design/changes/0015-generation-models.md):
its image, music and video clients, and its model guides. Written next to [0010](0010-run-conditions.md),
which takes AC-30 to AC-35; this record takes AC-36 to AC-39.

## Context

Experiments compare setups (model, parameters, prompt) over test cases. 0009 treats every model in a
`model` factor the same way: one prompt template, the same inputs, every case run on every model. That
fits chat models that all read plain text. It does not fit generation models, and it is not always fair
for chat models either:

- models are steered differently: ACE-Step wants comma-separated tags, SongGeneration wants a sentence;
  one image model changes the camera angle through fixed phrases of an add-on, another through a plain
  instruction, a third cannot do it at all;
- some cases ask for something only some models can do (a camera angle, editing with three references,
  a 60-second song on a model limited to 47 seconds);
- hone-models 0015 now records what each model can take (its inputs, its features with samples, its
  limits, its license) in the registry, as a `ModelGuide`, and calls image, music and video models through
  `mk.image`, `mk.music` and `mk.video`.

The owner: "hone-select should take that in mind, should understand that a model may be actually tested
differently compared to another model for a specific case."

## Problem

1. There is no generation subject. An image or music experiment needs a `python` subject that calls
   hone-models by hand: the seed, the output file, the failure handling and the records are each
   experiment's own code.
2. One prompt for every model handicaps the models that want another form, so the experiment measures
   the prompt's fit, not the model.
3. A case a model cannot do is run anyway and counted as a failure, which makes that model look worse at
   everything it *can* do.
4. The plan does not show what each model can take, so the person approving it cannot see whether the
   setups are fair.

## Options

1. **Keep one prompt; leave differences to `python` subjects.** Nothing new, but every image or music
   experiment writes the same glue, and "not applicable" stays a failure.
2. **Per-model overrides and declared needs in the definition, a generation subject, and the guides shown
   in the plan (proposed).** The definition says how each model is asked and what each case needs; the
   plan shows the result before approval; the results compare models on what they can do.
3. **Let an LLM rewrite the prompt per model from the guide, automatically.** Attractive, but it hides a
   second model inside every setup and makes results depend on it. It stays possible as an explicit
   step (a `python` subject, or a prompt subject that writes prompts, §4), not as a default.

## Decision

Option 2.

### 1. The generation subject

```toml
[generate]
kind = "generate"
client = "hone_models:music"                  # hone_models:image | :music | :video (extra `models`)
prompt = "{prompt}"                           # the factor `prompt` picks a file from prompts/, as today
inputs = { lyrics = "{case.lyrics}", duration_s = "{setup.duration_s}" }
output = "take.flac"                          # the file name inside the sample's workdir
timeout = 1800

[factors]
model = ["ace-step-1.5-xl-turbo", "songgeneration-v2-medium", "heartmula-3b"]
prompt = ["short.md", "detailed.md"]
duration_s = [60, 150]
```

- Each sample calls `client(model).generate(prompt, out=<workdir>/<output>, seed=<the sample's seed>,
  trace=<the run's trace>, **inputs)`. Placeholders are filled as in `command` (`{case.*}`, `{setup.*}`,
  `{workdir}`, `{seed}`); a value that names a case file becomes a `Path`, so hone-models treats it as a
  file input.
- The candidate is the result's files (with hashes, sizes, dimensions or durations), and its
  measurements are the result's `elapsed_s` and `cost_usd` (marked estimated when hone-models says so).
  `license` and `commercial_use` go into the candidate's meta.
- A `result.error` is a failed sample, as a failure is today, with `error_kind` kept (`out_of_memory`,
  `refused`, `invalid_input`, `no_output`, `failed`). `out_of_memory` is also a run-conditions signal:
  with 0010's conditions it marks the sample `outside` (the machine was short), so it is run again once
  instead of counting against the model.
- The generate subject runs the model's own session for a group of samples (`run.order = "by_model"`),
  so ComfyUI keeps the model between samples of one model and frees it at the group change (hone-models
  0015 §2; a session is also what starts ComfyUI when it is not running).
- hone-select's core still imports nothing from hone-models: `client` is resolved through the entry-point
  groups hone-models 0015 registers (`hone.image_clients`, `hone.music_clients`, `hone.video_clients`),
  as `hone_models:text` is today.

### 2. How each model is asked: per-model overrides

Two levels, both optional, both part of the definition (so part of the hash and the approval):

```toml
[generate.per_model."ace-step-1.5-xl-turbo"]        # for this model, in every case
prompt = "{prompt_tags}"                            # e.g. prompts/short.tags.md: the tag form of the same prompt
inputs = { key = "{case.key}" }                     # merged over [generate] inputs

[generate.per_model."qwen-image-edit-2511"]
inputs = { camera_angle = "{case.angle}" }          # hone-models turns it into the LoRA's phrase
```

```toml
# cases/c07-rooftop.toml: one case, asked differently of one model
id = "c07-rooftop"
scene = "a girl on a rooftop at night, seen from a low angle"
angle = "low_angle"
needs = ["camera angle"]
[per_model."gpt-image-1.5"]
scene = "a girl on a rooftop at night. Camera: low angle, looking up at her against the sky."
```

- Resolution for one sample: the case's `per_model[model]` fields over the case's fields, then
  `[generate.per_model.<model>]` over `[generate]`. Prompt files are looked up as
  `prompts/<model>/<file>` first, then `prompts/<file>`, so a model-specific version of `guided.md` sits
  next to the shared one.
- Per-model overrides make the comparison one of **model with its best-known way of asking**, which is the
  owner's intent. The plan and the results say so wherever an override applied: the setup is marked
  "asked differently" with the override shown, so nobody reads it as "same prompt, different model".
- Other factors (`temperature`, `steps`, `duration_s`) stay shared. A factor value a model cannot take
  (a duration over its `max_duration_s`, a size it does not list) is a need that is not met (§3), found
  at plan time.

### 3. Cases a model cannot do: `needs` and "not applicable"

- A case may declare `needs`: feature names from the models' guides (`"camera angle"`), input names
  (`"references"`, `"lyrics"`), or limits (`"duration_s >= 60"`, `"references >= 3"`). A factor value can
  create a need too (`duration_s = 150` needs `max_duration_s >= 150`).
- `plan` checks every cell against the model's guide through hone-models (§5). A cell whose model does not
  meet a need is **not applicable**: it is not run, it is listed in the plan with the unmet need
  ("heartmula-3b: no feature 'camera angle'"), and it is neither a failure nor a zero in the results.
- A model that does not *declare* a feature does not meet a need for it (the guide lists features
  positively). A model whose limit is unknown (`max_duration_s` not declared) is run and marked
  `need_unknown`, because unknown is not false; open question 1 asks whether to skip those instead.
- Results compare on what is comparable:
  - each setup's numbers are over its applicable cases, with the count of not-applicable cases next to
    them (`12 of 15 cases; 3 not applicable: camera angle`);
  - baseline deltas, wins and factor-level comparisons use only the cases both sides can do, and say how
    many that is;
  - `summary.md` gains a "what each model could not do" line per model.
- A `python` or `command` subject can declare the same `needs`; with no guide source (§5) every need is
  `need_unknown` and nothing is skipped.

### 4. The guides in the plan, the dashboard and the subjects

- `plan.json` gains `models`: per model in the experiment, its guide from hone-models (summary, prompt
  advice, accepted inputs, features with examples and source, limits, license, `commercial_use`), and
  per setup the resolved prompt file and inputs after overrides, and whether the model is installed
  (hone-models 0015's catalog). An experiment may name models that are not installed yet: the plan says
  which, with the `hone-models models install <id>` command for each, and `start` refuses until they
  are installed (or the person removes them from the factors), so a missing model never shows up as a
  column of failures. The dashboard's Plan and Definition tabs
  show them next to the setups, so the person approving can see how each model is asked and why some
  cells are not applicable.
- The prompt subject gets `{model_guide}` (the guide as text) as a placeholder, so an experiment can
  test prompt-writing: a chat model writes the image prompt for a target model named in the setup,
  given that model's guide. That is the explicit version of option 3.
- `python` subjects get `ctx.model_guide` (a plain dict, or `None` when unavailable) for the same use.
- Judges stay blind (0009): they see the case's fields and the output, never the setup, the per-model
  overrides or the guide. A case may give judges its own view with `judge_view = ["scene"]`; without it,
  judges see the case's shared fields, not a model's override.
- Results carry `license` and `commercial_use` per model; the summary marks non-commercial models. It
  never filters them out (owner: it must not block); a person or a later selection decides.

### 5. Where the guides come from: a second optional port

```python
class ModelGuides(Protocol):
    """What a model can take (design change 0011), provided by hone-models 0015; any object fits."""

    def guide(self, model_id: str) -> Mapping[str, Any] | None: ...
    # hone-models' ModelGuide as JSON: {"id", "kind", "summary", "prompt", "inputs", "features",
    #  "source", "checked", "license", "commercial_use", "sizes", "durations_s", "max_duration_s",
    #  "max_references", "installed": "yes" | "no" | "unknown", "install": "<command to run>"};
    #  None for a model the source does not know
```

- `[generate] guides = "hone_models:guides"` names it; it is the default when `client` is a `hone_models:*`
  factory. Resolution, entry-point group (`hone.model_guides`) and the lazy adapter follow 0010's
  `MachineProbe` exactly; without `hone-select[models]` a declared `guides` is a `ConfigError` naming the
  extra, and an undeclared one leaves every need `need_unknown`.
- The guide of each model is read once at `plan` time and stored in `plan.json`; the run uses the stored
  guide, so a registry edit between approval and start cannot change which cells run (a new plan is
  needed, as for any definition change).
- `hone_select.testing` gains `FakeModelGuides` and `check_model_guides` (the contract checker).

### 6. Acceptance cases

| AC | Scenario | Expected |
|---|---|---|
| AC-36 | A generate subject with a fake music client (the `hone_models.testing` fakes through the entry point): two models, a prompt factor, inputs from the case and the setup, a case file input; one fake result with `error_kind = "out_of_memory"` and another with `refused` | each sample calls `generate` with the sample's seed, `out` in the workdir and the filled inputs (the file as a `Path`); the candidate has the files and measurements; `refused` is a failed sample with its kind; `out_of_memory` with conditions is `outside` and run again once; one session per model group |
| AC-37 | Per-model overrides: `[generate.per_model]`, a case's `per_model`, `prompts/<model>/guided.md` next to `prompts/guided.md` | each model receives its own prompt and inputs, others the shared ones; `plan.json` shows the resolved prompt and inputs per setup and marks setups "asked differently"; judges never see an override; an override that names no factor or case field is a `ConfigError` at plan time |
| AC-38 | Needs: a case needing "camera angle", one needing `duration_s >= 60`, a factor value over a model's `max_duration_s`, a model with an undeclared limit | cells whose model lacks the feature or the limit are not applicable, listed with the unmet need, not run, not failures; the undeclared limit is run and marked `need_unknown`; per-setup numbers are over applicable cases with the count; baseline deltas and wins use only cases both sides can do and say how many |
| AC-39 | Guides: `FakeModelGuides` in the plan and the dashboard, `{model_guide}` in a prompt subject, `ctx.model_guide` in a python subject; no `hone-select[models]` | `plan.json` stores each model's guide and the dashboard shows it; the placeholder and `ctx` get the stored guide; license and `commercial_use` appear in the results and a non-commercial model is marked, not removed; a model the guide reports as not installed is listed in the plan with its install command and `start` refuses until it is installed; without the extra, a declared `guides` is a `ConfigError` naming it and needs are `need_unknown`; `FakeModelGuides` passes `check_model_guides` |

### 7. Tests

Offline, with the fakes hone-models 0015 ships (`FakeMedia.like`, reached through the entry points) and
`FakeModelGuides` with scripted guides (a model with the camera-angle feature, one without, one with no
limits declared). The dashboard parts are checked through `/api/experiments/<eid>` as in AC-35.

## Consequences

- Image, music and video experiments are definitions, not glue code: the same plan, approval, run,
  results and ratings as text experiments, with every call recorded by hone-models.
- A model is compared on what it can do and in the form it is best asked, and the plan makes that visible
  before approval. Results say "asked differently" wherever that applied, so they are read correctly.
- Not-applicable cells shrink the shared ground between models; with many needs, two models may share few
  cases. The results show the count on every comparison rather than hide it.
- hone-select gains a subject kind (about 120 lines), override resolution and needs (about 150), the
  `ModelGuides` port with fake and checker, and plan, results and dashboard additions: roughly 450 lines.
- Guides are documentation kept by hand in hone-models; a stale guide can mark a cell not applicable that
  the model could do. The plan shows each guide's `checked` date and source.

## Migration and compatibility

Additive. Definitions without `kind = "generate"`, `per_model` or `needs` behave as today; old plans have
no `models` section and are read as before. `hone_select.ports` gains `ModelGuides` (`PORTS_VERSION` stays
`"1"`), `hone_select.testing` gains `FakeModelGuides` and `check_model_guides`, `pyproject.toml` gains the
entry-point group `hone.model_guides` with `hone_models:guides`. The `models` extra needs the hone-models
version that ships 0015.

## Open questions for the owner

1. **Undeclared limits.** A model that declares no `max_duration_s` is run for a 150-second case and marked
   `need_unknown` (proposed: unknown is not false). Or skip it as not applicable until its guide says?
2. **Case-level overrides.** A case may ask one model differently (`per_model` in the case file). That
   is the most flexible, and also the easiest way to make a comparison unfair by accident. Allow it
   (proposed, always shown as "asked differently"), or allow overrides only per model for the whole
   experiment?
3. **Ranking with different case counts.** When two models can do different numbers of cases, the ranking
   uses each setup's mean over its own applicable cases (proposed), with head-to-head deltas only on
   shared cases. Or rank only on the cases every model can do (fairer, but it can leave very few)?
