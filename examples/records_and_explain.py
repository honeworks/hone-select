"""Records and explain: every step is a span in a SQLite store; any past decision can be re-explained.

What: where a run is recorded ([record]), joining a caller's trace (trace={"traceparent": ...}),
      reading the spans with plain SQL, engine.explain(result) now and explain_run(db, run_id) later
      from the store alone, and capture_content = false for private data.
How:  1. [record] sink = "sqlite" (default; also "jsonl" or "none"), path = ... (default
         $HONE_HOME/select/spans.db, i.e. .hone/select/spans.db), capture_content = true;
      2. engine.run(task, trace={"traceparent": "00-<trace id>-<parent span id>-01", ...}) makes the
         run a child of the caller's span; result.trace_id and result.run_id identify it;
      3. spans follow the shared honeworks span format: table `spans` (name, trace_id, parent_span_id,
         attributes JSON, ...); span names are hone.select.run / generate / gate / score / pairwise /
         decision;
      4. explain_run(db_path, run_id) (hone_select.explain) or `hone-select explain <run_id>` rebuilds
         the ranking and the decision trace.
Why:  "why did this candidate win?" must be answerable next week, by someone else, from the records.
      Pitfall: with capture_content = false the store keeps hashes and lengths instead of candidate
      text, reasons and errors - use it for private data, and expect explanations without the text.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

from hone_select import Engine, gate, generator, scorer
from hone_select.explain import explain_run


@generator()
def write(task, v):
    return f"{task} number {v['index']}"


@gate()
def not_three(c):
    return not c.data.endswith("3")


@scorer()
def later_is_better(c):
    return int(c.data[-1]) / 10


folder = tempfile.TemporaryDirectory()
db = Path(folder.name) / "spans.db"


def config(capture_content):
    # a TOML literal string ('...') keeps a Windows path's backslashes as they are
    return f"""
[generate]
n = 4
[score]
gates = ["not_three"]
cascade = [{{ scorers = ["later_is_better"] }}]
[record]
sink = "sqlite"
path = '{db}'
capture_content = {str(capture_content).lower()}
"""


def spans_of(trace_id):
    """(name, parent span id, attributes) of every span in one trace - plain SQL on the store."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT name, parent_span_id, attributes FROM spans WHERE trace_id = ?", (trace_id,)
        )
        return [(name, parent, json.loads(attributes)) for name, parent, attributes in rows]


# The caller (a workflow step, a web request) passes its W3C trace context.
incoming = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "hone.run_id": "flow-7"}

engine = Engine(config(capture_content=True), registry=[write, not_three, later_is_better])
result = engine.run("line", trace=incoming)
print(engine.explain(result))
assert result.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"  # the run joined the caller's trace

spans = spans_of(result.trace_id)
names = [name for name, _, _ in spans]
print("span names:", sorted(set(names)))
_, run_parent, _ = next(span for span in spans if span[0] == "hone.select.run")
assert run_parent == "00f067aa0ba902b7"  # the run's parent is the caller's span
assert names.count("hone.select.generate") == 4
assert names.count("hone.select.gate") == 4
score_attributes = next(attributes for name, _, attributes in spans if name == "hone.select.score")
assert score_attributes["hone.run_id"] == "flow-7"  # shared keys from the caller reach every span
assert {"hone.candidate_id", "hone.select.scorer", "hone.select.score.value"} <= set(score_attributes)

# Later, in another process: re-explain the decision from the store alone.
later = explain_run(db, result.run_id)
assert later == engine.explain(result)

# capture_content = false: hashes instead of text for anything that may hold private content.
private = Engine(config(capture_content=False), registry=[write, not_three, later_is_better])
private_run = private.run("my private diary")
stored = json.dumps([attributes for _, _, attributes in spans_of(private_run.trace_id)])
print("private text stored:", "diary" in stored)
assert "diary" not in stored
assert "sha256" in stored
folder.cleanup()
