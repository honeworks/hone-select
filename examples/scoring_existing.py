"""Scoring existing candidates: rank drafts you already have, without a generator.

What: Engine.select(candidates) runs dedup, gates, the cascade and selection on a list you pass in, and
      returns the same Result as run(): winner, ranking, decision trace, run_id, budget.
      Engine.score(candidates) is the short form that returns only the ranking. Also: how Candidate.of
      computes a stable id.
How:  1. wrap each item with Candidate.of(data, files=None, meta=None);
      2. build an Engine with scorers (and gates) but no @generator;
      3. ranked = engine.score(candidates) is a list of Scored, winner first - unless ranked[0].rejected:
         then every candidate was rejected and, with fallback = "none", none won;
      4. result = engine.select(candidates) when you need to say why a candidate is missing (a "dedup"
         entry names what it duplicated) or link to the records (result.run_id for hone-select explain).
Why:  use it when candidates come from elsewhere - a batch job, a human, another tool - or to re-score
      old outputs with new scorers. Pitfall: the id hashes `data` (and file contents), not `meta`, so
      two candidates with the same data are duplicates even if their meta differs.
"""

from hone_select import Candidate, Engine, scorer

drafts = [
    {"lyrics": "we were golden under summer rain", "author": "draft A"},
    {"lyrics": "golden", "author": "draft B"},
    {"lyrics": "we were golden, we were young, we were wild and we were never done", "author": "draft C"},
]


@scorer()
def near_eight_words(c):
    words = len(c.data["lyrics"].split())
    return max(0.0, 1 - abs(words - 8) / 8)


CONFIG = """
[score]
cascade = [{ scorers = ["near_eight_words"] }]
"""
engine = Engine(CONFIG, registry=[near_eight_words])

candidates = [Candidate.of(d, meta={"source": "notebook"}) for d in drafts]
ranked = engine.score(candidates)
for place, item in enumerate(ranked, start=1):
    print(place, item.candidate.data["author"], item.total)

assert ranked[0].candidate.data["author"] == "draft A"  # 6 words is closest to 8
assert ranked[-1].candidate.data["author"] == "draft B"

# The id is a hash of the data: stable across processes, independent of meta.
same = Candidate.of(drafts[0], meta={"source": "somewhere else"})
assert same.id == candidates[0].id and len(same.id) == 16
ranked_again = engine.score([*candidates, same])
assert len(ranked_again) == 3  # the duplicate was removed by the default exact dedup

# select() keeps the decision trace: it says which candidate the removed one duplicated.
result = engine.select([*candidates, same])
removed = [e for e in result.decision if e["event"] == "dedup"]
print("removed:", removed, "run:", result.run_id)
assert removed[0]["duplicate_of"] == candidates[0].id
assert result.winner is not None and result.winner.candidate.data["author"] == "draft A"
