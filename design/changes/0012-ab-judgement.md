# 0012: A/B: a person picks the better of two outputs, blind

## Status

`proposed` (2026-09-29). The owner chose A/B over ratings for the final say between close setups
(story-room 0001, open question 3). Builds on [0009](0009-experiments.md) (human ratings, the dashboard).

## Context

The research method's last step for every model or prompt question is "owner A/B on the top 2": the owner
sees two outputs for the same input, one from each setup, without knowing which is which, and picks the
better one or says "tie". Today hone-select has only ratings (`kind = "human"`, one output at a time on a
scale). Ratings answer "how good is this?"; A/B answers "which of these two?", which is faster to decide
and more reliable when the two setups are close.

## Problem

- There is no way to show a person two outputs side by side, blind, and record the pick.
- The results have no win rate between two setups, so a decision rule like "the winner if its win rate is at
  least 60 % over 20 pairs" (story-room W1) cannot be read from the results.

## Options

1. **Ratings only** (today): no change; close setups end in overlapping intervals and no decision.
2. **An A/B criterion in hone-select (proposed):** a new human criterion kind, a pairing rule, a side-by-side
   screen in the dashboard, win rates in the results. Every pipeline repository gets it.
3. **A separate A/B tool per repository:** repeats the blind pairing, storage and statistics in every repo.

## Decision (proposed)

### 1. The definition

```toml
[scorers.owner_pick]
kind = "ab"
question = "Which premise would you rather see as a video?"
between = "top"        # "top": the 2 best setups by the automatic total (default)
                       # or a list of setup names / baselines: ["hemmingway-1-t1.0", "today"]
                       # or "baseline": every setup against the first baseline
top = 2                # with between = "top": how many setups (2 = one pair; 3 = three pairs)
pairs = 20             # pairs to judge per pair of setups
allow_tie = true
```

An `ab` criterion is not part of the automatic total (like ratings); it has its own section in the results.

### 2. Pairing (blind, fair)

- A pair is **the same case** from the two setups (same input, the sample with the same index when both
  have it), so the pick compares the setups, not the inputs. Failed samples and samples outside the run
  conditions are never shown.
- Which setup is shown on the left is random per pair (seeded by the experiment's seed), so position cannot
  favour one setup. Nothing on the screen names a model, a setup or a prompt.
- `pairs` are drawn across cases as evenly as possible (with 8 cases and 20 pairs: 2 or 3 per case, different
  samples); the draw is fixed in `ab_plan.json` when the pairs become available, so stopping and coming
  back continues the same list.
- With `between = "top"` the pairs exist once generation and automatic scoring are done (the top setups
  are known then); the dashboard shows "A/B: waiting for the run" until then.

### 3. The screen

The dashboard's experiment page gets an **A/B** button next to **Rate** for each `ab` criterion: the question,
the two outputs side by side (text, JSON rendered as in Samples, images, audio and video players), and three
choices, **Left**, **Tie** (when allowed), **Right**, also on the keys ←, T, →. A progress line ("7 of 20"),
an undo of the last pick, and nothing else. Writes follow the dashboard's existing protections (the
page's header, same origin, loopback Host check).

### 4. Storage

`ab.jsonl` in the experiment folder, one line per pick:
`{"criterion", "pair": [setup_a, setup_b], "case", "left": sample_id, "right": sample_id, "choice":
"left" | "right" | "tie", "at"}`. The setups are recorded from the fixed plan, never shown. Undo appends a
line `{"undo": <index>}` (the file is append-only, like `ratings.jsonl`).

### 5. Results

For each pair of setups, in `results.json` and `summary.md`:

- wins, losses and ties of each side; **win rate** without ties, with a 95 % interval (Wilson), and
  `clear` when the interval does not include 50 %;
- `complete` when `pairs` picks exist;
- per case, who won (so a setup that wins only on one kind of input shows).

`summary.md` gets a line such as: "A/B (owner_pick): hemmingway-1-t1.0 beats styletune-31b-t1.0, 14-5
with 1 tie, win rate 74 % (51-88 %), clear." `hone-select experiments report` recomputes after picks, as for
ratings.

### 6. Acceptance cases

| AC | Scenario | Expected |
|---|---|---|
| AC-40 | An `ab` criterion with `between = "top"`, `top = 2`, `pairs = 6` over 3 cases, after a run | `ab_plan.json` holds 6 pairs, each the same case from the two best setups, 2 per case, left/right seeded; failed and outside samples never appear; the API serves the next pair without setup names |
| AC-41 | Picks through the dashboard API (left, right, tie, undo); a write without the header | stored as specified; undo removes the last pick; the refused write stores nothing |
| AC-42 | Results after 20 picks (14-5-1) | wins, losses, ties, win rate 73.7 % with the Wilson interval, `clear`, `complete`; the summary line; per-case winners; an unfinished A/B shows `complete: false` |

## Consequences

- Every pipeline repository gets the same blind A/B, and a decision rule on a win rate can be read from
  the results.
- The owner's time per question: about 20 picks, a few seconds each for text.
- About 250 lines (pairing, storage, results) plus the dashboard screen.

## Migration and compatibility

Additive: a new criterion kind, a new file `ab.jsonl` / `ab_plan.json`, a new results section, a new
dashboard screen and API routes. Experiments without `ab` criteria are unchanged.

## Open questions for the owner

1. **Default pairs:** 20 per pair of setups (the W1 plan's number)? More pairs make the result more certain
   but take longer; 20 is about 2-5 minutes for premises.
2. **Tie allowed** by default? Recommended: yes (a forced pick on two equal outputs adds noise).
