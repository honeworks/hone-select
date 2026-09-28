"""Gates and fallbacks: hard pass/fail checks before scoring, and what to do when nothing passes.

What: gates that return a bool or a GateResult with a reason; a gate that raises (it rejects the
      candidate, visibly); the three fallback modes for a run where every candidate was rejected.
How:  1. @gate(cost=...) functions return True/False or GateResult(passed, probability, reason,
         details) - details holds per-item verdicts, kept in scored.gates[name] and on the gate span;
      2. list them in [score] gates - they run cheapest first and stop at the first failure;
      3. [select] fallback = "best_rejected" | "first_valid" | "none" decides the winner when all are
         rejected; a fallback winner has winner.rejected = True and a "fallback" entry in result.decision.
Why:  a gate expresses "unacceptable no matter what" (wrong format, missing hook, unsafe), which a low
      score cannot: a gate-failed candidate never beats a passing one. Pitfall: a gate error rejects the
      candidate (it is not skipped); look for "gate_error" entries in result.decision.
"""

from hone_select import Engine, GateResult, gate, generator, scorer

IDEAS = ["a song about the sea", "SONG!!!", "a quiet song about rain", "a song about " + "x" * 200]


@generator()
def pitch(task, v):
    return IDEAS[v["index"]]


@gate(cost=0)  # free: runs first
def no_shouting(c):
    return c.data == c.data.lower()


@gate(cost=1)
def not_too_long(c):
    if len(c.data) > 100:
        raise ValueError(f"{len(c.data)} characters; the limit is 100")  # an error rejects, visibly
    return GateResult(passed=True, reason="length ok")


@gate(cost=2)
def mentions_water(c):
    found = [word for word in ("rain", "sea") if word in c.data.split()]
    passed = bool(found)
    reason = "" if passed else "no water"
    return GateResult(passed, 1.0 if passed else 0.0, reason, details={"water_words": found})


@gate()
def impossible(c):
    return False  # used below to show the fallbacks


@scorer()
def gentle(c):
    return 0.9 if "quiet" in c.data else 0.6


CONFIG = """
[generate]
n = 4
[score]
gates = ["mentions_water", "not_too_long", "no_shouting"]
cascade = [{ scorers = ["gentle"] }]
"""
registry = [pitch, no_shouting, not_too_long, mentions_water, gentle, impossible]

result = Engine(CONFIG, registry=registry).run("a song")
gated = next(entry for entry in result.decision if entry["event"] == "gated")
print("rejected:", gated["rejected"])
print("winner:", result.winner.candidate.data)
assert result.winner.candidate.data == "a quiet song about rain"
assert len(gated["rejected"]) == 2  # SONG!!! (no_shouting, cheapest, ran first) and the long one
assert any(entry["event"] == "gate_error" for entry in result.decision)
shouting = next(s for s in result.ranked if s.candidate.data == "SONG!!!")
assert list(shouting.gates) == ["no_shouting"]  # the first failure stops the other gates
rain = next(s for s in result.ranked if s.candidate.data == "a quiet song about rain")
assert rain.gates["mentions_water"].details == {"water_words": ["rain"]}  # the gate's audit trail


# Now every candidate fails a gate: the fallback decides.
def all_rejected_config(fallback):
    return f"""
[generate]
n = 4
[score]
gates = ["impossible"]
cascade = [{{ scorers = ["gentle"] }}]
[select]
fallback = "{fallback}"
"""


expected = {
    "best_rejected": "a quiet song about rain",  # the best total among the rejected
    "first_valid": "a song about the sea",  # the first generated one that has a total
    "none": None,  # no winner at all
}
for mode, expected_winner in expected.items():
    result = Engine(all_rejected_config(mode), registry=registry).run("a song")
    winner = result.winner.candidate.data if result.winner else None
    print(f"fallback={mode}: winner={winner!r}")
    assert winner == expected_winner
    assert any(entry["event"] == "fallback" for entry in result.decision)
    if result.winner is not None:
        assert result.winner.rejected  # flagged: a fallback winner is never mistaken for a passing one
