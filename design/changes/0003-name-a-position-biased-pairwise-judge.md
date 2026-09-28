# 0003: Name a position-biased pairwise judge instead of silently recording ties

## Status

`implemented in 0.1.0` (options 2 and 3; approved by the owner 2026-09-28)

## Context

Found while building OneShotStudio, a honeworks demo app. Its idea and lyrics selections escalate near
ties (`tie_margin = 0.03`) to a `PromptPairwise` judge, `qwen2.5vl-7b` through `mk.decision(...)`, a
different model family from the writer as the docs recommend. In the first real run every pairwise
comparison came back the same way:

```text
pairwise: {'a': '8c5b…', 'b': 'f38b…', 'ab': 'a', 'ba': 'b', 'outcome': 'tie'}
pairwise: {'a': '93f5…', 'b': 'fb1d…', 'ab': 'a', 'ba': 'b', 'outcome': 'tie'}
pairwise: {'a': '93f5…', 'b': 'ef5e…', 'ab': 'a', 'ba': 'b', 'outcome': 'tie'}
```

The judge chose whichever candidate it was shown first, in both orders, three times out of three.
hone-select did the right thing (both orders, disagreement is a tie, the original ranking stands), so
nothing went wrong, but the escalation spent six judge calls and decided nothing, and the only trace of
the reason is `ab != ba` in each entry.

## Problem

- A judge that always prefers position A is useless for pairwise decisions, and the engine can see it
  (every disagreement is "first shown wins"), but it does not say so. A user reading
  `outcome: tie` assumes the candidates were equally good.
- Each escalation keeps paying for calls to a judge that has shown the bias in this very run.

## Options

1. **Keep as is**: ties are correct; the user can read `ab` / `ba`.
2. **Name it**: when both orders disagree *and* each order picked its first-shown candidate, record the
   entry with `outcome: "tie"` plus `reason: "position_bias"`, and add one `warning` event per run when
   a judge does so for every comparison it made ("pairwise judge <model> picked the first-shown
   candidate in 3/3 comparisons; its pairwise decisions are ties").
3. **Stop paying**: after `k` consecutive position-biased comparisons in one run (config
   `[select] max_biased_pairwise = 2`), skip further escalations to that judge, recorded as
   `escalation_skipped`.
4. **Ask differently**: a pairwise question that shows both candidates and asks for a score for each
   (a "both scored" comparison), which some models handle better than "A or B". A larger change to the
   `DecisionClient` question types.

## Decision

Proposed: 2 and 3. They use information the engine already has, cost nothing when the judge is fine, and
turn a silent waste into an explained one. Option 4 may be a later change once 2 shows how common the
bias is across judges.

## Consequences

- Rankings are unchanged; decision traces gain a `reason` field on biased ties and a warning.
- Runs with a biased judge make fewer judge calls under option 3.
- hone-lens can count `position_bias` reasons per judge model across runs.

## Migration and compatibility

Additive: a new optional `reason` key on `pairwise` entries, a new warning message, a new optional config
key (default: no skipping, the current behaviour).

## Implementation notes

- Both directions count as position bias: the first-shown candidate in both orders, or the second-shown
  in both. The entry is `{"event": "pairwise", ..., "outcome": "tie", "reason": "position_bias"}`.
- The warning is added once per run, after selection, when every comparison made was position-biased;
  it names the judge, its model when known, the position and `k/k`.
- `[select] max_biased_pairwise` (≥ 1, default none) counts position-biased comparisons in a row; a
  comparison that is not biased resets the count. A skipped comparison is
  `{"event": "escalation_skipped", "a", "b", "reason": "position_bias", "biased_in_a_row"}`, a tie that
  costs nothing. One run has one pairwise judge, so the count is per run.
- `reason` on these two entries is a fixed token, so it is kept readable when content capture is off.
- Option 4 (a "both scored" question) is not built.
