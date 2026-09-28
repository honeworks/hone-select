# 0001: Initial design (v0.1)

## Status

`implemented in 0.1.0`

## Context

A local AI content pipeline (song ideas, lyrics, music, music videos) ran generate-score-select in five
places, each written by hand: song ideas scored by code checks plus an LLM rubric, lyric variants ranked
by an LLM rubric, rendered songs ranked by measurements (lyric recall from a transcription, an audio
quality model, signal checks), visual style proposals judged by a critic model, and keyframes retried
until a vision model scored one above a threshold. The research behind this design is in
[`../history/0000-research.md`](../history/0000-research.md).

The loops shared the same bugs and the same lessons:

- failed judges returned `0.0`, which made good lyrics look terrible;
- hard rejects worked best kept apart from the weighted score, while rejected items were still scored so
  they could serve as a fallback;
- most judging was measurement, not an LLM; the LLM scored only the subjective parts;
- a judge from the generator's model family preferred its own output;
- odd or truncated model output happened, and one bad answer must not crash the loop;
- when every candidate failed, the pipeline still needed to return something, clearly flagged.

## Problem

There was no small library that does the whole loop: gates, cascaded scorers, scorers written as code or
as a prompt, pluggable judges (LLM or decision model), selection policies, tie escalation, budgets,
caching and a full record of the decision. Existing tools each cover a piece (DSPy `BestOfN`, Inspect
AI's solver / scorer split, DeepEval and Promptfoo rubrics, Instructor's Universal Self-Consistency).

## Options

1. **Keep the hand-written loops**, one per stage. Every new stage repeats the bugs.
2. **Build on an existing framework** (DSPy or an evaluation tool). This ties users to that framework's
   modules and a single reward function, and evaluation tools are not built to pick winners in production.
3. **A small standalone library** that owns the loop, knows nothing about the domain, and talks to
   models only through small interfaces the user fills.

## Decision

Option 3: hone-select, a library step (`Engine.run`) with a thin optional CLI. The v0.1 design, in full
in [`../current.md`](../current.md):

- **Pipeline:** generate → dedup → gates → scorer cascade with `keep_top` → aggregate → select → record.
  No master LLM: the winner comes from a selector applied to scores.
- **Two ways to define good, mixable:** Python functions and external commands (JSON on stdin and
  stdout); plain-English criteria run by any `DecisionClient` (`PromptScorer`, `PromptGate`,
  `PromptPairwise`, with checklists and anchored scales).
- **Gates** separate from scores, cheapest first; an erroring gate rejects visibly.
- **Aggregation** (`weighted_mean`, `min`, `geometric`, `weighted_mean_with_floor`) and **missing
  policies** (`renormalize`, `zero`, `reject`): `None` is never `0`.
- **Selectors:** `argmax`, `first_above` (stop generating once good enough), `pairwise_tournament`; ties
  within `tie_margin` and scores below `min_confidence` escalate to a pairwise judge asked in both orders.
- **Fallbacks** `best_rejected`, `first_valid`, `none`, always flagged.
- **Budgets** in cost units, seconds and money; a budget stop ends generation and selection goes on.
- **Dedup** exact or by embedding; a **score cache** keyed by candidate content, scorer name, scorer
  version and judge model; **variation schedules** and seeds for reproducible runs.
- **Ports owned by hone-select:** `DecisionClient`, `TextClient`, `Embedder`, `RecordSink`,
  `ScoreCache`, the trace context and the scorer shape, each with a public fake and a contract checker.
  Adapters for the OpenAI SDK and LangChain, and a judge entry point for a model-access package, ship as
  optional extras. The core depends on the standard library and pydantic only.
- **Records:** every step is an OpenTelemetry-shaped span in a local SQLite store; the decision span
  holds enough to explain a run later from the store alone (`explain`).
- **Warnings** for Goodhart risk (large `n` against few scorers), self-judging, and all scores `None`.

Deliberately left out of v0.1: `panel_vote`, `usc` and `human` selectors, the G-Eval mode, a refine loop,
MinHash dedup, async generation. They add scope without changing the core loop, and each can be added
later behind the existing config keys.

## Consequences

- One engine replaces the hand-written loops; its semantics (gates, `None`, ties, fallbacks) are tested
  once, by 21 acceptance cases.
- A selection is explainable after the fact, and its records join the traces of the model calls behind
  it when the caller passes a trace context.
- Users must write their scorers as small functions or criteria, and state costs to get useful cascades
  and budgets.
- Emulated decision clients over chat models are uncalibrated (`calibrated=False`), so confidence-based
  escalation is only as good as the judge's self-reported confidence.
- Generation is sequential; runs against hosted models are slower than they could be.

## Migration and compatibility

The first release; nothing to migrate. The ports are public API: changing a port's signature is a
breaking change and is listed in `CHANGELOG.md`. Config keys reserved for later features
(`max_concurrency`, `text_client`) are accepted now so that adding them later does not break configs.
