"""Quickstart: generate five candidates, gate them, score them with a plain function, keep the best.

What: the smallest complete selection - one generator, one gate, one scorer, the argmax policy.
How:  1. decorate plain functions with @generator, @gate and @scorer;
      2. write the config as TOML text: [generate] n, [score] gates + cascade, [select] policy;
      3. Engine(config, registry=[...]).run(task) returns a Result: read result.winner, result.ranked,
         result.decision, and engine.explain(result) for a readable account.
Why:  start here; every other example is a variation of this shape. A scorer returns 0..1 where higher
      is better. Return None (never 0) when it cannot score. A gate rejects; it does not lower a score.
"""

from hone_select import Engine, gate, generator, scorer


@generator()
def write(task, v):  # v is the Variation: {"index": 0.., "seed": ..., "params": {...}}
    return f"{task} #{v['index']}" * (v["index"] + 1)  # a plain value is wrapped in a Candidate


@gate()
def not_too_long(c):
    return len(c.data) < 80  # False rejects the candidate


@scorer(cost=1)
def shorter_is_better(c):
    return 1 - len(c.data) / 100


engine = Engine(
    """
[generate]
n = 5
[score]
gates = ["not_too_long"]
cascade = [{ scorers = ["shorter_is_better"] }]
[select]
policy = "argmax"
""",
    registry=[write, not_too_long, shorter_is_better],
)

result = engine.run("hello")
print(result.winner.candidate.data, result.winner.total)  # hello #0 0.92
print(engine.explain(result))

assert result.winner.candidate.data == "hello #0"
assert [s.total for s in result.ranked] == sorted((s.total for s in result.ranked), reverse=True)
events = [entry["event"] for entry in result.decision]
assert {"generated", "gated", "scored", "selected"} <= set(events)
