# 0006: `Engine.score` that keeps the decision trace

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building lead-finder, a honeworks demo app. Two selections there score candidates that already
exist: the fit of one lead (gates for dealbreakers, code and judge scorers) and the daily shortlist
across leads (embedding dedup, then the top N by fit). Both use `Engine.score(candidates)`, which returns
`result.ranked` and drops the rest of the `Result`: the decision trace (`dedup` events with
`duplicate_of`, `gated`, `warning` such as the fallback to exact dedup when the embedder fails), the
`run_id` and the budget.

For the shortlist the app needs to say *why* a lead is missing. It infers "removed by dedup" as
"was passed in but is not in `ranked`", and cannot say which lead it duplicated, nor tell a dedup from an
embedder failure. It also cannot link its saved shortlist to the selection's records (`hone-select
explain <run_id>` needs the run id, which `score` does not return).

## Options

1. **`Engine.score(..., full=True) -> Result`** (default `False` keeps today's return type).
2. **A new method `Engine.select(candidates) -> Result`**, the `run()` twin for existing candidates;
   `score()` stays as the short form returning `ranked`.
3. Leave it; applications infer what they can.

## Decision

Proposed: option 2. Same name family as `run`, no flag that changes a return type, and `score()` keeps
working. `select()` is `score()` without the final `.ranked`.

## Consequences

- One new public method and a test; docs: `scoring_existing.py` shows both.
