"""Selection policies: stop at the first good-enough candidate, and settle near-ties with a pairwise judge.

What: the three policies - argmax (best total), first_above (stop generating once a candidate reaches a
      threshold) and pairwise_tournament (head-to-head) - plus tie handling: tie_margin, min_confidence,
      escalate = "pairwise", and why every pairwise comparison is asked in both orders.
How:  1. [select] policy = "argmax" | "first_above" (needs threshold) | "pairwise_tournament";
      2. a @pairwise function (a, b) -> "a" | "b" | "tie" (or (choice, confidence)), named in
         [select] pairwise = "...";
      3. [select] tie_margin = 0.05 and/or min_confidence = 0.6 with escalate = "pairwise": when the top
         two totals are within the margin, or a deciding score is unsure, the judge decides;
      4. read "escalation", "pairwise" and "tie" entries in result.decision;
      5. a judge that picks the same position in both orders is named: reason = "position_bias" on its
         "pairwise" entries, a warning when it did so every time, and [select] max_biased_pairwise = k
         stops asking it after k such comparisons in a row ("escalation_skipped").
Why:  totals within noise of each other are not a real preference, and LLM judges favour the first
      option they see. Asking (A, B) and (B, A) and counting only agreeing wins removes that position
      bias; when the orders disagree the original ranking is kept, and the tie is recorded.
"""

from hone_select import Engine, Score, generator, pairwise, scorer

LINES = ["plain line", "a good line", "a great line", "the best line", "another line"]
QUALITY = {"plain line": 0.3, "a good line": 0.85, "a great line": 0.86, "the best line": 0.95}
generated = []


@generator()
def write(task, v):
    generated.append(v["index"])
    return LINES[v["index"]]


@scorer()
def quality(c):
    return QUALITY.get(c.data, 0.1)


@scorer()
def unsure(c):  # e.g. a judge that says it is not sure of its answer
    value = {"plain line": 0.95, "a good line": 0.6}.get(c.data, 0.4)
    return Score(value, confidence=0.4)


@pairwise()
def prefers_good(a, b):  # a consistent judge: "a good line" beats anything, in either order
    if a.data == "a good line":
        return "a"
    return "b" if b.data == "a good line" else "tie"


@pairwise()
def always_first(a, b):  # a position-biased judge: always picks whatever it sees first
    return "a"


registry = [write, quality, unsure, prefers_good, always_first]

# 1. first_above: generate one at a time; stop as soon as one reaches the threshold.
FIRST_ABOVE = """
[generate]
n = 5
[score]
cascade = [{ scorers = ["quality"] }]
[select]
policy = "first_above"
threshold = 0.8
"""
result = Engine(FIRST_ABOVE, registry=registry).run("line")
print("first_above generated", generated, "winner:", result.winner.candidate.data)
assert generated == [0, 1]  # candidates 2..4 were never generated
assert result.winner.candidate.data == "a good line"


# 2. argmax on 3 candidates: 0.86 vs 0.85 is within tie_margin, so the pairwise judge decides.
def near_tie_config(judge):
    return f"""
[generate]
n = 3
[score]
cascade = [{{ scorers = ["quality"] }}]
[select]
tie_margin = 0.05
escalate = "pairwise"
pairwise = "{judge}"
"""


result = Engine(near_tie_config("prefers_good"), registry=registry).run("line")
pairs = [(e["ab"], e["ba"], e["outcome"]) for e in result.decision if e["event"] == "pairwise"]
print("escalated:", pairs, "winner:", result.winner.candidate.data)
assert result.winner.candidate.data == "a good line"  # 0.85 beat 0.86 head to head
assert pairs == [("b", "b", "b")]  # a = "a great line" (ranked first); "ba" is mapped back to that frame

# 3. A position-biased judge: the orders disagree, so it is a tie and the ranking stands.
result = Engine(near_tie_config("always_first"), registry=registry).run("line")
pair = next(e for e in result.decision if e["event"] == "pairwise")
print("biased judge:", pair["ab"], pair["ba"], "->", pair["outcome"], "winner:", result.winner.candidate.data)
assert (pair["ab"], pair["ba"], pair["outcome"]) == ("a", "b", "tie")
assert result.winner.candidate.data == "a great line"  # 0.86 vs 0.85: the ranking stands

# 4. min_confidence: a large margin, but the deciding score is unsure -> escalate anyway.
LOW_CONFIDENCE = """
[generate]
n = 3
[score]
cascade = [{ scorers = ["unsure"] }]
[select]
min_confidence = 0.6
escalate = "pairwise"
pairwise = "prefers_good"
"""
result = Engine(LOW_CONFIDENCE, registry=registry).run("line")
escalation = next(e for e in result.decision if e["event"] == "escalation")
print("escalation reason:", escalation["reason"])
assert "confidence" in escalation["reason"]
assert result.winner.candidate.data == "a good line"  # the judge overruled 0.95 vs 0.6

# 5. pairwise_tournament: the judge decides among all valid candidates, best-ranked first.
TOURNAMENT = """
[generate]
n = 4
[score]
cascade = [{ scorers = ["quality"] }]
[select]
policy = "pairwise_tournament"
pairwise = "prefers_good"
"""
result = Engine(TOURNAMENT, registry=registry).run("line")
print("tournament winner:", result.winner.candidate.data)
assert result.winner.candidate.data == "a good line"  # it beat "the best line" (0.95) head to head


# 6. The position-biased judge again, in a tournament: named, and not asked again after two such comparisons.
BIASED = TOURNAMENT.replace('pairwise = "prefers_good"', 'pairwise = "always_first"\nmax_biased_pairwise = 2')
result = Engine(BIASED, registry=registry).run("line")
reasons = [e.get("reason") for e in result.decision if e["event"] == "pairwise"]
skipped = [e for e in result.decision if e["event"] == "escalation_skipped"]
print("biased judge:", reasons, "skipped:", len(skipped))
assert (
    reasons == ["position_bias", "position_bias"] and len(skipped) == 1
)  # the third comparison was not asked
assert result.winner.candidate.data == "the best line"  # ties everywhere: the ranking stands
