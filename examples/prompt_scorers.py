"""Prompt scorers: plain-English criteria answered by a judge (an LLM or a decision model).

What: PromptScorer (one criterion, and a checklist), PromptGate and PromptPairwise over a DecisionClient,
      here the offline FakeDecisionClient; what the judge is asked (fake.calls); the self-judging warning.
How:  1. judge = any object with decide(state, questions, *, images=(), trace=None) - FakeDecisionClient
         in tests and examples, OpenAIDecisionClient / a hone-models client in real use;
      2. PromptScorer(name, criteria, judge, scale=(1, 5), anchors={...}, field="lyrics") asks one
         "score" question; a list of criteria is a checklist whose value is the mean of the items;
      3. PromptGate(name, criteria, judge, threshold=0.5) asks "yes_no" and passes when P(yes) >= threshold;
      4. PromptPairwise(name, criteria, judge) asks a "choice" between A and B; the engine asks both orders;
      5. register them like any scorer; the judge's model id is part of the score-cache key.
Why:  some qualities ("is it singable?") have no formula. Pitfalls: a judge from the same model family as
      the generator tends to prefer its own writing - the engine warns (see result.decision); use `field`
      so the judge sees only the text it should judge, not the rest of `data`.
"""

from hone_select import Candidate, Engine, PromptGate, PromptPairwise, PromptScorer, generator
from hone_select.testing import FakeDecisionClient

CHORUSES = [
    "we were golden / under summer rain",
    "la la la / la la la",
    "golden hour / golden power / golden",
]


@generator()
def write_chorus(task, v):
    # "model" in meta: the engine compares it with each judge's model id (self-judging check)
    return Candidate.of({"lyrics": CHORUSES[v["index"]], "draft": v["index"]}, meta={"model": "gemma4-12b"})


def rule(state, questions):
    """The fake judge's script: answers per question name, computed from the state it is shown."""
    if isinstance(state, dict):  # a pairwise question: state = {"A": ..., "B": ...}
        return {"better": "A" if "summer" in state["A"] else "B"}
    repetitive = state.count("golden") > 1
    return {
        "singable": 0.5 if repetitive else 1.0,  # a value 0..1 (1..5 scale: 3 and 5)
        "craft_1": 0.75,  # checklist item 1
        "craft_2": 0.25 if repetitive else 0.75,  # checklist item 2
        "on_topic": "yes" if "golden" in state else "no",
    }


judge = FakeDecisionClient(rule=rule, model_id="gemma4-27b")  # same family as the generator: a warning

singable = PromptScorer(
    "singable",
    "The chorus can be sung back after one listen.",
    judge,
    scale=(1, 5),
    anchors={"1": "flat", "3": "competent", "5": "instantly singable"},
    field="lyrics",
)
craft = PromptScorer("craft", ["Uses concrete images.", "Avoids filler syllables."], judge, field="lyrics")
on_topic = PromptGate(
    "on_topic", "The chorus is about the word 'golden'.", judge, threshold=0.5, field="lyrics"
)
better = PromptPairwise("better", "Which chorus is more memorable?", judge, field="lyrics")

CONFIG = """
[generate]
n = 3
[score]
gates = ["on_topic"]
cascade = [{ scorers = ["singable", "craft"] }]
[select]
tie_margin = 0.4
escalate = "pairwise"
pairwise = "better"
"""
# cache=None: this example counts calls (with the default cache, a second run reuses the scores)
engine = Engine(CONFIG, registry=[write_chorus, singable, craft, on_topic, better], cache=None)
result = engine.run("golden")
print("winner:", result.winner.candidate.data["lyrics"])
for item in result.ranked:
    print(" ", item.candidate.data["lyrics"], "| total", item.total, "| rejected", item.rejected)

# What the judge was asked: the DecisionClient question payloads (checked through fake.calls).
first_score_call = next(call for call in judge.calls if "singable" in call["questions"])
print("question:", first_score_call["questions"]["singable"])
assert first_score_call["state"] == CHORUSES[0]  # field="lyrics": only the lyrics, not "draft"
assert first_score_call["questions"]["singable"] == {
    "type": "score",
    "instructions": "The chorus can be sung back after one listen.",
    "scale": [1, 5],
    "anchors": {"1": "flat", "3": "competent", "5": "instantly singable"},
}

# The checklist value is the mean of its items; each item is kept in details.
repetitive = next(s for s in result.ranked if s.candidate.data["draft"] == 2)
assert repetitive.scores["craft"].value == 0.5  # mean of 0.75 and 0.25
assert repetitive.scores["craft"].details == {"craft_1": 0.75, "craft_2": 0.25}

# The gate rejected the off-topic chorus; the pairwise judge settled the near-tie in both orders.
assert next(s for s in result.ranked if s.candidate.data["draft"] == 1).rejected
pairwise_calls = [call["state"] for call in judge.calls if "better" in call["questions"]]
assert len(pairwise_calls) == 2 and pairwise_calls[0] == {
    "A": pairwise_calls[1]["B"],
    "B": pairwise_calls[1]["A"],
}
assert result.winner.candidate.data["draft"] == 0

# The judge and the generator are both "gemma": the engine warns.
warnings = [e["message"] for e in result.decision if e["event"] == "warning"]
print("warning:", warnings[0])
assert any("self-judging" in message for message in warnings)
