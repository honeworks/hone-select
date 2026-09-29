# Records, trace context and the CLI

## The span store
*Example: [`records_and_explain.py`](../examples/records_and_explain.py).*

Every run writes spans (OpenTelemetry-shaped, the format shared by honeworks packages) to `$HONE_HOME/select/spans.db` (default `.hone/select/spans.db`),
one row per span, in SQLite WAL mode so several processes can write at once.

| Span | Main attributes (all spans also carry `hone.schema_version` and the shared trace keys) |
|---|---|
| `hone.select.run` | `hone.select.config_hash`, `hone.select.config` (the configuration), `hone.select.task` (task preview; content), `.policy`, `.n`, `hone.select.budget.*` |
| `hone.select.generate` | `hone.select.candidate` (id, index, seed, params, meta) |
| `hone.select.gate` | `hone.select.gate`, `hone.select.gate.passed`, `hone.select.gate.probability`, `hone.select.gate.details` (when the gate returned details; content) |
| `hone.select.score` | `hone.scorer`, `hone.select.scorer_version`, `hone.select.score.value`, `.confidence`, `.reason`, `.error`, `hone.select.cache_hit`, `hone.select.image_keys` (a prompt judge that sends images) |
| `hone.select.pairwise` | `hone.select.pairwise.a`, `.b`, `.choice`, `hone.select.image_keys` (a prompt judge that sends images) |
| `hone.select.decision` | `hone.select.ranked`, `hone.select.decision_trace`, `hone.select.winner_id`, `hone.select.escalated`, `hone.select.fallback_used` |

Values over 64 KiB go to a `blobs` table by sha256. Anything that looks like an API key or bearer token is
replaced with `***`, and so is every configuration value whose key is named like a key, token, secret,
password, authorization or credential (for example `api_key` in a `[judges.*]` section; a harmless name
such as `sort_key` is redacted too, on purpose). With `[record] capture_content = false` (or
`HONE_CAPTURE_CONTENT=0`), candidate text, reasons, error messages, the task and the prompt text in the
configuration (`criteria`, `anchors`) are stored as hashes only.

Other sinks: `sink = "jsonl"` (one JSON span per line) or `"none"`; or pass any `RecordSink`
(`emit`, `flush`, `close`) as `Engine(..., sink=...)`, such as `hone_select.testing.MemorySink`:

```python
from hone_select import Engine, generator, scorer
from hone_select.testing import MemorySink


@generator()
def say(task, v):
    return f"{task} {v['index']}"


@scorer()
def half(c):
    return 0.5


sink = MemorySink()
result = Engine("[score]\ncascade = [{ scorers = ['half'] }]", registry=[say, half], sink=sink).run("hi")
names = {span["name"] for span in sink.spans}
assert {"hone.select.run", "hone.select.generate", "hone.select.score", "hone.select.decision"} <= names
assert all(span["trace_id"] == result.trace_id for span in sink.spans)
```

## Trace context
*Examples: [`records_and_explain.py`](../examples/records_and_explain.py), [`other_packages_through_ports.py`](../examples/other_packages_through_ports.py).*

Pass a W3C `traceparent` to join a caller's trace; `result.trace_id` is then the caller's trace id. Inside
a run, `current_trace()` returns the context of the current span, so a generator can hand it to its own
model calls and their spans nest under the run.

```python
from hone_select import Engine, current_trace, generator

seen = []


@generator()
def traced(task, v):
    seen.append(current_trace()["traceparent"])
    return task


incoming = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
engine = Engine("[generate]\nn = 1", registry=[traced])
result = engine.run("x", trace=incoming)
assert result.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
assert seen[0].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
```

A gate, scorer, pairwise judge or generator that takes a `trace` keyword is called with
`trace=current_trace()` (the context of its own span), so a scorer from another package, such as
hone-taste's `for_select(...)`, records its spans in the selection's trace.

## Explaining a run
`engine.explain(result)` prints the ranking and the decision trace. The same text can be rebuilt later from
the store alone:

```python
from hone_select.explain import explain_run

print(explain_run(".hone/select/spans.db", result.run_id))
```

## The CLI
*Example: [`cli_run_and_explain.py`](../examples/cli_run_and_explain.py).*

`pip install "hone-select[cli]"`. See `examples/cli/` for a runnable setup. `python -m hone_select.cli ...`
runs the same command with a chosen interpreter.

```bash
hone-select run selection.toml --task task.json --registry shortest_line   # prints the winner
hone-select run selection.toml --task task.json --registry shortest_line --json --seed 3
hone-select explain <run_id> [--db .hone/select/spans.db]
hone-select show <run_id> [--db ...] [--json]                              # every span of the run
```

`--registry` names a module (importable from the current folder) whose top-level decorated functions and
scorer objects are registered. `--json` prints `run_id`, `trace_id`, `winner`, `ranked`, `decision` and
`budget`. Errors print one `error: ...` line and exit with status 1.

## The dashboard

`hone-select dashboard` (extra `cli`) serves a small read-only web page over the span store, on
`http://127.0.0.1:8788/` by default (`--port`, `--host`, `--db`, `--open`):

- **Runs:** every run, newest first, with its policy, n, candidates, rejected, winner and total,
  fallback / escalation, duration and the trace context it ran in (`hone.run_id`, `hone.item`, `hone.step`).
  Type in the box above the table to filter every column, or in a column's own box; click a header to sort.
- **A run:** what was tested (the configuration and the task), the budget used, and the candidate table:
  variation params as columns, each gate's result, one column per scorer, total, rank, stage reached,
  rejected and the winner (highlighted). Click a candidate for its data preview, gate details and every
  score's reason or error. Pairwise judgements and the decision trace follow.
- **Candidates across runs:** every candidate of every run in one table; *Compare by* a variation param
  (for example `model`) gives candidates, wins, win rate and mean total per value, over the rows your
  filters keep. This is how an experiment that varies models, temperatures or prompts is read.

The same data is available in Python:

```python
from hone_select.dashboard import all_candidates, list_runs, run_detail

runs = list_runs(".hone/select/spans.db")  # newest first
detail = run_detail(".hone/select/spans.db", runs[0]["run_id"]) if runs else None
```

With experiments in the project (`experiments/`, see [experiments.md](experiments.md)), the dashboard
also has an **Experiments** page: the plan, Approve / Deny, the results and every sample, and a blind
rating screen for human criteria (`--project PATH` when the project is not the current folder).

Runs recorded before the dashboard existed have no configuration or task on their run span; the page says
"not recorded" and shows everything else.
