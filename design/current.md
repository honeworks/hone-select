# hone-select: the design as it stands (v0.1.0)

This is the design of hone-select today: its concepts, its rules and the behaviour it guarantees. Why the
package exists is in [`README.md`](README.md); how the design got here is in [`changes/`](changes/) and
[`decisions.md`](decisions.md). User documentation, with runnable examples, is in [`../docs/`](../docs/)
and [`../examples/`](../examples/).

## 1. What hone-select does

hone-select generates several candidates for the same task, scores each one with pluggable checks,
selects a winner with a configurable policy, and records everything. The engine knows nothing about the
domain.

"What is good" is defined in one of two ways, and both can be used in the same run:

1. **Custom script:** a Python function, or an executable in any language (JSON on stdin, JSON on stdout).
2. **Prompt and a chosen judge:** plain-English criteria run by any `DecisionClient` (§7.2): an LLM, local
   or hosted, or a decision model, injected by the user.

**No master LLM decides.** The winner comes from a selector applied to scores.

## 2. Guarantees

- **Useful alone.** A selection with plain functions takes about ten lines. The core depends only on the
  standard library and pydantic; it never imports an optional extra or another package it can work with.
- **Gates are not scores.** A gate rejects a candidate; it is never averaged into a total.
- **`None` is not `0`.** A scorer that fails, raises, or returns something unusable gives
  `Score(None, error=...)`. A value is never clamped or silently replaced.
- **Failures are soft and visible.** One broken candidate, scorer or judge never stops the run; every
  failure is in the decision trace.
- **Ties and low confidence escalate** to a pairwise judge that is asked in both orders.
- **Fallbacks are flagged.** A winner chosen from rejected candidates has `rejected=True`.
- **Budget-aware:** cost units, seconds and money stop generation cleanly.
- **Cache-aware:** scores are cached by candidate content, scorer name, scorer version and judge model.
- **Reproducible:** the same config and seed give the same ranking and totals. No id uses Python's `hash()`.
- **Fully recorded:** every step is a span in a local store, and a decision can be explained later from
  the store alone.

## 3. Data types

All in `hone_select` (module `hone_select.types`).

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    id: str                          # first 16 hex of sha256 over canonical JSON of data + file hashes
    data: Any
    files: Mapping[str, str] = {}    # name -> path
    meta: Mapping[str, Any] = {}     # generator, index, seed, params, seconds, plus the generator's own

@dataclass(frozen=True, slots=True)
class Score:
    value: float | None              # 0..1, higher is better; None = could not score
    confidence: float | None = None
    reason: str = ""
    details: Mapping[str, Any] = {}
    error: str = ""

@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    probability: float | None = None
    reason: str = ""
    details: Mapping[str, Any] = {}  # e.g. per-item verdicts; recorded on the gate span (change 0004)

@dataclass(slots=True)
class Scored:
    candidate: Candidate
    gates: dict[str, GateResult]
    scores: dict[str, Score]
    total: float | None
    rejected: bool
    stage_reached: int

@dataclass(slots=True)
class Result:
    winner: Scored | None
    ranked: list[Scored]             # everyone, best first; rejected after non-rejected
    decision: list[dict]             # ordered trace: generated, gated, scored, tie, escalation, fallback, stop
    run_id: str
    trace_id: str
    budget: dict                     # cost_used, seconds_used, money_used
```

`Candidate.of(data, files=None, meta=None)` computes the id. The same data gives the same id in every
process, which is what dedup and the score cache rely on. `Variation` is
`{"index": int, "seed": int, "params": dict}`, produced by the variation schedule (§5.10).

## 4. Components

A component is a generator, gate, scorer or pairwise judge. Each has a `name` (unique across all kinds), a
`cost` (for budgets and gate ordering) and a `version` (part of the score cache key).

### 4.1 Decorated functions

```python
@generator(name=None, *, cost=0.0)
def write(task, variation: Variation) -> Candidate | Any: ...        # a non-Candidate is wrapped

@gate(name=None, *, cost=0.0, version="1")
def hook_on_rhyme(c: Candidate) -> bool | GateResult: ...

@scorer(name=None, *, cost=1.0, version="1")
def syllable_balance(c: Candidate) -> float | Score | None: ...      # float -> Score; None -> Score(None)

@pairwise(name=None, *, cost=10.0, version="1")
def prefer(a: Candidate, b: Candidate) -> Literal["a", "b", "tie"] | tuple[str, float | None]: ...
```

Any exception raised by a component is caught and converted:

| Component | Becomes |
|---|---|
| scorer | `Score(None, error="<type>: <message>")` |
| gate | `GateResult(False, reason="error: ...")` plus a decision-trace entry: an error rejects, visibly |
| pairwise | `"tie"`, with the error recorded |
| generator | the candidate is skipped with a `generate_error` entry; the run goes on |

A scorer value or confidence outside 0..1, `NaN`, a `bool` or an unusable return type also becomes
`Score(None, error=...)`.

Any other callable is accepted too; it is read with the scorer shape of §7.7. A component whose callable
takes a `trace` keyword is called with `trace=current_trace()` (§7.1).

### 4.2 Prompt scorers

Plain-English criteria over a `DecisionClient` (§7.2):

```python
PromptScorer(name, criteria: str | list[str], judge: DecisionClient | str, *, output="score",
             scale=(1, 5), anchors=None, field=None, images_from=None, cost=5, version="1")
             # images_from: str | Sequence[str] | None
PromptGate(name, criteria, judge, *, threshold=0.5, field=None, cost=1, version="1")
PromptPairwise(name, criteria, judge, *, field=None, images_from=None, cost=10, version="1")
```

- `judge` is a client object or a name resolved from `Engine(judges=...)` or a `[judges.<name>]` config
  section (§7.8).
- The judged state is `candidate.data[field]`, or the whole `data` when `field` is `None` (JSON-dumped when
  not a string). `images_from` names file keys sent as images: one key sends `candidate.files[key]`, a
  sequence sends `[candidate.files[k] for k in keys]` in that order (a reference that is the same for
  every candidate goes into each candidate's `files`; the criteria say which image is which). A missing
  key is an error of that call (`Score(None, error=...)`). Design changes
  [0005](changes/0005-prompt-scorer-with-several-images.md) and [0007](changes/0007-pairwise-over-images.md).
- `PromptScorer` asks one `score` question (or `yes_no` with `output="yes_no"`). A list of criteria is a
  checklist: one question per item, the value is the mean of the answered items, `details` holds each.
- `PromptGate` asks one `yes_no` question and passes when the probability of "yes" reaches `threshold`.
- `PromptPairwise` asks one `choice` question with options `["A", "B", "tie"]` per call. The engine always
  asks both orders, (A, B) and (B, A); a win counts only when both orders agree, otherwise it is a tie.
  With `images_from` it sends A's images, then B's; a file both share (the same path) is sent once, first.

### 4.3 Command scorers

```python
CommandScorer(name, command: list[str], *, cost=20, timeout_s=60, version="1")
```

The executable runs once per candidate. It reads `{"candidate": {"id", "data", "files", "meta"}}` on stdin
and writes `{"value": 0.9, "reason": "...", "confidence": 0.8, "details": {}}` on stdout. A non-zero exit,
bad JSON or a timeout gives `Score(None, error=<stderr tail>)`.

### 4.4 Normalizers

`linear(lo, hi)`, `inverse(lo, hi)`, `sigmoid(mid, k)` and `from_1_5` map raw measurements to 0..1.

## 5. The engine

### 5.1 Engine API

```python
engine = Engine(
    config: SelectionConfig | str | Path,       # object, TOML path, or TOML text
    *,
    registry: Iterable[Callable] = (),          # components (§4)
    judges: Mapping[str, DecisionClient] = {},  # names used by prompt scorers
    text_client: TextClient | None = None,      # accepted; unused in v0.1
    embedder: Embedder | None = None,           # needed for dedup method "embedding"
    sink: RecordSink | None = None,             # default: the SQLite span store (§8)
    cache: ScoreCache | "default" | None = "default",   # "default": SQLite cache next to the store; None: off
)
engine.run(task, *, trace=None, seed=None) -> Result          # generate, score, select
engine.select(candidates, *, trace=None) -> Result           # score and select candidates you already have
engine.score(candidates, *, trace=None) -> list[Scored]       # the same, returning only result.ranked
engine.explain(result) -> str                                 # human-readable decision trace
engine.config                                                 # the validated SelectionConfig
engine.judges                                                 # name -> DecisionClient, including [judges.*]
```

A string that is a single line ending in `.toml` is a path; anything else is TOML text. `run()` needs
exactly one registered generator; `select()` and `score()` need none (design change
[0006](changes/0006-score-returns-the-decision.md)).

Public import paths: `hone_select` (everything above, the decorators, types, normalizers, the errors
`HoneSelectError`, `ConfigError`, `BudgetExceeded`, `PortError`, `current_trace` and `PORTS_VERSION`),
`hone_select.ports` (§7), `hone_select.testing` (fakes and contract checkers, §7.9),
`hone_select.cache.SqliteScoreCache`, `hone_select.explain.explain_run`, and the adapters (§9.1).

### 5.2 The pipeline

```
run(task):
  root span hone.select.run
  for each variation (§5.10):                  # first_above: one at a time, may stop early
      stop if the budget is used up (§5.9)
      candidate = generator(task, variation)   # span hone.select.generate
      first_above: score it fully; stop when it reaches the threshold and is not rejected
  dedup (§5.11)                                # removed ones recorded
  gates, cheapest first (§5.3)
  cascade stages with keep_top (§5.4)          # cache lookup before each score (§5.12)
  total = aggregate(scores) (§5.5)
  rank (§5.6); select, escalate ties (§5.7); fallback when all are rejected (§5.8)
  decision span; return Result
```

Every step appends an entry to `result.decision` and writes a span.

### 5.3 Gates

Gates run in the order of their declared cost, cheapest first, and **stop at the first failure**: the
candidate is rejected and the reason recorded. A gate that raises rejects too, with the error in the
trace. Rejected candidates are still scored when the fallback (§5.8) may need them.

### 5.4 Cascade

`[score] cascade` is a list of stages; each stage names scorers and an optional `keep_top`. A stage runs
its scorers on the candidates still in play, computes partial totals, and keeps the best `keep_top` for
the next stage; ties at the cut are kept. `Scored.stage_reached` is the last stage a candidate took part
in. Put cheap scorers first, so expensive ones see only the finalists.

### 5.5 Aggregation and missing scores

`[score] aggregate` combines one candidate's scores into `total`. Scorers not in `weights` get weight 1.

| Aggregator | Total |
|---|---|
| `weighted_mean` (default) | Σ wᵢvᵢ / Σ wᵢ |
| `min` | the lowest value |
| `geometric` | weighted geometric mean, values clamped to ≥ 1e-6 |
| `weighted_mean_with_floor` | weighted mean, capped at `floor` if any value is below `floor` |

`missing` says what a `None` score does: `renormalize` (default) leaves it out, `zero` counts it as 0,
`reject` rejects the candidate with the reason recorded. A candidate with no values has `total=None`.

### 5.6 Ranking

Candidates are ranked by:

1. non-rejected before rejected;
2. a total before no total;
3. a later cascade stage reached before an earlier one;
4. higher total;
5. generation order.

A candidate the cascade cut keeps only a partial total from the cheap scorers, which can be higher than a
finalist's full total. The cascade exists to eliminate, so a candidate it cut never outranks one that
went further (decision D-003 in [`decisions.md`](decisions.md)).

### 5.7 Selection policies and escalation

| Policy | Winner |
|---|---|
| `argmax` (default) | the top of the ranking |
| `first_above` | candidates are generated and fully scored one at a time; the first non-rejected one with `total ≥ threshold` wins and generation stops. If none reaches it, the best one wins and `threshold_not_met` is recorded |
| `pairwise_tournament` | king of the hill with the `pairwise` judge in ranking order: a challenger takes over only by winning in both orders |

With `argmax` and `escalate = "pairwise"`, two situations go to the pairwise judge:

- **a tie:** the top candidates are within `tie_margin` (default 0: only exact ties). The contenders are
  every non-rejected candidate within the margin of the top, at least the top two;
- **low confidence:** any score of the top two candidates has `confidence < min_confidence` (a `None`
  confidence never triggers it).

The contenders then play king of the hill as above. When the two orders disagree the result is a tie and
the original ranking stands. Every comparison is recorded.

**Position bias** (design change [0003](changes/0003-name-a-position-biased-pairwise-judge.md)): when both
orders chose the same position (the first-shown candidate both times, or the second-shown both times), the
`pairwise` entry gets `reason: "position_bias"`. When every comparison of a run was like that, one warning
names the judge and its model (§5.13). `[select] max_biased_pairwise = k` (default: none, never skip)
stops asking the judge after `k` position-biased comparisons in a row: each further comparison is an
`escalation_skipped` entry (`reason: "position_bias"`) and counts as a tie, at no cost.

### 5.8 Fallbacks

When every candidate is rejected:

| `fallback` | Winner |
|---|---|
| `none` (default) | `None` |
| `best_rejected` | the best rejected candidate |
| `first_valid` | the first rejected candidate, in generation order, that has a total |

A fallback winner has `rejected=True`, and the fallback is recorded.

### 5.9 Budgets

`[budget]` sets `max_cost`, `max_seconds` and `max_money_usd`. Every component has a `cost`. The budget
is checked before each generation and before each cascade stage after the first; it is a stop condition,
so a run may overshoot by one step. When it runs out, generation stops and selection goes on among the
candidates that exist. The stop is recorded, and `BudgetExceeded` never reaches the caller of `run()`. A
generator may raise `BudgetExceeded` itself (for example a provider's spending cap); a scorer or gate that
raises it is treated like any other component error. `result.budget` reports what was used.

### 5.10 Variations and determinism

`[generate] vary` builds one variation per candidate: `seed = "increment"` gives base seed + index (the
base comes from `run(task, seed=...)`), a list of seeds is cycled, and every other key is a list cycled
independently (no cartesian product). The candidate's `meta` records generator, index, seed, params and
seconds, overlaid by the generator's own meta. Generation is sequential; `max_concurrency` is accepted,
but only 1 is used in v0.1. With the same config, seed and deterministic components, runs give identical
rankings and totals.

### 5.11 Dedup

`[dedup] method`: `exact` (default) drops candidates with the same `Candidate.id`; `embedding` drops
candidates whose cosine similarity to an earlier one is at least `threshold` (default 0.95), using the
`Embedder` port (§7.4); `off` keeps everything. Removed candidates are recorded. If the embedder fails,
dedup falls back to exact and records why.

### 5.12 Score cache

Scores are cached by `(candidate id, scorer name, scorer version, judge model)` through the `ScoreCache`
port (§7.6). The default is a SQLite file, `cache.db`, next to the SQLite span store (else
`$HONE_HOME/select/cache.db`). Only scores with a value are cached, so failures are retried on the next
run. Bumping a scorer's `version` recomputes only that scorer. Cache hits are recorded on the score span.

### 5.13 Warnings

Logged and added to `result.decision`:

- **Goodhart risk:** `n` is more than 10 times the number of independent scorers;
- **self-judging:** a judge's model shares a family prefix with the generator's model
  (`candidate.meta["model"]`), for example both start with `gemma`;
- **no values:** every score is `None`;
- **position bias:** the pairwise judge chose by position in every comparison of the run (§5.7).

## 6. Configuration

`selection.toml` maps one to one onto `SelectionConfig` (pydantic, unknown keys rejected). Every section
is optional. The full reference with every key is in [`../docs/config.md`](../docs/config.md).

| Section | Keys |
|---|---|
| `[judges.<name>]` | `client` (an entry-point name, §7.8); other keys go to the factory |
| `[generate]` | `n` (default 4), `vary`, `max_concurrency` |
| `[dedup]` | `method`, `threshold` |
| `[score]` | `gates`, `cascade`, `weights`, `aggregate`, `floor`, `missing` |
| `[scorers.<name>]` | `kind = "prompt"` (the `PromptScorer` options) or `kind = "command"` (the `CommandScorer` options): scorers without code |
| `[select]` | `policy`, `threshold`, `tie_margin`, `min_confidence`, `escalate`, `pairwise`, `max_biased_pairwise`, `fallback` |
| `[budget]` | `max_cost`, `max_seconds`, `max_money_usd` |
| `[record]` | `sink` (`sqlite`, `jsonl`, `none`), `path`, `capture_content` |

Validation raises `ConfigError` with a message that says what to change: an unknown scorer or gate in the
cascade, a weight for an unknown scorer, an unknown policy, `first_above` without a `threshold`,
`escalate = "pairwise"` or `pairwise_tournament` without a pairwise judge, dedup by embedding without an
embedder, an unknown judge client, two components with the same name.

## 7. Ports

hone-select owns these interfaces. They are `typing.Protocol`s in `hone_select.ports`; any object with
the right shape fits, and nothing in hone-select imports an implementation. `PORTS_VERSION = "1"`.

Rules for everything that crosses a port:

- payloads are plain JSON-compatible data, or objects exposing the listed attributes; both a `Mapping`
  and an attribute-style object are accepted (`hone_select.ports.get`);
- unknown extra keys are ignored; missing optional keys take the listed default;
- hone-select catches `Exception` at every port call, records it and converts it to its own failure value
  (for example `Score(None, error=...)`).

### 7.1 Trace context

```python
TraceContext = Mapping[str, str]
# "traceparent":       W3C trace context, "00-<32 hex trace id>-<16 hex parent span id>-01"
# "hone.candidate_id": the candidate a call is about
# "hone.scorer":       the component making the call
# other keys (for example "hone.run_id", "hone.step", "hone.lens.finding_id") are passed through
```

- `run()` and `score()` accept `trace=`. The spans of the run then use the caller's trace id, and the root
  span's parent is the caller's span.
- Without an incoming context, a run starts a new trace (random 128-bit id).
- `current_trace()` returns the context of the current span, so a generator can pass it to its own model
  calls.
- A component whose callable takes a `trace` keyword is called with `trace=current_trace()`: the context
  of its own gate, score, pairwise or generate span. Plain `fn(candidate)` callables are called without it.
  Prompt scorers pass the same context to their judge.

### 7.2 `DecisionClient`

Structured questions about a state. Used by the prompt scorers (§4.2).

```python
class DecisionClient(Protocol):
    def decide(
        self,
        state: str | Mapping[str, Any],            # the context being judged
        questions: Mapping[str, Question],         # name -> question
        *,
        images: Sequence[str] = (),                # file paths
        trace: TraceContext | None = None,
    ) -> Mapping[str, Answer]: ...                 # same keys as questions
```

`Question` (Mapping):

| Key | Required | Meaning |
|---|---|---|
| `type` | yes | `"yes_no"`, `"choice"` or `"score"` |
| `instructions` | yes | the plain-English criterion or question |
| `options` | for `choice` | option labels |
| `scale` | for `score` | `[low, high]`, default `[1, 5]` |
| `anchors` | no | `{"1": "flat", "3": "competent", "5": "instantly singable"}` |

`Answer` (Mapping or attribute object):

| Key | Type | Meaning |
|---|---|---|
| `type` | `str` | the question type |
| `value` | `float \| None` | `yes_no`: probability of "yes"; `score`: normalized 0..1, (raw − low) / (high − low); `choice`: probability of the chosen option |
| `choice` | `str \| None` | the chosen option (`choice` only) |
| `probabilities` | `Mapping[str, float] \| None` | per option, or `{"yes": p, "no": 1 - p}` |
| `raw` | `float \| None` | the raw score on the question's scale (`score` only) |
| `confidence` | `float \| None` | 0..1, if the client can estimate it |
| `calibrated` | `bool` | `True` only when probabilities come from a calibrated source (logprobs, a decision model) |
| `rationale` | `str \| None` | an explanation, if the client gives one |
| `error` | `str \| None` | set when the question could not be answered; `value` is then `None` |

A key missing from the returned mapping means "not answered", like `error`. The client's `model_id` (or
`model`) attribute, when present, is the judge model used for the cache key and the self-judging warning.

### 7.3 `TextClient`

Prompt in, text or a structured object out. The adapters (§9.1) build their `DecisionClient` on top of it.

```python
class TextClient(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],     # [{"role": ..., "content": str | list[part]}]
        *,
        schema: Mapping[str, Any] | None = None,   # JSON Schema; result.parsed is filled or error set
        trace: TraceContext | None = None,
        **params: Any,                             # temperature, seed, max_tokens, ... (unknown ignored)
    ) -> TextResult: ...
```

`TextResult` has `text: str`, `parsed: Any | None`, `error: str | None` (set for failed calls or failed
parsing; model-quality problems never raise), `model: str`, `finish_reason: str | None`,
`usage: Mapping[str, int]` and `span_id: str | None`. Image parts are
`{"type": "image", "path": str}` or `{"type": "image", "data_b64": str, "mime": str}`. Transport and
configuration errors raise the provider's own exception.

### 7.4 `Embedder`

```python
class Embedder(Protocol):
    model_id: str
    dimensions: int
    def embed(self, texts: Sequence[str], *, trace: TraceContext | None = None) -> list[list[float]]: ...
```

Vectors are L2-normalized, in input order; empty input returns `[]`. Used by embedding dedup (§5.11).

### 7.5 `RecordSink`

```python
class RecordSink(Protocol):
    def emit(self, span: Mapping[str, Any]) -> None: ...    # a span as in §8.1
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

Shipped: `SqliteSpanSink` (default), `JsonlSpanSink`, `NullSink`, and `MemorySink` for tests. A sink never
raises into the run: failures are logged to stderr once and counted.

### 7.6 `ScoreCache`

```python
class ScoreCache(Protocol):
    def get(self, key: tuple[str, str, str, str]) -> Score | None: ...   # (candidate id, scorer, version, judge model)
    def put(self, key: tuple[str, str, str, str], score: Score) -> None: ...
```

Shipped: `hone_select.cache.SqliteScoreCache(path)`.

### 7.7 Scorer shape

hone-select accepts as a scorer any callable:

```python
def scorer(candidate, *, trace=None) -> ScoreLike: ...    # the trace keyword is optional (§7.1)
```

- `candidate` exposes `id: str`, `data: Any`, `files: Mapping[str, str]`, `meta: Mapping[str, Any]`.
- `ScoreLike` is a float, `None`, or a Mapping or attribute object with `value: float | None` (0..1, higher
  is better), `confidence: float | None = None`, `reason: str = ""`, `details: Mapping = {}`,
  `error: str = ""`.
- The callable may expose `name`, `kind` (`"scorer"`, `"gate"` or `"pairwise"`), `cost`, `version` and
  `judge_model`; hone-select reads them when present and uses the scorer defaults otherwise.
- Gates use the same shape and return a bool or a `GateLike`: `passed: bool`,
  `probability: float | None = None`, `reason: str = ""`, `details: Mapping = {}`.

This is the shape a package of ready-made scorers implements to plug into hone-select without importing
it.

### 7.8 Judges from config

`[judges.<name>] client = "<entry point>"` resolves `client` by exact name in the entry-point group
`hone.decision_clients` and calls the factory with the other keys of the table (for example
`model = "..."`). An unknown name raises `ConfigError` listing the available names. hone-select registers
one entry point itself, `hone_models:decision`, which imports the `hone_models` package lazily (extra
`models`). Config never imports arbitrary `module:attr` paths.

### 7.9 Fakes and contract checkers

`hone_select.testing` exports deterministic, scriptable fakes that record every call (`fake.calls`):
`FakeDecisionClient`, `FakeTextClient`, `FakeEmbedder` (similarity only where scripted), `FakeMachineProbe`
(scripted snapshots and a live list of loaded models, §7.10), `FakeModelGuides` (scripted guides, §7.11)
and `MemorySink`. It also exports the contract checkers `check_decision_client`, `check_text_client`,
`check_embedder`, `check_record_sink`, `check_machine_probe` and `check_model_guides`. The fakes pass them. A provider of a port runs the same checkers against its real
implementation.

### 7.10 `MachineProbe`

The machine's model state for an experiment's run conditions (design change
[0010](changes/0010-run-conditions.md) §6), provided by hone-models (`hone_models.machine.Machine`):
`snapshot()` returns the GPUs (`None` when no reader answered), the model servers (`running`: True, False or
None), the loaded models (`server`, `name`, registry `model_id`, `size_gb`, `vram_gb`), the GPU lock and the
leases; `prepare(needed, *, if_busy="block")` unloads every model that is not needed (never loads one, never
waits; with `if_busy="block"` it unloads nothing while another process holds a lease or the lock) and returns
`blocked_by`, `unloaded`, `errors`, `missing`, `loaded_models` and `need_gb`. An optional `load(model_id)`
(detected with `hasattr`) warms a model up. Every key is optional; an unknown value is `None`, never 0.
`[conditions] probe = "<name>"` resolves by exact name in the entry-point group `hone.machine_probes`; the
factory is called with no arguments. hone-select registers `hone_models:machine`, which imports
`hone_models.machine` lazily (extra `models`) and raises `ConfigError` naming the extra when it is missing.

### 7.11 `ModelGuides`

What each model can take, for model-aware experiments (design change
[0011](changes/0011-model-aware-experiments.md) §5), provided by hone-models (`mk.guide`):
`guide(model_id) -> Mapping | None` returns the guide as JSON (`id`, `kind`, `summary`, `prompt`,
`inputs`, `features` with `name`, `how`, `input`, `examples`, `source`; `source`, `checked`, `license`,
`commercial_use`, `sizes`, `durations_s`, `max_duration_s`, `max_references`, `installed` (`yes` / `no` /
`unknown`), `install`), or `None` for a model the source does not know. Every key is optional.
`[generate] guides = "<name>"` resolves by exact name in the entry-point group `hone.model_guides`, else as
a `module:factory` (called with no arguments); it defaults to `hone_models:guides` when the client is a
`hone_models:*` factory. hone-select registers `hone_models:guides`, which imports `hone_models` lazily
(extra `models`) and raises `ConfigError` naming the extra when it is missing (design/decisions.md D-020).

## 8. Records

### 8.1 Span format

Every run writes spans in an OpenTelemetry-shaped JSON format:

```json
{
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7",
  "parent_span_id": "a3ce929d0e0e4736", "name": "hone.select.score", "kind": "internal",
  "start_time": "2026-09-27T14:03:11.120Z", "end_time": "2026-09-27T14:03:13.402Z",
  "status": {"code": "ok", "message": ""},
  "attributes": {"hone.schema_version": "1", "...": "..."},
  "events": [], "resource": {"hone.package": "hone-select", "hone.package.version": "0.1.0"}, "links": []
}
```

Ids are lowercase hex (32 for traces, 16 for spans); a root span has `parent_span_id: null`. Times are
ISO-8601 UTC with milliseconds. Values over 64 KiB are stored in a `blobs` table and referenced as
`{"$blob": "<sha256>"}`.

### 8.2 The store

The default store is SQLite at `$HONE_HOME/select/spans.db` (`HONE_HOME` defaults to `.hone` in the current
folder), with the tables `meta` (schema name and version), `spans`, `blobs` and `changes` (a change log
for incremental readers). It uses WAL mode with a busy timeout, so several processes can write to it at
once. `[record] sink = "jsonl"` writes one span per line instead (large values stay inline), and
`"none"` records nothing.

Secrets are never recorded: values that look like API keys are scrubbed, and in the recorded configuration
(`hone.select.config`) the values of keys named like a key, token, secret, password, authorization or
credential are replaced with `***`. Content capture (candidate data,
reasons, the decision trace) is on by default; `capture_content = false` or `HONE_CAPTURE_CONTENT=0`
stores hashes and lengths instead.

### 8.3 Spans and attributes

Every span has `hone.schema_version = "1"` and copies from the trace context `hone.run_id`, `hone.item`,
`hone.step`, `hone.candidate_id`, `hone.scorer` and `hone.lens.finding_id` when present.

| Span | Attributes |
|---|---|
| `hone.select.run` (root) | `hone.select.config_hash`, `hone.select.config` (the validated configuration, JSON), `hone.select.task` (the task's first 2,000 characters; content), `hone.select.policy`, `hone.select.n`, `hone.select.budget.cost_used`, `hone.select.budget.seconds_used`, `hone.select.budget.money_used` |
| `hone.select.generate` | `hone.select.candidate` (JSON `{id, meta, data_preview, data_sha256}`) |
| `hone.select.gate` | `hone.select.gate`, `hone.select.gate.passed`, `hone.select.gate.probability`, `hone.select.gate.details` (only when the gate returned details; content, hashed when capture is off) |
| `hone.select.score` | `hone.select.scorer`, `hone.select.scorer_version`, `hone.select.cache_hit`, `hone.select.score.value`, `hone.select.score.confidence`, `hone.select.score.reason`, `hone.select.score.error`, `hone.select.image_keys` (only for a judge with `images_from`) |
| `hone.select.pairwise` | `hone.select.pairwise.a`, `hone.select.pairwise.b`, `hone.select.pairwise.choice`, `hone.select.image_keys` (only for a judge with `images_from`) |
| `hone.select.decision` | `hone.select.winner_id`, `hone.select.ranked` (JSON ids and totals), `hone.select.decision_trace` (JSON), `hone.select.fallback_used`, `hone.select.escalated` |

### 8.4 Explaining a run

The decision span holds the full ranked table and the decision trace, so a result can be re-explained
later from the store alone: `engine.explain(result)`, `hone_select.explain.explain_run(db_path, run_id)`
and `hone-select explain <run_id>` give the same text.

## 9. Adapters and CLI

### 9.1 Adapters (optional extras)

| Extra | Module | Provides |
|---|---|---|
| `openai` | `hone_select.adapters.openai` | `OpenAITextClient`, `OpenAIDecisionClient` over an OpenAI SDK client (also any OpenAI-compatible server, such as Ollama's `/v1`) |
| `langchain` | `hone_select.adapters.langchain` | `LangChainTextClient`, `LangChainDecisionClient` over any LangChain chat model |
| `models` | `hone_select.adapters.hone_models` | the `hone_models:decision` judge entry point (§7.8), the `hone_models:machine` probe (§7.10) and the `hone_models:guides` guide source (§7.11); generate subjects reach `hone_models:image` / `:music` / `:video` through hone-models' own entry points |

The decision clients are emulated over a text client: all questions go into one prompt that asks for
`{name: {"rationale", "answer"}}` (through `response_format` JSON schema with OpenAI, through an
instruction with LangChain). Replies are parsed plain, fenced, or as the outermost `{...}`. Emulated
answers are `calibrated=False`; an answer outside the scale or options gives `value=None` with an error.
`OpenAIDecisionClient` uses `temperature=0` by default (`temperature=None` leaves it out, for models that
reject it). Transport errors are the SDK's own exceptions.

Adapters are imported lazily; the core never imports them.

### 9.2 CLI (extra `cli`)

```
hone-select run CONFIG --task task.json --registry MODULE [--seed N] [--json]
hone-select explain RUN_ID [--db PATH]
hone-select show RUN_ID [--db PATH] [--json]
hone-select dashboard [--db PATH] [--project PATH] [--host 127.0.0.1] [--port 8788] [--open]
hone-select experiments new TITLE | plan EID [--pilot] | approve EID [--note] | deny EID --note
hone-select experiments start EID | stop EID | status [EID] | list | report EID [--include-outside]
                                                                                     (all: [--project PATH])
```

`run` uses the top-level components of the registry module; `explain` rebuilds the decision from the
store; `show` prints every span of the run's trace. Errors print one `error: ...` line and exit 1.
`python -m hone_select.cli` is the same command.

`dashboard` serves a read-only web page over the store (design change
[0008](changes/0008-dashboard.md)): the runs (filterable, sortable, with their trace context), one run
(configuration, task, budget, the candidate table with variation params, gates, one column per scorer,
total, rank and winner, every reason on click, pairwise judgements, decision trace) and all candidates
across runs with a group-by on any variation param (candidates, wins, win rate, mean total). The server is
the standard library's `http.server` on localhost; `hone_select.dashboard.list_runs`, `run_detail` and
`all_candidates` return the same data as JSON-ready values. `GET /api/info` returns `store` (the span
store path), `store_exists`, `project` (the experiments folder's project, or null) and `version`, for the
page's sidebar. The page's layout is design/decisions.md D-012.

## 10. Acceptance cases

The behaviour hone-select guarantees. Each case has a test in `tests/e2e/test_ac<N>_*.py` (AC-20 in
`tests/gpu/`).

| AC | Scenario | Expected |
|---|---|---|
| AC-1 | README quickstart | runs; the winner is the shortest non-rejected candidate; `result.decision` lists generated, gated, scored, selected |
| AC-2 | A scorer raises for one candidate | that score is `None` with the error; aggregation renormalizes; the run completes |
| AC-3 | `missing="reject"` | the candidate with the failed scorer is rejected, with the reason recorded |
| AC-4 | All candidates fail a gate, `fallback="best_rejected"` | the winner is the best rejected one, `winner.rejected=True`, fallback in the trace; with `fallback="none"` the winner is `None` |
| AC-5 | Cascade with `keep_top=2` and a costly stage-2 scorer | the stage-2 scorer is called exactly twice; the others have `stage_reached=1` |
| AC-6 | `first_above`, threshold met by candidate 2 of `n=5` | only 2 are generated; candidate 2 wins |
| AC-7 | Top two within `tie_margin`, escalate to pairwise | the pairwise judge is called in both orders and decides; if the orders disagree, the original ranking is kept and recorded; a judge that chose by position gets `reason: "position_bias"` and, when it did so every time, one warning; `max_biased_pairwise` stops further calls (`escalation_skipped`) |
| AC-8 | Low-confidence deciding score (`min_confidence`) | escalation even when the margin is large |
| AC-9 | `PromptScorer`, `PromptGate`, `PromptPairwise` with `FakeDecisionClient` | correct question payloads (`fake.calls`); answers normalized; checklist mean correct; `images_from` with several keys sends the images in order (also from config), a missing key gives `Score(None, error)`; `PromptPairwise(images_from=...)` sends A's then B's images in both orders, a shared file once; the image keys are on the spans |
| AC-10 | `CommandScorer` with a small Python script, and a failing one | scores parsed; failure gives `Score(None, error)` |
| AC-11 | `max_cost` reached during generation | generation stops, selection among existing candidates, `BudgetExceeded` not raised, stop in the trace |
| AC-12 | Score cache | a second identical run calls no scorer (hits recorded); bumping a scorer's `version` recomputes only that scorer |
| AC-13 | Records | spans in `.hone/select/spans.db` with the attribute names of §8.3; an incoming `traceparent` sets the trace id; `capture_content=false` stores hashes only |
| AC-14 | Determinism | same config and seed give identical ranking and totals |
| AC-15 | Dedup, exact and embedding (scripted `FakeEmbedder`) | duplicates removed and recorded |
| AC-16 | Config validation | unknown scorer in the cascade, weight for an unknown scorer, bad policy: `ConfigError` with a clear message |
| AC-17 | Self-judging | judge model family equal to the generator's gives a warning in the trace |
| AC-18 | CLI | `hone-select run` on an example config prints the winner; `--json` schema; `explain` rebuilds from the store |
| AC-19 | OpenAI adapter with recorded HTTP fixtures | the contract checker passes; the emulated decision client parses JSON answers |
| AC-20 (real model) | `PromptScorer` and `PromptPairwise` through `OpenAIDecisionClient` on a local model behind an OpenAI-compatible endpoint | the contract checker passes on the real model; the clearly better of three candidates wins |
| AC-21 | Examples | every `examples/*.py` runs offline, opens with a What / How / Why docstring and is listed in `examples/README.md` |
| AC-22 | `Engine.select` on existing candidates | returns a `Result`: winner, ranking, the decision trace (dedup with `duplicate_of`, the embedder-failure warning), `run_id` that `explain_run` finds, budget; `score()` equals `select().ranked` |
| AC-23 | A gate returns `details` (a `GateResult` or a GateLike mapping) | kept in `Scored.gates[name].details`, also for rejected candidates; recorded as `hone.select.gate.details` on the gate span, hashed when content capture is off |
| AC-24 | The dashboard over a store with two runs in one trace | lists both runs newest first with policy, n, candidates, winner and trace context; a run shows its configuration, task and every candidate with variation params, gate results and scores; candidates across runs carry their params; the task is hashed when content capture is off; the server answers `/`, `/api/info` (store, store_exists, project, version), `/api/runs`, `/api/runs/<id>`, `/api/candidates` and 404s unknown paths |
| AC-25 | An experiment's lifecycle | `new` creates `experiments/E000N-slug/` with a valid template; `plan` expands every setup (full / one_at_a_time / list, baselines first), counts outputs, shows the exact commands, estimates only with `--pilot`; a person approves or denies a plan for one definition hash; `start` refuses anything but an approved current plan; an edited definition is a draft again; the CLI does all of it |
| AC-26 | Experiment subjects | prompt, python and command subjects; every sample records data, files, seconds, peak memory, exit code, log; an exception, a non-zero exit, a timeout, a missing program or non-JSON output is a recorded error, never a crash; `TransientError` / exit 75 is retried |
| AC-27 | Running and results | a stopped run resumes without redoing samples; the budget stops it; each case is a selection recorded with `hone.run_id = EID` and `hone.item = case`; results per setup, factor level and baseline find the known best setup, with intervals, wins and pass rates; `keep_files` and scorer agreement as declared |
| AC-28 | Experiment cases | from `cases.toml` fields, from one folder of files per case, or from an earlier experiment's winners or all outputs (its data and files as inputs); duplicate or missing cases are errors |
| AC-30 | Run conditions in the definition and the plan | `[conditions]` is validated (unknown keys, bad values); `only_needed_models` / `models_on_gpu` without a probe, `min_free_vram_gb` for a prompt subject without a probe, `gpu_lock` with a `gpu-lock.sh` wrap and unresolvable `models` placeholders are `ConfigError`s that say what to change; `plan.json` shows the declared conditions, the needed models per setup and the current reading with `ok` / `outside` / `unknown` per check and what the run would do; `plan` unloads nothing; editing a condition makes the experiment a draft; a pilot on a busy machine is refused |
| AC-31 | Waiting and stopping | a busy reading before a sample: `run.json` is `waiting` with the reasons and `status` reports `waiting`; `start` refuses a waiting experiment; a good reading continues the run; `STOP` ends a wait; `wait_timeout` stops the run with `stopped_because`; `on_violation = "stop"` stops at once; `record_only` runs and marks; `start` resumes a stopped run; waiting time is in no sample's seconds or the budget |
| AC-32 | The environment and the results | every sample's `result.json` has `environment` with before / after readings, checks and status, also without `[conditions]` (`not_checked`); a sample outside after it ran is set aside as `outside-1.json` and run once more, a second outside run is kept and marked; `outside` and `unknown` samples are left out of every number and of the selection, counted per setup and factor level with reasons; `report --include-outside` counts them and says so; old `result.json` files without an environment count as before |
| AC-33 | Needed models, the probe and unknowns | `prepare` gets the setup's model for a prompt subject, the declared `models` or nothing for python / command subjects, and unloads the previous group's model; `prepare`'s answer is in the environment; `blocked_by` makes the run wait, `if_busy = "unload"` unloads anyway; an unload error is `outside` (`unknown` for a server that did not answer); `missing` marks the sample `cold` unless `warm_up` loads it; `min_free_vram_gb` does not count the needed models; `models_on_gpu` marks a partly offloaded model; `gpus: None` falls back to `nvidia-smi`, then `unknown`; a server with `running: None` and a raising probe are `unknown`; a declared check that cannot be measured refuses `start`; `probe = "hone_models:machine"` without hone-models is a `ConfigError` naming the extra |
| AC-34 | The GPU lock | with `gpu_lock`, no other process takes the lock during the run (samples, waits and scoring) and one can right after; the holder file is written and removed; a lock held elsewhere makes the run wait and stop at `wait_timeout`; with `HONE_GPU_LOCK_HELD=1` the lock is not taken again; subjects receive `HONE_GPU_LOCK_HELD=1`; a killed run releases the lock |
| AC-35 | Run conditions in the dashboard | the list shows the `waiting` status and reason; an experiment shows the run-conditions card with the plan's reading before approval and the waits; each sample carries its environment; outside samples are marked and the results say how many were not counted |
| AC-36 | A generate subject (design change 0011) | each sample calls `generate` with its seed, `out` in the workdir and the filled inputs (a case file as a `Path`, a one-placeholder input keeps its type); the candidate has the files and the measurements (`elapsed_s`, `cost_usd`, `cost_estimated`), license and `commercial_use` in its meta; `refused` is a failed sample with its `error_kind`; `out_of_memory` with conditions is `outside` and runs once more, without conditions a failure; one client session per model group; a pilot ends its session |
| AC-37 | Per-model overrides | `[generate.per_model]`, a case's `per_model` and `prompts/<model>/<file>` give each model its own prompt and inputs, the others the shared ones; `plan.json` `asked` shows the resolved prompt and inputs per setup and marks setups asked differently, as do the results and the summary; judges and scorers see only the case's shared fields (`judge_view`); an override naming no factor or case field, or a model the experiment does not run, is a `ConfigError` at plan time |
| AC-38 | Needs | cells whose model lacks a needed feature or limit (a case's need or a factor value) are not applicable: listed with the unmet need in the plan and the CLI, not run, not failures, not in `outputs`; an undeclared limit runs and is marked `need_unknown`; per-setup numbers are over applicable cases with the count; baseline differences, wins and losses and factor levels use only shared cases and say how many; the summary lists what each model could not do; without a guide source every need is `need_unknown` |
| AC-39 | Model guides | `plan.json` `models` stores each model's guide, installed state, install command and license, and the dashboard shows them; `{model_guide}` (of `target_model`, else `model`) and `ctx.model_guide` get the stored guide; results carry license and `commercial_use` and the summary marks a non-commercial model without removing it; a model reported not installed makes `start` refuse, naming the install command, until the source reports it installed; without hone-select[models] a declared `hone_models:guides` is a `ConfigError` naming the extra; `FakeModelGuides` passes `check_model_guides` |
| AC-29 | Ratings and the Experiments page | human criteria are rated blind in a fixed random order and join the results; the dashboard lists and shows experiments, approves or denies them, takes ratings and serves their files; writes without the page's header or from another origin, and paths outside `cases/` and `outputs/`, are refused |
| AC-40 | A/B pairs (design change 0012): `between = "top"`, `top = 2`, `pairs = 6` over 3 cases, after a run | `ab_plan.json` holds 6 pairs, each the same case from the two best setups, 2 per case, left and right seeded; failed and outside samples never appear; the API serves the next pair without setup names; before the run the pairs wait; unknown names in `between` fail at plan time |
| AC-41 | A/B picks through the dashboard API | left, right and tie are stored in `ab.jsonl` as specified; undo removes the last pick; a write without the page's header stores nothing; a bad choice, a tie with `allow_tie = false` or a second pick of a pair is refused |
| AC-42 | A/B results after 20 picks (14-5-1) | wins, losses and ties of each side, win rate 73.7 % with its Wilson interval, `clear`, `complete`, the summary line and the winner per case; an unfinished A/B shows `complete: false`; `experiments report` recomputes |

## 11. Not in v0.1

- **Out of scope:** orchestration (hone-select is one step), model clients (only adapters), evaluation
  benchmarks.
- **Later:** `panel_vote`, `usc` and `human` selectors; a refine loop (scorer feedback as hints for the
  next attempt); MinHash dedup; async and concurrent generation; calibrated answers from logprobs in the
  adapters.
- **Known limitations:** `text_client` is accepted but unused; only the top level of a text client's
  `parsed` reply is checked against the schema (the emulated decision client checks each answer);
  `PromptPairwise` asks one order per call, so both orders are guaranteed only through the engine.

## 12. Experiments

Design change [0009](changes/0009-experiments.md); user guide [docs/experiments.md](../docs/experiments.md).
An experiment is a folder `experiments/E000N-<slug>/` in a project: `experiment.toml` (title, question,
cases, samples, seed, registry, `[generate]` subject, `[factors]`, `[design]`, `[[baseline]]`, `[criteria]`,
`[judges.*]`, `[scorers.*]`, `[budget]`, `[run]`, `[conditions]`), `cases/`, `prompts/`, `scripts/`, and what hone-select
writes: `plan.json`, `review.json`, `run.json`, `outputs/`, `ratings.jsonl`, `ab_plan.json`, `ab.jsonl`,
`results/`.

- **Status** comes from the files: draft (no plan, or the definition changed since), proposed, approved /
  denied (the last decision on the current plan's definition hash), running / waiting / stopped / completed.
- **Subjects:** `prompt` (a `TextClient` from a `module:factory`), `python` (`function(case, setup, ctx)` in
  its own interpreter), `command` (any program, placeholders, JSON on stdin, `wait4` peak memory),
  `generate` (an image, music or video client, design change 0011). A failure is a result; the implicit
  gate `ran_ok` rejects it.
- **Secrets and costs:** `env` values may be `$VAR` references; logs, errors and the served definition are
  scrubbed (secret-named keys are `***`); an unreported cost is `None`, never 0.
- **Running:** one run at a time (a live run or a completed experiment refuses `start`); samples in
  `run.order`, skipping done ones (resume), stopping at `STOP` or the budget; then one
  selection per case (`Engine.select`) with the criteria (`measure` normalized over the experiment, human
  and A/B criteria excluded), recorded in `.hone/select/spans.db` of the project.
- **Results:** per setup, per factor level and against each baseline: mean total with a 95 % bootstrap
  interval over cases, pass rate, errors, wins, criteria, measurements, money, human ratings; scorer
  agreement for `compare` pairs. `hone_select.experiments` is the Python API (`Project`, `start`, `stop`,
  `report`, `Ctx`, `TransientError`).
- **Run conditions** (design change [0010](changes/0010-run-conditions.md)): `[conditions]` declares the
  machine state an experiment needs (CPU busy share, free RAM, free VRAM not counting the needed models, GPU
  utilization, only the needed models loaded, the needed models fully on the GPU, the machine-wide GPU lock
  for the whole run). The check runs before the first sample, between samples and after the last:
  `after_group` hook, the probe's `prepare` for the next sample (and `load` with `warm_up`), a reading
  (`/proc`, `nvidia-smi` or the probe), a judgement `ok` / `outside` / `unknown` per condition. On a
  violation the run waits (`run.json` `waiting`, `waits`; STOP or `wait_timeout` end it), stops
  (`stopped_because`) or records only; a sample outside after it ran is set aside as `outside-1.json` and
  run once more. Every `result.json` has an `environment` (readings, checks, status, prepared, `cold`,
  attempt); outside and unknown samples are left out of every number and of the selection (scored apart
  for `report --include-outside`); a cold sample's `seconds` are left out. An unmeasurable declared check
  refuses `start`. `plan.json` gains `conditions` (declared, needed models per setup, a reading now and
  what the run would do). Samples' candidate meta carries `environment_status`. `start` and `Project.plan`
  take `sources=` (where the readings come from; tests pass fakes).
- **Model-aware experiments** (design change [0011](changes/0011-model-aware-experiments.md)): the
  `generate` subject calls `client(model).generate(prompt, out=<workdir>/<output>, seed=, timeout_s=, trace=,
  **inputs)` (clients through `hone.<kind>_clients` or a `module:factory`, D-020); the result's files are the
  candidate (`data = {"case": <judge view>, "files": [...]}`), `elapsed_s` and `cost_usd` its measurements;
  `error_kind` is kept and `out_of_memory` with conditions is `outside` (D-025); a client `session()` is held
  per model group. `[generate.per_model."<model>"]`, a case's `per_model` table and `prompts/<model>/<file>`
  ask a model differently; `plan.json` `asked` shows the resolved prompt and inputs per setup and marks it,
  as do the results, the summary and the dashboard; judges see only a case's shared fields (`judge_view`,
  D-021). A case's `needs` and the factors `duration_s` / `size` are checked against each model's guide at
  plan time (D-023): cells a model cannot do are not run and listed in `plan.json` `applicability`, a
  limit the guide does not declare is `need_unknown`. The guides (§7.11) are read once into `plan.json`
  `models` (with installed state, install command, license); `start` refuses while a model is reported not
  installed (D-024); `{model_guide}` and `ctx.model_guide` give subjects the guide (D-022). Results carry
  `applicable` counts per setup, baseline and factor comparisons on shared cases (D-026), `models` (license,
  `commercial_use`, non-commercial marked, never removed) and `could_not`.
- **A/B** (design change [0012](changes/0012-ab-judgement.md)): a `kind = "ab"` criterion (`question`,
  `between` = `"top"` | `"baseline"` | a list of setup ids, baseline or `[[setup]]` names, `top` = 2,
  `pairs` = 20, `allow_tie` = true; names checked at plan time, D-029) is judged by a person and is not part
  of the automatic total. Its pairs are the same case from two setups (the same sample index when both
  have it, never a failed or outside / unknown sample), spread evenly over the cases, left and right seeded
  by the experiment's seed (D-028); they are drawn when every case is scored, by the run's report or the
  first request, and fixed in `ab_plan.json` for the plan's definition hash (D-027). Picks are appended to
  `ab.jsonl` (`criterion`, `pair`, `case`, `left`, `right`, `choice`, `at`; an undo is `{"undo": <line>}`).
  The dashboard's A/B screen shows two outputs side by side with Left / Tie / Right (keys ←, T, →), the
  progress and undo; its API names a pair by its index and serves its files by index and side, never a
  setup (`GET /api/experiments/<eid>/ab/<criterion>[/<index>/<side>/<path>]`, `POST .../ab`,
  `POST .../ab/undo`, with the dashboard's write protections: its header, same origin, a loopback Host).
  `results.json` `ab` has, per criterion and pair of setups (more wins first), each side's wins, losses and
  ties, the win rate without ties with a 95 % Wilson interval, `clear` (the interval excludes 50 %),
  `judged` / `planned` / `requested`, `complete` and the winner per case; `summary.md` has one line per
  pair (D-030).
