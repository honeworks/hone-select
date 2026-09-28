# Why hone-select exists

> Bring your generator and your definition of good (code or a prompt); get a reproducible, recorded,
> budget-aware winner.

## The problem

Best-of-N is the most dependable way to improve the output of a generative model without changing the
model: generate several candidates for the same task, score them, keep the best. It is simple enough
that almost every project writes it by hand, and hard enough that the hand-written versions tend to get
the same things wrong:

- **"Could not score" becomes "scored 0".** A judge that fails or returns something unreadable yields
  `0.0`, and a good candidate looks terrible.
- **Pass/fail rules are mixed into scores.** A hard reject ("the hook must rhyme") gets averaged with a
  quality score instead of excluding the candidate.
- **Expensive checks run on everything.** A slow model-based judge sees all candidates, including ones a
  cheap check would have removed.
- **Ties and doubtful scores are decided silently**, often by the order the candidates happened to be
  generated in.
- **The judge is the generator.** A model grading its own output prefers it.
- **Only the winner is kept.** Afterwards nobody can say why it won, or whether the others were close.

hone-select came out of a local song-generation pipeline that ran this loop in five places (ideas,
lyrics, rendered songs, visual styles, keyframes), each written by hand with its own version of these
bugs.

## Why existing tools fall short

Several tools cover a piece of the loop, none covers all of it in a small library:

- **DSPy `BestOfN` / `Refine`** has good stopping rules but is tied to DSPy modules and one reward function.
- **Inspect AI** has a clean solver / scorer split, but is built for evaluation, not for picking a winner
  in production.
- **DeepEval (G-Eval), Promptfoo** provide rubrics and LLM judges, but no selection engine.
- **Instructor's Universal Self-Consistency** is one selection strategy.

None combines gates, cascaded scorers, code-or-prompt scorers, pluggable judges, selection policies,
tie escalation, budgets, caching and a full record of the decision.

## Core ideas

1. **No master LLM decides.** The winner comes from a selector applied to scores. A model is one
   possible source of scores, often not the best; real measurements come first when they exist.
2. **"Good" is defined in one of two ways, mixable in one run:** a custom script (a Python function, or
   an executable in any language speaking JSON), or plain-English criteria run by a judge you choose (an
   LLM, local or hosted, or a decision model).
3. **Gates are not scores.** Pass/fail checks reject; they are never averaged in.
4. **`None` is not `0`.** A failed scorer gives "no value" with an error, and a `missing` policy says what
   aggregation does with it.
5. **Cheap before expensive.** Scorers run in cascade stages; each stage keeps only the top candidates
   for the next.
6. **Uncertainty is explicit.** Near-ties and low-confidence scores escalate to a pairwise judge, asked
   in both orders; fallbacks are flagged when nothing passes.
7. **Everything is recorded** as spans in a local store, so any decision can be explained later from the
   store alone.
8. **The engine knows nothing about the domain.** Text, audio files, images or code: a candidate is data
   plus optional files.
9. **Useful alone.** The core depends on the standard library and pydantic. Judges, embedders and
   record sinks are small interfaces (ports) that any client can implement; adapters for common SDKs
   are optional extras.

## Who it is for

Anyone who generates more than one candidate and has to pick one: lyrics, ideas, summaries, code
patches, images, audio renders. It is a library step you call from your own code (or from a workflow
runner), not a service.

## What it deliberately does not do

- **No orchestration.** It is one reusable step; running it inside a larger workflow is the caller's job.
- **No model clients.** It never calls a model itself; it asks the clients you inject.
- **No evaluation benchmarks.** It picks winners, it does not score model releases.
- **Not a hosted service.** Everything runs in your process and writes local files.

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as it stands today: concepts, rules and the guaranteed behaviour (acceptance cases) |
| [`changes/`](changes/) | one record per design change: what was found, what was decided, why, and how to migrate |
| [`history/`](history/) | the early research the design started from, kept readable |
| [`decisions.md`](decisions.md) | smaller implementation choices, and the ones awaiting owner review |

A new design change starts as a record in `changes/` with status `proposed`; see
[CONTRIBUTING.md](../CONTRIBUTING.md#changing-the-design). How the package was built is described in the
[README](../README.md#how-this-was-built).
