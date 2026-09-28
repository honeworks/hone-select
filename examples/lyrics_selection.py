"""Lyric selection: the whole pipeline on a real-world shape (OneShotStudio's chorus picker), offline.

What: a generator (an LLM in real use) writes chorus drafts; a cheap code gate rejects drafts that lost
      the hook; a cheap code scorer checks line balance and keeps the best two; prompt judges (narrative,
      singability) score only those finalists; a pairwise judge settles a near-tie; a fallback keeps a
      result even if every draft loses the hook.
How:  1. code components with @generator / @gate / @scorer, prompt components with PromptScorer and
         PromptPairwise over one judge (FakeDecisionClient here, scripted by a `rule` function);
      2. one TOML config: gates, a two-stage cascade with keep_top, weights, tie_margin + escalate,
         fallback = "best_rejected";
      3. engine.run(task, seed=7), then result.winner and engine.explain(result).
Why:  this is the shape to copy for "generate several, keep the best" with an LLM: cheap checks first,
      judges only where they can change the outcome. To go live, swap FakeDecisionClient for
      OpenAIDecisionClient (bring_your_own_client.py) or a hone-models judge (hone_models_judge.py), and
      use a judge from a different model family than the generator (the engine warns otherwise).
"""

from hone_select import Candidate, Engine, PromptPairwise, PromptScorer, gate, generator, scorer
from hone_select.testing import FakeDecisionClient

HOOK = "we were golden"
DRAFTS = [
    "we were golden / under summer rain / we were golden / never felt the same",
    "summer rain again / we danced / it was fine",
    "we were golden / we were golden / golden golden golden / we were golden",
    "under the lights / we were golden / hold on tight / never let me go",
]


@generator()
def write_chorus(task, v):
    lyrics = DRAFTS[v["index"]]  # an LLM call with v["seed"] and v["params"] in real use
    return Candidate.of({"lyrics": lyrics}, meta={"model": "gemma4-12b"})


@gate(cost=0.1)
def hook_preserved(c):
    return HOOK in c.data["lyrics"]


@scorer(cost=1)
def line_balance(c):
    lengths = [len(line.split()) for line in c.data["lyrics"].split(" / ")]
    return min(lengths) / max(lengths)


def rule(state, questions):
    """The fake judge: rewards variety (distinct words) and penalises repetition."""
    if isinstance(state, dict):  # a pairwise question: state = {"A": ..., "B": ...}
        return {"better_lyric": "A" if variety(state["A"]) >= variety(state["B"]) else "B"}
    return {name: min(1.0, variety(state) + 0.2) for name in questions}


def variety(text):
    """Distinct words / all words: 1.0 means no word repeats."""
    words = text.split()
    return len(set(words)) / len(words)


judge = FakeDecisionClient(rule=rule, model_id="qwen2.5vl-7b")
narrative = PromptScorer("narrative", "The chorus tells one clear moment.", judge, field="lyrics")
singability = PromptScorer("singability", "Easy to sing back after one listen.", judge, field="lyrics")
better_lyric = PromptPairwise("better_lyric", "Which chorus is stronger?", judge, field="lyrics")

CONFIG = """
[generate]
n = 4
vary = { seed = "increment", temperature = [0.8, 1.0] }
[score]
gates = ["hook_preserved"]
cascade = [
  { scorers = ["line_balance"], keep_top = 2 },
  { scorers = ["narrative", "singability"] },
]
weights = { line_balance = 0.2, narrative = 0.4, singability = 0.4 }
[select]
tie_margin = 0.1
escalate = "pairwise"
pairwise = "better_lyric"
fallback = "best_rejected"
"""

engine = Engine(
    CONFIG, registry=[write_chorus, hook_preserved, line_balance, narrative, singability, better_lyric]
)

result = engine.run({"hook": HOOK}, seed=7)
print("winner:", result.winner.candidate.data["lyrics"])
print(engine.explain(result))

gated = next(entry for entry in result.decision if entry["event"] == "gated")
assert len(gated["rejected"]) == 1  # "summer rain again ..." lost the hook
finalists = [s for s in result.ranked if s.stage_reached == 2]
assert len(finalists) == 3  # keep_top = 2, plus the draft tied with the second at the cut
pairwise = next(entry for entry in result.decision if entry["event"] == "pairwise")
assert pairwise["outcome"] == "a"  # the judge confirmed the leader in both orders
assert result.winner.candidate.id == pairwise["a"]
assert result.winner.candidate.data["lyrics"].endswith("never let me go")
assert "narrative" not in result.ranked[-1].scores  # the gated draft was never judged
assert not result.winner.rejected
