# Concepts

## The pipeline
*Examples: [`quickstart.py`](../examples/quickstart.py), [`dedup.py`](../examples/dedup.py).*

`Engine.run(task)` goes through these steps. Each step is recorded both in `result.decision` and as a span.

1. **Generate** `n` candidates with your `@generator`. Each call gets a `Variation`
   (`{"index", "seed", "params"}`) from the `[generate]` schedule. A generator that raises is skipped and
   the run goes on.
2. **Dedup** exact duplicates (same `Candidate.id`) or near duplicates (`method = "embedding"`, needs an
   `Embedder`).
3. **Gates** run cheapest first. The first failing gate rejects the candidate. A gate that raises
   rejects it too, and the error is recorded.
4. **Cascade.** Each stage runs its scorers on the candidates still in play, then keeps the best
   `keep_top` for the next stage (ties at the cut are kept). Put cheap scorers first. Expensive ones then
   only see the finalists.
5. **Aggregate** each candidate's scores into a `total` (see below).
6. **Select** a winner by policy, and escalate ties or low-confidence decisions to a pairwise judge.
7. **Fallback** when every candidate was rejected.

`Engine.select(candidates)` runs steps 2 to 7 on candidates you already have and returns the same
`Result` as `run()` (winner, ranking, decision trace, `run_id`, budget). `Engine.score(candidates)` is
the short form that returns only `result.ranked`:

```python
from hone_select import Candidate, Engine, scorer


@scorer()
def brevity(c):
    return 1 - len(c.data["lyrics"]) / 100


engine = Engine("[score]\ncascade = [{ scorers = ['brevity'] }]", registry=[brevity])
drafts = ["la la la", "a much longer line about the summer sun"]
ranked = engine.score([Candidate.of({"lyrics": t}) for t in drafts])
assert ranked[0].candidate.data["lyrics"] == "la la la"

result = engine.select([Candidate.of({"lyrics": t}) for t in [*drafts, drafts[0]]])
assert [e["event"] for e in result.decision if e["event"] == "dedup"] == ["dedup"]  # why one is missing
```

## Candidates
*Example: [`scoring_existing.py`](../examples/scoring_existing.py).*

`Candidate.of(data, files=None, meta=None)` computes a stable `id`: the first 16 hex characters of a
sha256 over the canonical JSON of `data` plus the content hash of each file. The same data gives the same
id in every process, which is what the score cache and dedup rely on. A generator can return a plain
value; it is wrapped with `Candidate.of`.

## Scores and `None`
*Example: [`aggregation_and_missing.py`](../examples/aggregation_and_missing.py).*

A scorer returns a value in 0..1 (higher is better), a `Score(value, confidence, reason, details, error)`,
or `None`. **`None` means "could not score", never 0.** Here is what happens with bad scorer output:
- a scorer that raises gives `Score(None, error="ValueError: ...")`;
- a value outside 0..1, `NaN` or a `bool` gives `Score(None, error=...)` (it is never clamped).

Use the normalizers to map raw measurements to 0..1: `linear(lo, hi)`, `inverse(lo, hi)`,
`sigmoid(mid, k)`, `from_1_5`.

```python
from hone_select import from_1_5, inverse, linear

assert linear(0, 10)(5) == 0.5
assert inverse(0, 10)(2) == 0.8  # smaller raw values are better
assert from_1_5(4) == 0.75
```

## Aggregation
*Example: [`aggregation_and_missing.py`](../examples/aggregation_and_missing.py).*

`[score] aggregate` combines one candidate's scores. Scorers without a weight get weight 1.
- `weighted_mean` (default): the sum of wᵢ·vᵢ divided by the sum of wᵢ.
- `min`: the worst score.
- `geometric`: the weighted geometric mean.
- `weighted_mean_with_floor`: the weighted mean, capped at `floor` if any score is below `floor`.

`missing` says what a `None` score does:
- `renormalize` (default): leave it out;
- `zero`: count it as 0;
- `reject`: reject the candidate.

A candidate with no values at all has `total = None` and ranks last.

## Ranking and selection
*Examples: [`selection_policies.py`](../examples/selection_policies.py), [`gates_and_fallbacks.py`](../examples/gates_and_fallbacks.py).*

Candidates are ranked in this order ([decision D-003](../design/decisions.md#d-003-ranking-puts-deeper-cascade-stages-before-higher-partial-totals--2026-09-27)):
1. non-rejected before rejected;
2. candidates with a total before those without;
3. candidates that reached a later cascade stage first;
4. higher total;
5. generation order.

Policies:
- `argmax`: the top of the ranking.
- `first_above`: generate and score one candidate at a time. Stop at the first one whose total is at least
  `threshold`. If none reaches it, the best one wins and `threshold_not_met` is recorded.
- `pairwise_tournament`: king of the hill with the `pairwise` judge, in ranking order.

`escalate = "pairwise"` sends two cases to the pairwise judge: the top candidates are within `tie_margin`,
or a score of the top two has `confidence < min_confidence`. The judge is always asked in both orders, and
a win counts only when both orders agree. Otherwise it is a tie and the original ranking stands.

A judge that picks the same position in both orders (always the first-shown candidate, say) is choosing
by position, not content. Its comparison is recorded as `outcome: "tie"` with `reason: "position_bias"`,
and when it did so in every comparison of the run, a warning names the judge. With
`max_biased_pairwise = k`, after `k` such comparisons in a row the judge is not asked again in that run;
each skipped comparison is an `escalation_skipped` entry and a tie.

`fallback` handles the case where every candidate is rejected:
- `none` (default): the winner is `None`;
- `best_rejected`: the best rejected candidate wins;
- `first_valid`: the first rejected candidate, in generation order, that has a total wins.

In both cases the winner has `rejected=True` and the fallback is recorded.

## Budgets
*Example: [`cascade_and_budget.py`](../examples/cascade_and_budget.py).*

`[budget]` sets `max_cost`, `max_seconds` and `max_money_usd`. Every component has a `cost`. The budget
is checked before each generation and before each cascade stage after the first. When it runs out,
generation stops and selection goes on with the candidates that exist. The stop is recorded, and
`BudgetExceeded` never reaches you from `run()`. A run may overshoot by one step. `result.budget` reports
what was used.

## Determinism
*Example: [`variations_and_seeds.py`](../examples/variations_and_seeds.py).*

Seeds come from the variation schedule and the `seed` argument of `run(task, seed=...)`. With the same
config, seed and deterministic scorers, you get the same ranking and totals. No id uses Python's `hash()`.

## Score cache
*Example: [`score_cache.py`](../examples/score_cache.py).*

Scores are cached by (candidate id, scorer name, scorer version, judge model). The cache is a SQLite file
next to the span store. Change a scorer's `version` to recompute its scores. Failed scores are not cached.
`Engine(..., cache=None)` turns the cache off.

## Warnings
*Example: [`prompt_scorers.py`](../examples/prompt_scorers.py).*

These go to the log and into `result.decision`:
- `n` is more than 10 times the number of independent scorers ("Goodhart risk");
- a judge model shares a family with the generator model (`meta["model"]`), which is self-judging;
- every score is `None`.
