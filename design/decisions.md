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
