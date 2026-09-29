# Implementation decisions

Choices too small for a change record: places where the design was silent or ambiguous and the
implementation had to pick. Numbered in the order they were made; code comments refer to them by number.
Entries marked **awaiting owner review** are in effect but not yet confirmed by the owner.

## Awaiting owner review

These entries are in effect but not yet confirmed by the owner. When the owner decides, the entry's
**Status:** line records the outcome.

| Entry | Question for the owner |
|---|---|
| [D-003](#d-003-ranking-puts-deeper-cascade-stages-before-higher-partial-totals--2026-09-27) | Accept that ranking puts deeper cascade stages before higher partial totals (a deviation from the original design text)? |
| [D-010](#d-010-public-names-beyond-the-main-import-list--2026-09-27) | Accept the public names beyond the main import list (widens the documented public API)? |

## D-001: the `models` extra and the local path source  (2026-09-27)

- **Question:** `hone-select[models]` depends on `hone-models`, which was not published or installable
  while hone-select was built.
- **Options:** (a) depend on it immediately (breaks `uv sync`); (b) a path dependency on a sibling
  checkout; (c) declare the extra empty until hone-models can be installed.
- **Choice:** first (c), then, once hone-models could be installed, `models = ["hone-models>=0.1"]`,
  resolved for development through a `[tool.uv.sources]` path source to a sibling `../hone-models`
  checkout. The built wheel carries only `hone-models>=0.1`. The adapter imports `hone_models` lazily and
  raises a clear error if it is missing.
- **Reason:** keeps the repository buildable on its own while the two packages are developed side by side.
- **Status:** decided by the owner (2026-09-29). For the public repository the path source is replaced
  by a uv git source, `{ git = "https://github.com/honeworks/hone-models", branch = "main" }`, until
  hone-models is on PyPI; then the source is dropped. The development tools moved from a `dev` extra to
  the `dev` dependency group, so no published extra depends on another honeworks package's dev setup.

## D-002: judge `client` strings resolve by exact entry-point name  (2026-09-27)

- **Question:** How does `client = "hone_models:decision"` in `[judges.<name>]` resolve?
- **Options:** (a) import `module:attr` directly; (b) exact entry-point name in the group
  `hone.decision_clients`; (c) the prefix before `:` as the entry-point name.
- **Choice:** (b). hone-select registers `"hone_models:decision" =
  "hone_select.adapters.hone_models:decision"`; other packages register their own names. The factory is
  called with the other keys of the table (for example `model = "..."`). Unknown names raise
  `ConfigError` listing the available names.
- **Reason:** one rule, and no arbitrary imports from config files.
- **Note:** the entry-point name `hone_models:decision` contains a colon, which is unusual for an entry
  point name but valid; it reads like the factory it stands for.

## D-003: ranking puts deeper cascade stages before higher partial totals  (2026-09-27)

- **Question:** The first design sorted by `(rejected, -total, stage_reached desc, index)`. A candidate
  cut at stage 1 keeps a partial total (cheap scorers only) that can exceed a finalist's full total, and
  would then win.
- **Options:** (a) the original order; (b) `(rejected, total is None, -stage_reached, -total, index)`.
- **Choice:** (b), described in [`current.md`](current.md) §5.6.
- **Reason:** the cascade exists to eliminate; a candidate it cut must not beat the finalists. Within one
  stage the order is unchanged.
- **Status:** **awaiting owner review** (a deviation from the original design text).

## D-004: `Engine(cache=...)` default is the sentinel `"default"`  (2026-09-27)

- **Question:** The first design said `cache=None` meant both "the default SQLite cache" and "caching
  off".
- **Choice:** `cache: ScoreCache | Literal["default"] | None = "default"`. `"default"` builds a
  `SqliteScoreCache` (`cache.db` next to the SQLite span store, else `$HONE_HOME/select/cache.db`); `None`
  turns caching off. Only scores with a value are cached, so failures are retried on the next run.

## D-005: generation is sequential; `max_concurrency` is accepted but only 1 is used  (2026-09-27)

- **Reason:** async and concurrency are out of scope for v0.1; sequential generation keeps runs
  deterministic. The key is accepted so configs keep working when concurrency arrives.

## D-006: small semantic choices the design left open  (2026-09-27)

- Gates run cheapest first and **stop at the first failure** (the reason to order them by cost).
- A scorer value or confidence outside 0..1 (or NaN), or an unusable return type, becomes
  `Score(None, error=...)`: never clamped, never silently `None`. `True` and `False` are not scores.
- `first_above` never met: the best non-rejected candidate wins; `threshold_not_met` is in the trace.
- Tie escalation applies to `argmax` only. The contenders are all non-rejected candidates within
  `tie_margin` of the top (at least the top two). `pairwise_tournament` and escalation both use king of the
  hill in ranking order: a challenger takes over only by winning in both orders.
- Low confidence means any score of the top two candidates with `confidence < min_confidence` (`None`
  never triggers it).
- `fallback="first_valid"`: the first rejected candidate, in generation order, that has a total.
- Defaults: `escalate="none"`, `fallback="none"` (no surprise winners), `tie_margin=0` (only exact ties),
  `generate.n=4`.
- `@generator(name=None, *, cost=0.0)`: generators have a cost so `max_cost` can stop generation. Budgets
  are checked before each generation and before cascade stages 2 and later; a run may overshoot by one
  step (the check is "stop once reached"). A generator may raise `BudgetExceeded` itself (for example a
  provider's cap). A scorer or gate raising `BudgetExceeded` is treated like any other component error.
- A generator that raises is skipped with a `generate_error` entry; the run continues.
- Variation schedule: `seed = "increment"` (base seed + index) or a list (cycled); every other `vary` key
  is a list cycled independently (no cartesian product).
- Candidate meta is `{generator, index, seed, params, seconds}`, overlaid by the generator's own meta.
- The engine asks every pairwise judge in both orders; `PromptPairwise` asks one order per call, so "both
  orders, always" holds through the engine.
- The selection policies live in one module, `selectors.py` (three short policies), not a package.
- A `str` config that is a single line ending in `.toml` is a path; anything else is TOML text.
- Exactly one `@generator` must be registered for `run()`; `score()` needs none.

## D-007: adapters emulate decisions over a text client; CLI shape  (2026-09-27)

- **Question:** The design said the OpenAI and LangChain `DecisionClient`s are "emulated via JSON schema"
  without saying how, and listed the CLI commands without their options or output.
- **Choice:** one `EmulatedDecisionClient(text_client)` (`adapters/emulated.py`) asks all questions in one
  prompt and requests `{name: {"rationale", "answer"}}`, through `response_format=json_schema` (OpenAI) or
  an instruction (LangChain); replies are parsed plain, fenced or as the outermost `{...}`. Answers are
  `calibrated=False`; an answer outside the scale or options is `value=None, error="unusable answer ..."`.
  Transport errors raise the SDK's own exceptions unwrapped: wrapping them would hide the SDK's types, and
  every caller catches `Exception` at the port boundary anyway; the engine turns them into `Score(None)`.
  `OpenAIDecisionClient` defaults to `temperature=0`; `temperature=None` leaves it out (reasoning models
  reject it). A text client's `parsed` reply is checked only at the top level (an object with the schema's
  `required` keys); per-question checks happen in the emulated client.
- **CLI:** `run CONFIG --task task.json --registry MODULE [--seed] [--json]` (the registry module's
  top-level decorated functions and scorer objects are used), `explain RUN_ID [--db]`,
  `show RUN_ID [--db] [--json]` (every span of the run's trace). Package errors print one `error: ...`
  line and exit 1.
- **Note:** `openai>=3` uses `httpx2` internally, which `respx` does not intercept; the adapter tests pass
  a plain `httpx.Client` (the SDK accepts it) so the recorded HTTP fixtures work.

## D-008: components that take `trace=` get the span's context  (2026-09-27)

A design change: see [`changes/0002-trace-to-components.md`](changes/0002-trace-to-components.md).

## D-009: the selection-policies example is `examples/selection_policies.py`, not `selectors.py`  (2026-09-27)

- **Question:** The examples plan named `examples/selectors.py`. Run as a script, its folder comes first on
  `sys.path`, so the standard library's `socket` → `import selectors` loads the example itself, and the
  import of `hone_select` fails with a circular import.
- **Choice:** name it `selection_policies.py`; a test checks that no example shadows a standard-library
  module.

## D-010: public names beyond the main import list  (2026-09-27)

- **Question:** The examples use only the public API. The first design listed no import path for the
  score cache class or for explaining a stored run, and did not list `Engine` attributes.
- **Choice:** these are public and stable: `hone_select.cache.SqliteScoreCache` and
  `hone_select.explain.explain_run` (each module declares `__all__`), and the read-only attributes
  `Engine.config` (the validated `SelectionConfig`) and `Engine.judges` (name → client, including clients
  built from `[judges.*]`). The examples test allows exactly the modules listed in `examples/README.md`.
- **Reason:** users need a way to choose the cache file and to explain a stored run from Python; the docs
  already used both paths.
- **Status:** **awaiting owner review** (widens the documented public API).

## D-011: the dashboard checks the Host header only on a loopback bind  (2026-09-29)

Bound to `127.0.0.1` / `localhost` / `::1` (the default), the dashboard answers only requests whose `Host`
is its own loopback address and port: a DNS-rebinding page, which sends its own host name, gets 403 for
reads and writes. Bound to another address (`--host 0.0.0.0`, a machine name), it is reached by LAN
addresses and names it cannot list, so any `Host` is answered; writes still need the page's
`X-Hone-Dashboard` header and, when present, an `http(s)` `Origin` on the same address as the `Host`.
Binding to another address is the user's explicit choice to expose the page on the network.

## D-012: the dashboard's layout  (2026-09-29)

The dashboard is one static page (no build step, no dependencies): a sidebar with Experiments (the start
page), Runs and Candidates; light and dark themes from the system setting; tables with a search box,
optional per-column filters and sortable headers; details in a side panel. An experiment opens on the
answer (the best setup and a chart per setting) and the next step, with the decision on top while a plan
is proposed. `GET /api/info` returns the store and project paths and the version for the sidebar.
Reason: the owner asked for a simple, modern and useful page; the answer and the next action come first.

## D-013: where run-condition readings come from, and the CPU window  (2026-09-29)

- **Question:** Design change 0010 §14 asks for injectable sources, and every sample records readings even
  without `[conditions]`; a one-second CPU window per sample costs ten minutes on 576 samples.
- **Choice:** `hone_select.experiments.conditions.Sources` (the `/proc` root, the `nvidia-smi` command, the
  clock, the sleep, the poll interval, the window, and a probe object used instead of resolving
  `[conditions] probe`); `experiments.start(..., sources=)` and `Project.plan(..., sources=)` take it, the
  default is this machine. The CPU window is one second only when `max_cpu_load` is declared, otherwise
  0.1 s. The wait's `since` / `until` and each reading's `at` come from the sources' clock. The default test
  suite replaces the default sources with a quiet fake machine and points `$HONE_GPU_LOCK` at a temporary
  file (`tests/conftest.py`).
- **Reason:** tests never read the real machine or wait in real time; readings stay cheap when nothing
  needs the CPU window.

## D-014: one check between samples serves both, and its limits  (2026-09-29)

- **Question:** 0010 §2 makes one check the "after" of a sample and the "before" of the next, with
  `prepare` for the next sample before the reading.
- **Choice:** as written. Consequences: at a model-group change `prepare` unloads the previous model
  before the reading, so `models_on_gpu` judges the previous sample's model after it only while it is still
  loaded (the before check of every sample always judges it); after the last sample the check is a
  reading only (no `prepare`, so nothing is unloaded before scoring); a set-aside sample runs again right
  after the conditions hold, with a fresh check (and `prepare`) when it needs other models than the next
  sample did. Without `only_needed_models` (no `prepare`), a missing model is a needed one that is not in the
  probe's reading; without a probe nothing is `cold`.
- **Reason:** the simplest reading of the record; one reading per sample.

## D-015: an unknown reading at the first check refuses `start`  (2026-09-29)

- **Question:** 0010 §5 refuses a declared check "that cannot be measured on this machine" at start, and
  treats a failed reading during the run like a violation. The first check cannot tell the two apart.
- **Choice:** any `unknown` condition at the run's first check (and a `prepare` that raises there) refuses
  `start` with the reasons and what to do; `run.json` is `stopped` with `stopped_because`. Later, `unknown`
  counts like `outside`.
- **Reason:** a run should not begin on readings it cannot trust; starting again is cheap.

## D-016: the GPU lock is judged as the `gpu_lock` condition  (2026-09-29)

- **Question:** 0010 §4 says the lock "is not a condition" (the run always waits for it), and §6 says a lock
  or lease another process holds is `outside` when `gpu_lock` is declared.
- **Choice:** taking the lock is not a condition (every mode waits for it up to `wait_timeout`); with
  `gpu_lock` declared, each check also judges `gpu_lock`: `ok` while the run (or its parent) holds it,
  `outside` when the probe reports the lock held by another process or a lease with `mine: False`. In the
  plan, a free lock is `ok`.
- **Reason:** another process's GPU lease disturbs a sample as much as a foreign lock.

## D-017: samples outside the conditions are scored apart  (2026-09-29)

- **Question:** 0010 §10 leaves outside samples out of each case's selection but lets `report
  --include-outside` count them, which needs their totals.
- **Choice:** the scoring phase scores them on their own (`Engine.score`, trace step
  `experiment-outside`) and writes them to `selection.json` under `outside`; they never compete and never
  win. This costs their judge calls.
- **Reason:** `--include-outside` then recomputes from what is stored, without scoring again.

## D-018: a cold sample's speed is its `seconds`  (2026-09-29)

- **Question:** 0010 open question 9: a cold sample counts but is left out of the "speed numbers".
- **Choice:** the speed number is the `seconds` measurement: it is left out of the results' measurements,
  of the `measure` range and of the sample's `measure` score (the candidate carries no `seconds`); every
  other number counts the sample.
- **Reason:** `seconds` is the measurement hone-select takes itself; a subject's own measurements have no
  known meaning.

## D-019: the samples' environment status in the records  (2026-09-29)

- **Question:** 0010 §11 says the `hone.select.generate` span of a sample shows `environment_status`, but an
  experiment's selection uses `Engine.select`, which records no generate spans.
- **Choice:** each candidate's meta carries `environment_status` (scorers and gates see it); the record of a
  sample's conditions is its `result.json` and `selection.json`. No span changes.
- **Reason:** adding generate spans to `Engine.select` would change the records of every selection.

## D-020: how `client` and `guides` resolve for model-aware experiments  (2026-09-29)

- **Question:** Design change 0011 §1 resolves a generate `client` "through the entry-point groups
  hone-models 0015 registers, as `hone_models:text` is today" (today that string is imported as
  `module:attribute`), and §5 resolves `guides` "as 0010's `MachineProbe`" (an entry point by exact name).
- **Choice:** `client = "<name>:<kind>"` with `<kind>` image, music or video is first looked up in
  `hone.<kind>_clients` by the entry-point name `<name>` (hone-models registers `hone_models`); otherwise it
  is imported as `module:factory`, so `hone_models:music` works either way. `guides = "<name>"` is an entry
  point of `hone.model_guides` by exact name, else a `module:factory` called with no arguments; with neither,
  the `ConfigError` lists the installed sources. `guides` defaults to `hone_models:guides` when the client
  starts with `hone_models:`.
- **Reason:** one rule per string, the same one `hone_models:text` follows; a `module:factory` lets a project
  (and the offline tests) bring its own client and guides without a new hone-models version.

## D-021: what judges see of a case, and a generation's data  (2026-09-29)

- **Question:** 0011 §4 says judges see "the case's fields and the output, never the setup, the per-model
  overrides or the guide"; prompt judges only see a candidate's `data`.
- **Choice:** every sample's `result.json` has `judge_view` (the case's shared fields, only those in the
  case's `judge_view` when set) and a candidate's meta carries it as `case`. A generate sample's `data` is
  `{"case": <judge view>, "files": [{"name", "sha256", "bytes", "mime", "width", "height", "duration_s"}]}`,
  so a prompt judge reads the case and the output files, never the prompt a model was sent. The subject
  itself gets the case as its model is asked (`per_model` over the fields; the shared fields stay in
  `shared_fields`).
- **Reason:** the judge needs to know what was asked to judge the output, and must not see a model's
  override, or two models' outputs would be judged against different requests.

## D-022: whose guide `{model_guide}` and `ctx.model_guide` are  (2026-09-29)

- **Question:** 0011 §4: "a chat model writes the image prompt for a target model named in the setup,
  given that model's guide"; the record names no factor.
- **Choice:** the guide of the setup's `target_model` factor when it has one, else of its `model`. Both are
  read into `plan.json` `models`. Without a guide the placeholder is empty and `ctx.model_guide` is `None`;
  a source's own `text` (hone-models' `as_text()`) is used as is, otherwise the JSON is written out as lines.
- **Reason:** the simplest explicit naming; prompt-writing experiments put the image model in a factor.

## D-023: the needs grammar  (2026-09-29)

- **Question:** 0011 §3 gives examples (`"camera angle"`, `"references"`, `"duration_s >= 60"`,
  `"references >= 3"`, a factor value over `max_duration_s`) but no grammar.
- **Choice:** a need `<name> >= | <= | = <value>` is a limit: `<name>` is checked against the guide's list
  (`durations_s`, `sizes`) when it declares one, else against `max_<name>`; neither declared (or a value
  that is not a number) is `need_unknown`. Anything else is a name, met when the guide lists it as a
  feature or an input (case-insensitive). The factors `duration_s` and `size` add the need
  `duration_s = <value>` / `size = <value>`. With no guide for the model every need is `need_unknown`.
  `plan.json` `applicability` lists `not_applicable` cells (case, setup, model, the needs and the reasons)
  and `need_unknown` cells; `outputs` counts only applicable cells; the run skips the rest; a pilot uses
  the first applicable cell.
- **Reason:** covers the record's examples with one small parser; unknown is never false (open question 1).

## D-024: `start` asks again about models the plan found not installed  (2026-09-29)

- **Question:** 0011 §4: `start` refuses "until they are installed", but guides are read once at plan time.
- **Choice:** only for the models the plan marked `installed: "no"`, `start` asks the guide source again
  and refuses while it still says `"no"` (naming `hone-models models install <id>`); once installed, the
  approved plan starts without a new plan. Everything else uses the stored guides.
- **Reason:** installing a model does not change the definition, so it should not need a new approval.

## D-025: out of memory, sessions and the run order  (2026-09-29)

- **Question:** 0011 §1: `out_of_memory` "with 0010's conditions" marks the sample outside; the session is
  held "for a group of samples (`run.order = "by_model"`)".
- **Choice:** with any condition declared, a generation whose `error_kind` is `out_of_memory` gets the
  environment status `outside` with an `out_of_memory` check, so 0010's set-aside and rerun apply; without
  conditions it is a failed sample. The generate subject opens the client's `session()` (when it has one)
  for its model and keeps it while the next sample has the same model; the runner ends it when the model
  changes (before the next check), at the end of the run and after a pilot. Grouping by model is the
  existing default `run.order = "model"`; no `by_model` value was added. A sample set aside at the end of a
  model's group runs again in a new session (as D-014 runs it after a fresh check).
- **Reason:** reuses 0010's machinery without a second retry path; a new session after an out-of-memory
  failure also frees the memory.

## D-026: comparisons on shared cases  (2026-09-29)

- **Question:** 0011 §3: "baseline deltas, wins and factor-level comparisons use only the cases both
  sides can do, and say how many"; open question 3 keeps the ranking on each setup's own cases.
- **Choice:** per setup, `applicable` = `{cases, of, not_applicable, needs, need_unknown}` and the numbers
  over its own applicable cases (the ranking uses those means); `wins` stays the count of cases the setup
  won. Against a baseline: the difference over the cases both setups have results for, with
  `shared_cases`, `wins` and `losses` (cases where the setup's total is higher or lower). Each factor level
  is computed over the cases every level can do (a level can do a case when any of its setups can), with
  `shared_cases`. `results.json` also has `models` (license, `commercial_use`) and `could_not`.
- **Reason:** the most direct reading of the record; a setup's own number and a fair head-to-head are both
  shown with their counts.
