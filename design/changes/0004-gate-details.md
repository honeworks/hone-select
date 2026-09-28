# 0004: Details on gate results

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building course-builder, a honeworks demo app. Its quiz selection has two gates per quiz
draft: a script gate (every question: 4 distinct options, one key, evidence quoted from the lesson) and
a judge gate (the judge answers each valid question blind; a draft needs 5 confirmed questions). The
per-question verdicts are the valuable part: they decide which questions ship and are the audit trail
for "every answer was checked".

`GateResult` has `passed`, `probability` and `reason` only. The app keeps the per-question rows in a
closure dict keyed by candidate id, which later scorers and the final assembly read. It works but is
invisible to the decision trace, the records and `hone-select explain`, and a cached gate verdict would
lose the rows.

## Options

1. **`GateResult.details: Mapping[str, Any]`** (like `Score.details`), recorded as a content attribute on
   the gate span and kept in `Scored.gates[name].details`.
2. Make the check a scorer instead of a gate. Loses the "reject before costly scoring" semantics.
3. Leave it (today).

## Decision

Proposed: option 1; `to_gate_result` reads `details` from GateLike objects the same way `to_score` does.
Backward compatible (default empty mapping).

## Implementation notes

`GateResult.details` defaults to an empty mapping (keyword or fifth positional argument). The gate span
gets `hone.select.gate.details` only when the gate returned details; it is content, so with content
capture off only its hash and length are stored. Gates are not cached, so no cache change was needed.
