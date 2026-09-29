# 0010: Run conditions: experiments that refuse a busy machine

## Status

`implemented in 0.1.0` (accepted 2026-09-29, owner: "start implementing"; open questions take the proposed answers; implementation choices in design/decisions.md D-013 to D-019). Designed together with hone-models changes
[0016 machine state](https://github.com/honeworks/hone-models/blob/main/design/changes/0016-machine-state.md),
which provides the machine probe (§6 uses its interface exactly), and
[0015 generation models](https://github.com/honeworks/hone-models/blob/main/design/changes/0015-generation-models.md),
which makes ComfyUI's models registry entries. The dashboard parts (§12) follow the
layout of design/decisions.md D-012.

## Context

Experiments (design change [0009](0009-experiments.md)) run on one shared machine: an RTX 4060 Laptop GPU
with 8 GB, 24 CPU cores, 30 GB of RAM. The same machine runs the other honeworks repos' GPU tests,
ComfyUI, Blender renders, Ollama for chat, and the owner's own work.

A busy machine corrupts results without saying so:

- a model left loaded in Ollama holds VRAM, so the model under test is partly offloaded to the CPU. Its
  samples are slower, some hit the timeout and become errors, and its pass rate drops. The experiment then
  "finds" that the model is worse, when the machine was full;
- another GPU job or heavy CPU work (a render, a build, a test suite) changes time and speed measurements,
  so a `measure = { seconds = "lower" }` comparison measures the neighbours, not the setups.

The owner decided where each part belongs:

- **hone-select** refuses to start or pauses an experiment when the machine does not match the conditions
  the experiment declares, and marks every sample with the conditions it ran under;
- **hone-models** knows and controls the model state: which models are loaded, their VRAM, unloading them,
  and its GPU leases. hone-select's core cannot import hone-models (it depends only on pydantic), so
  hone-select owns a port and hone-models provides an adapter through the optional extra
  `hone-select[models]`, like the judges (current.md §7.8, D-002);
- **python and command experiments** (Blender, exporters, fixers) must be protected without hone-models
  installed, so hone-select checks CPU, free RAM, the machine-wide GPU lock
  (`/tmp/honeworks-gpu.lock`, the `flock` used by every repo's `scripts/gpu-lock.sh`) and GPU memory
  through `nvidia-smi` on its own.

## Problem

1. An experiment starts and runs whatever the machine is doing. Nothing checks free VRAM, loaded models,
   CPU load or free RAM before a sample.
2. Nothing records the machine's state per sample, so a slow or failed sample caused by a neighbour looks
   exactly like one caused by the setup, and the results cannot leave it out.
3. Nothing keeps other GPU users away for the length of a run. `[generate] wrap = ["scripts/gpu-lock.sh"]`
   takes the lock per sample only, so another job can take the GPU between two samples and leave a model
   loaded.
4. Nothing unloads models the experiment does not need. `after_group` hooks do it by hand, per project.

## Options

1. **Declared conditions in `experiment.toml`, checked by hone-select, with an optional machine probe port
   for model state (proposed).** Built-in checks need only the standard library and `nvidia-smi`; model
   state comes from hone-models through the port when it is installed.
2. **Leave it to wrappers** (`wrap = ["scripts/gpu-lock.sh"]`, `after_group` hooks): per sample only, no
   CPU or memory checks, nothing recorded, and the results cannot tell a good sample from a disturbed one.
3. **Everything in hone-models** (hone-select calls one "make the machine ready" function): python and
   command experiments without hone-models would stay unprotected, and hone-select would still have to
   decide what "wait", "stop" and "outside the conditions" mean for its runs and results.
4. **Built-in checks only, reading Ollama's `/api/ps` directly:** puts model-server knowledge into
   hone-select, which the owner assigned to hone-models, and duplicates it.

## Decision

Option 1. An experiment declares the conditions it needs; hone-select checks them before the run and
between samples, waits or stops when they do not hold, holds the GPU lock for the whole run, records the
machine's state with every sample, and leaves samples that ran outside the conditions out of the results.

### 1. The declaration

```toml
[conditions]                  # optional; without it an experiment runs as today (the state is still recorded)
max_cpu_load = 0.5            # share of all CPU cores busy (0..1), measured over one second between samples
min_free_ram_gb = 8           # available RAM (MemAvailable)
min_free_vram_gb = 7          # free memory on GPU 0, not counting the models this experiment needs (§3)
max_gpu_utilization_pct = 20 # GPU 0 busy (0..100 %) between samples, when this experiment is not using it
only_needed_models = true     # other loaded models are unloaded through the probe; needs `probe`
models_on_gpu = true          # after a sample, the needed models must be fully in VRAM; needs `probe`
models = ["{setup.model}"]    # the models a python / command subject needs (§3); placeholders as in `command`
gpu_lock = true               # hold the machine-wide GPU lock for the whole run (true: $HONE_GPU_LOCK or
                              # /tmp/honeworks-gpu.lock; or a path)
if_busy = "block"             # another process holds a GPU lease or the lock: block (unload nothing, wait)
                              # | unload (unload the unneeded models anyway); passed to the probe's prepare
warm_up = true                # load the needed models before a group's first sample (probe's load), §6
on_violation = "wait"         # wait (up to wait_timeout, then stop) | stop | record_only
wait_timeout = 1800           # seconds one wait may last
probe = "hone_models:machine" # optional; the built-in checks always run
```

Every key is optional; a key that is not set is not checked. `ExperimentSpec` gets a
`conditions: ConditionsSpec` (pydantic, unknown keys rejected, like every other section).

Refinements of the owner's sketch, and why:

- **`max_cpu_load` is measured over one second from `/proc/stat`, not the 1-minute load average.** The
  load average includes the experiment's own previous sample (a Blender render using all cores leaves a
  load of 20 for minutes) and would make the run wait for itself. One second between samples, when none of
  the experiment's processes run, measures the neighbours only.
- **`min_free_vram_gb` does not count the models this experiment needs.** A prompt subject's model stays
  loaded between samples; counting it as "used" would fail the check after the first sample. With a probe
  the needed models' VRAM is added back; without one (python and command subjects, whose processes end
  after each sample) the plain free memory is used.
- **`max_gpu_utilization_pct` and `models_on_gpu` are new.** Utilization catches another GPU job that uses
  little memory but much compute; `models_on_gpu` catches the exact symptom the owner saw (the model under
  test partly on the CPU), from the probe's `size_gb` and `vram_gb` (hone-models reads Ollama's
  `size_vram`, so a partly offloaded model has `vram_gb < size_gb`). Both are cheap: the data is already
  read. See open question 7.
- **`gpu_lock = true`** means the family's lock path (`$HONE_GPU_LOCK`, default
  `/tmp/honeworks-gpu.lock`, the same rule as `scripts/gpu-lock.sh`), so a definition does not repeat it.
- **`models`** for python and command subjects (§3).

### 2. Where the checks happen

```text
start ──▶ take the GPU lock (wait for it) ──▶ can every declared check be measured? (no: refuse, §5)
      ──▶ [between samples] ──▶ sample ──▶ [between samples] ──▶ sample ──▶ ... ──▶ scoring ──▶ release
```

The **between-samples check** runs before the first sample, between every two samples, and after the
last one. It is the "after" check of the sample that just ended and the "before" check of the next one,
so each sample costs one check (about one second, most of it the CPU window), not two.

One check, in this order:

1. when the `run.order` factor changes, the `after_group` hook runs first (as today);
2. with `only_needed_models` and a probe: `probe.prepare(needed)` for the **next** sample (§3), which
   unloads the models it does not need, including the previous group's model;
3. a reading: CPU busy share over one second, `MemAvailable`, GPU 0 from `nvidia-smi` (or the probe's
   `gpus`), the lock state, and the probe's `snapshot()`;
4. every declared condition is judged `ok`, `outside` (with the value and the limit) or `unknown` (with
   why it could not be measured).

The result of the check is written into the previous sample's `result.json` as its `after` reading and
kept as the next sample's `before` reading. A group change is therefore not a special case: the next
sample's `prepare` sees a different needed model and unloads the old one. Most `after_group` hooks written
to unload models become unnecessary (they keep working).

The scoring phase (one selection per case, judges included) is not checked: it is not timed and its
numbers do not depend on speed. The GPU lock is still held during scoring, because local judges use the
GPU.

`plan --pilot` runs its one sample under the same rules without waiting: when a condition does not hold,
the pilot is refused with the reason, so an estimate never comes from a busy machine.

### 3. Needed models, per subject kind

| Subject | Needed models |
|---|---|
| `prompt`, `generate` (0011) | the setup's `model` factor value (or `client_args.model` when there is no `model` factor) |
| `python`, `command` | `[conditions] models`, placeholders filled from the sample (`"{setup.model}"`, `"{case.source_model}"`); none when not declared |
| any, with `[conditions] models` | the declared list (it overrides the default) |

"None" is meaningful: a Blender experiment with `only_needed_models = true` and no `models` runs with no
model loaded at all, which is what a render timing needs. Judges are not needed during generation; if a
local judge is still loaded from an earlier run, `prepare` unloads it before the first sample.

Names are hone-models registry ids (such as `gemma4-12b` or `ace-step-1.5-xl-turbo`), as the subject uses
them; the probe maps them to the server's names and reports each loaded model's `model_id` (§6). ComfyUI's
models are registry entries too (hone-models 0015), so an image or music experiment names its model like
any other; `prepare` keeps ComfyUI's memory when every model it holds is needed and frees it otherwise.

### 4. Waiting, stopping and recording only

| `on_violation` | Before a sample (the check fails) | After a sample (it ran outside the conditions) |
|---|---|---|
| `wait` (default) | the run waits, checking every 10 s, until the conditions hold (then it continues) or `wait_timeout` passes (then it stops) | the sample is set aside and run again once, when the conditions hold |
| `stop` | the run stops at once, with the reason | the sample is set aside; the run stops; `start` again runs it once more |
| `record_only` | the sample runs anyway | the sample is kept, marked |

- **Set aside** means its `result.json` is written as `outside-1.json` in the same folder (kept as
  evidence, never counted), so the sample is not done and runs again. If the second run is also outside,
  it is kept as `result.json`, marked `outside`: a setup that always disturbs the machine (a model too big
  for the GPU) cannot loop forever, and the results show why it has no numbers.
- **The GPU lock is not a condition:** in every mode the run waits for the lock up to `wait_timeout`,
  then stops. Running without the lock is never the result of a violation.
- `wait_timeout` is per wait, not for the whole run. The time spent waiting is not part of any sample's
  `seconds` and not part of the time budget (which sums sample seconds, as today).
- `experiments stop` ends a wait within one poll (the `STOP` file is checked while waiting).

`run.json` shows it:

```json
{
  "state": "waiting",
  "pid": 41120,
  "started_at": "2026-10-02T09:12:00Z",
  "definition_hash": "…",
  "waiting": {
    "since": "2026-10-02T11:40:12Z",
    "until": "2026-10-02T12:10:12Z",
    "reasons": ["min_free_vram_gb: 3.1 GB free, needs 7 (ollama 4.6 GB)",
                "only_needed_models: llama3.1:8b is loaded and could not be unloaded (leased by pid 5120)"]
  },
  "waits": [{"since": "…", "ended": "…", "outcome": "conditions met", "reasons": ["…"]}],
  "stopped_because": null
}
```

- `state` gains `waiting` (a live process, like `running`: `Project.status` reports `waiting` while the
  pid is alive and `stopped` when it died); `start` refuses a waiting experiment like a running one.
- A stop caused by the conditions (timeout, or `on_violation = "stop"`) sets `state = "stopped"` and
  `stopped_because` to the reasons; `start` resumes it as today.
- `waits` keeps every wait of the run (since, end, outcome, reasons), so a long run's pauses stay visible.
- `hone-select experiments status` prints `waiting` and the first reason:
  `E0003  waiting     120/576  Open-weight writing  (min_free_vram_gb: 3.1 GB free, needs 7)`.

Two experiments started at once on the same machine: the second waits for the GPU lock (or, for CPU
conditions, for the first one's load to end). A waiting run is idle, so the two never wait for each other.

### 5. Unknown values

A check that cannot measure is `unknown`, never `ok` and never `outside` by accident:

- **At start:** a declared condition that cannot be measured on this machine refuses the run with a
  message that says what to do, for example `min_free_vram_gb is set but GPU memory cannot be read (no
  nvidia-smi and no probe): install the NVIDIA driver tools, set probe = "hone_models:machine", or remove
  the key`. The plan shows the same (§7) so it is found before approval.
- **During the run:** a reading that fails (`nvidia-smi` times out, the probe raises) makes that condition
  `unknown` for this check, and `unknown` is treated like `outside`: the run waits (or stops); with
  `record_only` the sample is marked `unknown`. A condition holds only when it was measured.
- **Definition errors at plan time** (`ConfigError`): `only_needed_models` or `models_on_gpu` without a
  `probe`; `min_free_vram_gb` with a `prompt` subject without a `probe` (its own model would count as used,
  §1); `gpu_lock` together with a `wrap` that contains `gpu-lock.sh` (the wrapper would wait forever for
  the lock the run holds, §8); `models` placeholders that name no factor or case field.

With a probe, the GPU facts come from it; when it reports `gpus: None` (neither NVML nor `nvidia-smi`
answered there), hone-select tries its own `nvidia-smi` reading, and then the GPU checks are `unknown`. A
model server the probe reports as `running: None` (it could not tell) makes `only_needed_models` and
`models_on_gpu` `unknown`; `running: False` (connection refused) means it holds nothing.

Readings that are not needed by any declared condition are still recorded when they can be read, and
simply left out when they cannot.

### 6. The port and the hone-models adapter

hone-select owns a new Protocol in `hone_select.ports` (additive; `PORTS_VERSION` stays `"1"`). Its shape
is the one hone-models 0016 implements (`hone_models.machine.Machine`); hone-select reads every key as
optional, and an unknown value is `None`, never 0.

```python
class MachineProbe(Protocol):
    """The machine's model state (design change 0010), provided by hone-models 0016; any object fits."""

    def snapshot(self) -> Mapping[str, Any]: ...
    # {"time": "…",
    #  "gpus": [{"index": 0, "name": "RTX 4060 Laptop", "memory_total_gb": 8.0, "memory_used_gb": 6.9,
    #            "memory_free_gb": 1.1, "utilization_pct": 4, "processes": [{"pid": 3310, "name": "ollama",
    #            "memory_gb": 6.6}]}],                               # None when no reader answered
    #  "servers": [{"server": "ollama", "running": True, "error": None},
    #              {"server": "comfyui", "running": None, "error": "timeout"}],   # True | False | None
    #  "loaded_models": [{"server": "ollama", "name": "gemma4:12b-q4", "model_id": "gemma4-12b",
    #                     "size_gb": 7.1, "vram_gb": 6.6},
    #                    {"server": "comfyui", "name": "z-image-turbo.json", "model_id": "z-image-turbo",
    #                     "size_gb": None, "vram_gb": None}],        # model_id None: a job hone-models did not run
    #  "gpu_lock": {"path": "/tmp/honeworks-gpu.lock", "held": True, "holder": "hone-flow pid 5120",
    #               "mine": False},
    #  "leases": [{"name": "gpu:tts", "pid": 5120, "gb": 3.0, "mine": False}]}

    def prepare(self, needed: Sequence[str], *, if_busy: str = "block") -> Mapping[str, Any]: ...
    # Make sure only `needed` (registry ids) are loaded: unload the others, never load one, never wait.
    # if_busy "block": unload nothing while another process holds a lease or the lock; "unload": anyway.
    # {"needed": [...], "if_busy": "block", "blocked_by": [...] | None, "unloaded": [{"server", "name"}],
    #  "released": [...], "errors": [...], "missing": [...], "loaded_models": [...], "need_gb": 7.1}

    # Optional (hone-select checks with hasattr): warm a model up. hone-models 0016's mk.machine.load.
    # def load(self, model_id: str) -> Mapping[str, Any]:
    # {"model_id", "loaded": True | False | None, "seconds", "size_gb", "vram_gb", "error"}
```

How hone-select reads the result:

- **`blocked_by`** (another process holds the machine-wide lock or a GPU lease): with `if_busy = "block"`
  (default) hone-models unloads nothing, and `only_needed_models` is `outside` with the reason ("blocked by
  lease gpu:tts, pid 5120"), so the run waits or stops by `on_violation`. With `if_busy = "unload"` the
  unneeded models are unloaded anyway and `blocked_by` is only recorded in the environment.
- **`errors`** (an unload failed): `only_needed_models` is `outside`; when the error is a server that did
  not answer, it is `unknown`.
- **`missing`** (a needed model is not loaded yet, since `prepare` never loads): with `warm_up = true` and a
  probe that has `load`, hone-select calls `load(model_id)` for each missing model before the sample and
  records the answer in the environment (`warm_up`: seconds, `vram_gb`); a `load` that answers
  `loaded: None` (not supported: ComfyUI and `command` models, in-process models) or fails leaves the
  sample `cold`. Without `warm_up` the sample that follows is marked `cold` in its environment (it
  includes the load time); see open question 9. `load` answering `vram_gb < size_gb` is `models_on_gpu`
  `outside` before the first sample, not after it.
- **`need_gb` larger than `memory_total_gb`**: `models_on_gpu` is `outside` with the reason "the needed models
  do not fit in the GPU"; at plan time this is shown before approval.
- **`prepare` raising** (for example an unknown registry id) at the first check refuses `start` with the
  message; later it is an `unknown` reading.
- **The run's own lock:** when hone-select holds the lock (§8) it sets `HONE_GPU_LOCK_HELD=1`, so the probe
  reports the lock as `mine: True` and `prepare` is not blocked by the run itself. With `gpu_lock` declared,
  a lock or lease with `mine: False` is `outside` ("another process holds the GPU lock: hone-flow pid 5120").

Resolution, like the judges: `probe = "<name>"` resolves by exact name in the entry-point group
`hone.machine_probes`, and the factory is called with no arguments. The pairing is the one decision clients
use (hone-models 0016): hone-models registers `hone.machine_probes` / `hone_models` →
`hone_models.machine:Machine`, and hone-select registers `"hone_models:machine" =
"hone_select.adapters.hone_models:machine"`, which imports `hone_models` only when called, returns
`Machine()`, and raises `ConfigError` ("install hone-select[models]") when it is missing. An unknown name is a
`ConfigError` listing the available names. hone-select catches `Exception` at every probe call; a failure
is an `unknown` reading with the error, never a crash.

`hone_select.testing` gains `FakeMachineProbe` (scripted snapshots, one per call or repeating the last;
`prepare` records `needed` in `fake.calls` and applies the scripted unloads) and `check_machine_probe` (the
contract checker: `snapshot()` returns a mapping of the documented shape, `prepare([])` returns a mapping).
hone-models runs the same checker against its adapter.

### 7. The plan shows the conditions

`plan.json` gains a `conditions` section, and the dashboard shows it next to the commands before approval:

```json
"conditions": {
  "declared": {"max_cpu_load": 0.5, "min_free_vram_gb": 7, "only_needed_models": true,
               "gpu_lock": "/tmp/honeworks-gpu.lock", "on_violation": "wait", "wait_timeout": 1800,
               "probe": "hone_models:machine"},
  "needed_models": {"gemma4-12b-…": ["gemma4-12b"], "styletune-12b-…": ["styletune-12b"]},
  "now": {"at": "2026-10-02T09:05:00Z",
          "checks": {"max_cpu_load": {"state": "ok", "value": 0.07, "limit": 0.5},
                     "min_free_vram_gb": {"state": "outside", "value": 3.1, "limit": 7},
                     "only_needed_models": {"state": "outside", "value": ["llama3.1:8b"]}},
          "would": "wait: min_free_vram_gb, only_needed_models (prepare would unload llama3.1:8b)"}
}
```

`plan` never unloads anything: `now` is a snapshot only. The conditions are in `experiment.toml`, so they
are part of the definition hash: changing a threshold makes the experiment a draft that needs a new plan
and approval (open question 1).

### 8. The GPU lock

- With `gpu_lock` set, `start` takes an exclusive `flock` on the lock file (polling with `LOCK_NB`, so the
  wait shows in `run.json` and `STOP` ends it) and holds it for the **whole run**: every sample, the waits
  between them, and scoring. It writes `<lock>.holder` in the format `gpu-lock.sh` uses
  (`<project folder> <pid> <time>`, plus the experiment id) and removes it at the end. The kernel releases
  the lock when the process dies, so a crash never leaves the GPU locked.
- Per run, not per sample: another job taking the GPU between two samples is exactly what corrupts a
  comparison, and it can leave a model loaded. The cost is that a long experiment blocks the other repos'
  GPU tests for its whole length; the plan shows that the run holds the lock.
- hone-select sets `HONE_GPU_LOCK_HELD=1` in its own environment and in every subject's environment, the
  variable `gpu-lock.sh` already exports to its child. When `start` itself runs with
  `HONE_GPU_LOCK_HELD=1` (for example `scripts/gpu-lock.sh hone-select experiments start E0003`), it does
  not take the lock again and records `gpu_lock: "held by the parent process"`.
- **Interaction with `wrap = ["scripts/gpu-lock.sh"]`:** a `flock` on a new file description blocks even
  when the same process tree holds the lock, so a wrapped subject would wait for the run forever. Until
  the family's lock users treat `HONE_GPU_LOCK_HELD=1` as "already held" (open question 5), `plan` refuses
  `gpu_lock` together with a `wrap` containing `gpu-lock.sh`, with the message "remove gpu-lock.sh from
  [generate] wrap: the run holds the GPU lock ([conditions] gpu_lock)". Without `gpu_lock`, the wrapper
  works as today (per sample).
- The same applies to hone-models' `FileLockGpuLease` if it is ever pointed at the same file: it must
  honour `HONE_GPU_LOCK_HELD=1` (part of the interface with hone-models, §6).

### 9. The environment of each sample

Every `result.json` gains `environment`, with or without `[conditions]`:

```json
"environment": {
  "status": "ok",
  "before": {"at": "…", "cpu_busy": 0.06, "free_ram_gb": 21.4,
             "gpu": {"index": 0, "free_gb": 7.6, "free_for_run_gb": 7.6, "utilization_pct": 0,
                     "processes": [{"pid": 3310, "name": "ollama", "memory_gb": 0.3}]},
             "loaded_models": [], "gpu_lock": "held by this run"},
  "after":  {"at": "…", "cpu_busy": 0.09, "free_ram_gb": 21.1,
             "gpu": {"index": 0, "free_gb": 0.9, "free_for_run_gb": 7.5, "utilization_pct": 2, "processes": […]},
             "loaded_models": [{"server": "ollama", "name": "gemma4:12b-q4", "model_id": "gemma4-12b",
                                "size_gb": 7.1, "vram_gb": 7.1}],
             "gpu_lock": "held by this run"},
  "checks": {"max_cpu_load": {"state": "ok", "before": 0.06, "after": 0.09, "limit": 0.5},
             "models_on_gpu": {"state": "ok", "after": {"gemma4-12b": 1.0}}},
  "prepared": {"blocked_by": null, "unloaded": [{"server": "ollama", "name": "llama3.1:8b"}],
               "errors": [], "missing": [], "need_gb": 7.1},
  "cold": false,
  "attempt": 1
}
```

`status` is one of:

| Status | Meaning | In the results |
|---|---|---|
| `ok` | every declared condition held before and after the sample | counted |
| `outside` | a declared condition did not hold before or after | left out, counted as `outside` |
| `unknown` | a declared condition could not be measured before or after | left out, counted as `unknown` |
| `not_checked` | the experiment declares no conditions (the readings are still there) | counted |

A `result.json` written before this change has no `environment` and is read as `not_checked`, so existing
outputs keep their numbers.

### 10. Results

- Samples marked `outside` or `unknown` are left out of **every** number: totals, criteria, pass rate,
  errors, wins, measurements and the `measure` ranges. Speed is not the only thing a busy machine
  corrupts: timeouts become errors and lower the pass rate, so leaving them out only of the timing would
  still bias the comparison. They are also left out of each case's selection (they are not candidates).
- Each setup and factor level gains `outside` and `unknown` counts next to `samples`, and `results.json`
  gains `conditions: {"declared": {...}, "excluded": N, "reasons": {"min_free_vram_gb": 12, ...}}`.
- `summary.md` opens with one line when anything was left out: "14 of 576 samples ran outside the run
  conditions and are not counted (12 × min_free_vram_gb, 2 × max_cpu_load)." A setup whose every sample
  was left out shows `no samples in conditions` instead of numbers.
- `hone-select experiments report EID --include-outside` recomputes with them counted (for a
  `record_only` experiment, or to see the effect); the summary then says so in its first line.

### 11. Records

No new span names. The experiment's own record stays in its files, as in 0009: each sample's
`environment` in `result.json`, the waits in `run.json`, the declared conditions and the reading at plan
time in `plan.json`. The selection spans are unchanged; candidate meta gains `environment_status`, so the
`hone.select.generate` span of a sample shows the status it ran under. Readings contain no secrets
(process names and model names only).

### 12. The dashboard

On the Experiments page (in the layout PR #6 lands):

- **List:** a `waiting` status badge (amber, like the pending states) whose tooltip is the first reason and
  the time waited; `stopped` shows `stopped_because` when the conditions stopped it.
- **An experiment:** a *Run conditions* card: the declared conditions, the plan's reading before approval
  (with what the run would do), and while running or waiting the last reading and the reasons, with the
  wait's deadline. The waits of the run are listed under it.
- **The sample panel:** the environment (before / after readings, each check's state, what `prepare`
  unloaded, the attempt), and for a sample that was run again, a link to its `outside-1.json`.
- **Samples and results:** `outside` and `unknown` samples carry a badge and are greyed out; the results
  show "N samples not counted" with the reasons, and a setup without samples in conditions says so.

`GET /api/experiments/<eid>` returns the same data (the status carries `waiting`; each sample row carries
`environment`). The dashboard's writes are unchanged.

### 13. Acceptance cases

| AC | Scenario | Expected |
|---|---|---|
| AC-30 | Conditions in the definition and the plan | `[conditions]` is validated (unknown keys, bad values); `only_needed_models` / `models_on_gpu` without a probe, `min_free_vram_gb` for a prompt subject without a probe, `gpu_lock` with a `gpu-lock.sh` wrap and unresolvable `models` placeholders are `ConfigError`s that say what to change; `plan.json` shows the declared conditions, the needed models per setup and the current reading with `ok` / `outside` / `unknown` per check and what the run would do; `plan` unloads nothing; editing a condition makes the experiment a draft |
| AC-31 | Waiting and stopping | with a scripted busy reading before a sample, `run.json` is `waiting` with the reasons and `status` reports `waiting`; `start` refuses a waiting experiment; when the reading turns good the run continues; `STOP` ends a wait; `wait_timeout` stops the run with `stopped_because`; `on_violation = "stop"` stops at once; `record_only` runs and marks; `start` resumes a stopped run; waiting time is in no sample's seconds or the budget |
| AC-32 | The environment and the results | every sample's `result.json` has `environment` with before / after readings, checks and status, also without `[conditions]` (`not_checked`); a sample outside after it ran is set aside as `outside-1.json` and run once more, a second outside run is kept and marked; `outside` and `unknown` samples are left out of every number and of the selection, counted per setup and factor level with reasons; `report --include-outside` counts them and says so; old `result.json` files without an environment count as before |
| AC-33 | Needed models, the probe and unknowns | `prepare` is called with the setup's model for a prompt subject, the declared `models` (placeholders filled) or nothing for python / command subjects, and unloads the previous group's model at a group change; `prepare`'s result (`blocked_by`, `unloaded`, `errors`, `missing`) is in the environment, `blocked_by` makes the run wait, `errors` is `outside` (or `unknown` for a server that did not answer), `missing` marks the sample `cold`; `min_free_vram_gb` does not count the needed models; `models_on_gpu` marks a partly offloaded model; `gpus: None` falls back to hone-select's own `nvidia-smi` reading, then `unknown`; a server with `running: None` makes the model checks `unknown`; `if_busy` is passed to `prepare` and `"unload"` unloads despite `blocked_by`; with `warm_up` a missing model is loaded through `load` and the sample is not `cold`, and a `load` that answers "not supported" leaves it `cold`; a probe that raises gives `unknown` (the run waits); a declared check that cannot be measured refuses `start` with a message; `FakeMachineProbe` passes `check_machine_probe`; `probe = "hone_models:machine"` without hone-models is a `ConfigError` naming the extra |
| AC-34 | The GPU lock | with `gpu_lock`, another process cannot take the lock during the run (samples, waits and scoring) and can right after; the holder file is written and removed; a run whose lock is held elsewhere waits (visible in `run.json`) and stops at `wait_timeout`; with `HONE_GPU_LOCK_HELD=1` at start the lock is not taken again; subjects receive `HONE_GPU_LOCK_HELD=1`; the lock is released when the process is killed |
| AC-35 | The dashboard | the list shows the `waiting` badge and reason; the experiment shows the run-conditions card, the plan's reading before approval and the waits; the sample panel shows the environment; outside samples are marked and the results say how many were not counted |

### 14. Tests with fakes (no GPU, no model, no waiting in real time)

- **Built-in readings** live in one module (`experiments/conditions.py`) whose sources are injectable: the
  `/proc` root (tests write fake `stat` and `meminfo` files, two `stat` snapshots for the one-second window),
  the `nvidia-smi` command (a small script on a temporary `PATH` printing scripted CSV, one that fails, one
  that is missing) and the clock and poll interval (tests use a fake clock and a 0-second poll, so a
  30-minute `wait_timeout` passes instantly).
- **Model state:** `FakeMachineProbe` with scripted snapshots and `prepare` results in hone-models 0016's
  shape (a leftover model that `prepare` unloads, a `blocked_by` lease, an unload in `errors`, a server
  with `running: None`, `gpus: None`, a `missing` model, a partly offloaded model, a probe that raises).
- **The lock:** a real `flock` on a file in `tmp_path` (it needs no GPU); a helper subprocess holds it to
  test waiting, and tries `LOCK_NB` during a run to test holding.
- **Subjects:** the existing fake text client and small python / command subjects; the command subject
  prints its `HONE_GPU_LOCK_HELD`.
- Nothing in the default suite reads the real `/proc`, calls the real `nvidia-smi` or touches
  `/tmp/honeworks-gpu.lock`. One optional `gpu`-marked test (through `scripts/gpu-lock.sh`, which then
  exercises the `HONE_GPU_LOCK_HELD` path) reads the real machine once and checks the reading's shape.

## Consequences

- An experiment no longer measures its neighbours: it waits for a quiet machine, keeps other GPU users
  away for its whole length, unloads models it does not need, and every sample says what the machine was
  doing. The owner's "model offloaded by a leftover model" case is prevented (VRAM check, `prepare`) and,
  if it happens anyway, detected (`models_on_gpu`) and left out.
- Results can be trusted more and explained: an excluded sample is still on disk with its reasons.
- Python and command experiments are protected without hone-models (CPU, RAM, GPU memory and utilization,
  the lock); model state and unloading need `hone-select[models]`.
- Costs: about one second per sample for the CPU window (576 samples: about ten minutes); a run that
  waits can take much longer than its estimate; a long experiment holds the GPU lock for hours and other
  repos' GPU tests wait (or time out after `HONE_GPU_LOCK_TIMEOUT`, two hours by default).
- Leaving out-of-condition samples out of every number means a heavily disturbed setup can end with few or
  no counted samples; the summary says so rather than showing numbers from a busy machine.
- hone-select grows by a conditions module (declaration, built-in readings, checks, the wait loop, the
  lock: about 250 lines), small changes to the runner, results, plan, status and dashboard, one port, one
  fake, one contract checker and one adapter function: roughly 450–550 lines with the dashboard.
- Linux only for the CPU and RAM readings (`/proc`); elsewhere they are `unknown`, so declaring them
  refuses the run there, which is explicit.
- hone-select depends on hone-models honouring the port's shape (§6); the contract checker keeps them in
  step.

## Migration and compatibility

- Additive. An `experiment.toml` without `[conditions]` runs as today; its samples gain an `environment`
  with readings and status `not_checked`, which changes no number.
- Existing `result.json` files (without `environment`) are read as `not_checked` and keep counting.
- Adding `[conditions]` to an existing experiment changes its definition hash: it needs a new plan and
  approval, then `start` resumes and runs only the missing samples (the done ones stay `not_checked`, and
  the summary says how many were not checked).
- `run.json` gains the `waiting` state, `waiting`, `waits` and `stopped_because`; a reader that knows only
  `running` / `stopped` / `completed` sees an unknown state while a run waits (only hone-select reads it).
- `hone_select.ports` gains `MachineProbe` (`PORTS_VERSION` stays `"1"`: nothing existing changes);
  `hone_select.testing` gains `FakeMachineProbe` and `check_machine_probe`; `pyproject.toml` gains the
  entry-point group `hone.machine_probes` with `hone_models:machine`. The `models` extra is unchanged
  (`hone-models>=` the version that ships the probe).
- Projects that use `wrap = ["scripts/gpu-lock.sh"]` keep working; to hold the lock for the whole run they
  replace the wrap with `[conditions] gpu_lock = true` (plan refuses both together, §8).
- `after_group` hooks that only unload models can be removed once `only_needed_models` is on; they keep
  working if kept.
- The experiment template (`experiments new`) gains a commented `[conditions]` block (open question 8).

## Open questions for the owner

1. **Conditions in the definition hash.** They are in `experiment.toml`, so they are hashed: tuning
   `wait_timeout` or a threshold needs a new plan and approval. Keep that (proposed: yes, conditions change
   what the results mean), or exclude `[conditions]` from the hash?
2. **Leave outside samples out of every number**, not only speed and time (proposed, because timeouts also
   lower pass rates), with `report --include-outside` to see them counted. Agreed?
3. **Run an outside sample once more**, then keep it marked (proposed). Or never re-run (fewer samples),
   or re-run until `wait_timeout`?
4. **Unknown readings:** refuse `start` when a declared check cannot be measured at all, and treat a
   reading that fails mid-run like a violation (wait). Agreed?
5. **Re-entrant lock in the family:** make every repo's `scripts/gpu-lock.sh` (and hone-models'
   `FileLockGpuLease`) skip locking when `HONE_GPU_LOCK_HELD=1`? Then `gpu_lock` and a `gpu-lock.sh` wrap
   can be combined, and the plan-time refusal (§8) goes away. This is a small change in every repo.
6. **Unloading other programs' models:** with `only_needed_models = true` the run unloads any model it does
   not need, including one the owner loaded for chat in the middle of a run. Acceptable, with hone-models
   blocking (and hone-select waiting) only while another process holds the GPU lock or a lease?
7. **The two additions to the sketch,** `max_gpu_utilization_pct` and `models_on_gpu`: keep them (proposed:
   cheap and they catch the reported symptom directly) or leave them for later?
8. **Defaults:** the template gets a commented `[conditions]` block with this machine's values (proposed:
   thresholds depend on the machine, so nothing is on by default). Or should `experiments new` turn it on
   for prompt subjects with a local client?
9. **Cold first samples:** `prepare` never loads a model, so the first sample of each model group includes
   the model's load time. hone-models 0016 now offers `load` (the owner accepted it there), so the
   proposal is `warm_up = true` in the template: warm the model up first, and mark a sample `cold` only
   when warm-up is not possible (ComfyUI and in-process models load during their first job), counting it
   but leaving it out of the speed numbers. Agreed?
