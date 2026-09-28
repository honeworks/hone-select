"""Other packages through ports: plug in a scorer from another library and join a workflow's trace,
without hone-select importing that library.

What: (1) a scorer object shaped like hone-taste's `for_select(...)` result - a callable with `name` and
      `version` that takes `trace=` and returns a Score-like object of its own type; (2) being called by a
      workflow runner (hone-flow's role) that passes its trace context, which reaches that scorer.
How:  1. any callable fn(candidate) works as a scorer; the optional attributes name, cost and version
         (the scorer shape), plus hone-select's kind ("scorer" | "gate" | "pairwise") and judge_model,
         describe it;
      2. it may return float, None, hone_select.Score, or any object/dict with `value` (+ confidence,
         reason, details, error) - it does not need to import hone_select;
      3. if it accepts a `trace` keyword, it is called with trace=current_trace(): the W3C traceparent
         of the current span plus shared keys (hone.run_id, hone.step, hone.candidate_id, hone.scorer);
      4. the caller passes its own context with engine.run(task, trace={...}) or, as here,
         engine.score(candidates, trace={...}).
Why:  honeworks packages are useful alone and meet only at ports; the real hone-taste scorer drops in
      exactly like the stand-in below (`registry=[tt.for_select(tt.patterns([...]), field="lyrics")]`),
      and its spans join the selection's trace. Pitfall: return None (or value=None with an error) when
      you cannot score - never 0.
"""

from dataclasses import dataclass, field

from hone_select import Candidate, Engine


@dataclass
class TasteScore:  # the other package's own score type: only `value` is required
    value: float | None
    reason: str = ""
    error: str = ""


@dataclass
class ClicheScorer:
    """A stand-in for a hone-taste scorer adapted with for_select(...): fewer cliches is better."""

    name: str = "cliches"
    version: str = "0.1.0"  # the other package's version: bumping it invalidates cached scores
    cliches: tuple = ("delve", "tapestry", "journey")
    traces: list = field(default_factory=list)  # kept here to show what the engine passes

    def __call__(self, candidate, /, *, trace=None):
        self.traces.append(trace)
        text = candidate.data["lyrics"].lower()
        found = [word for word in self.cliches if word in text]
        return TasteScore(1 - len(found) / len(self.cliches), reason=f"cliches: {found}")


cliches = ClicheScorer()
# cache=None: this example records calls (with the default cache, a second run reuses the scores)
engine = Engine('[score]\ncascade = [{ scorers = ["cliches"] }]', registry=[cliches], cache=None)

# A workflow step calls the selection with its own trace context.
workflow_context = {
    "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    "hone.run_id": "nightly-lyrics",
    "hone.step": "pick_chorus",
}
drafts = ["let us delve into the tapestry of our journey", "rain on the roof, you by the door"]
ranked = engine.score([Candidate.of({"lyrics": d}) for d in drafts], trace=workflow_context)
print("winner:", ranked[0].candidate.data["lyrics"], "| reason:", ranked[0].scores["cliches"].reason)
assert ranked[0].candidate.data["lyrics"].startswith("rain")
assert ranked[1].total == 0.0  # three of three cliches: a real 0, not a missing score

# The scorer received the selection's trace: same trace id, the workflow's keys, and its own ids.
trace = cliches.traces[0]
print("trace seen by the scorer:", trace)
assert trace["traceparent"].split("-")[1] == "0af7651916cd43dd8448eb211c80319c"
assert trace["hone.run_id"] == "nightly-lyrics" and trace["hone.step"] == "pick_chorus"
assert trace["hone.scorer"] == "cliches" and trace["hone.candidate_id"] == ranked[1].candidate.id
