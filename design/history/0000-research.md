# 0000: Research: generate, score, select

This is the research that started hone-select, written in September 2026 before any code, rewritten
here to be read as the starting point of the design. What was built is in
[`../changes/0001-initial-design.md`](../changes/0001-initial-design.md) and
[`../current.md`](../current.md); the last section below lists where the design moved away from this
research.

> **Names.** The research used placeholder names. Today's names:
>
> | In the research | Today |
> |---|---|
> | `bestofn` (also considered: `pickwise`, `sift`, `winnow`, `selecta`) | `hone-select` (import `hone_select`) |
> | `modelkit`, the sister "model access" package | `hone-models` |
> | `flowkit` (workflow runner) | `hone-flow` |
> | `tracelens` (workflow analyzer) | `hone-lens` |
> | `ours.select.*` record fields | `hone.select.*` span attributes |
> | `protocols.py` | `hone_select/ports.py` |

## 1. The pattern

The most common quality pattern in generative AI:

1. **Generate** several candidates for the same task.
2. **Score** each one with pluggable checks.
3. **Select** a winner with a configurable policy, and record everything.

Best-of-N (called rejection sampling in training) is the most dependable way to improve output without
changing the model, and its outputs double as training data. Yet every project re-implements it badly:
ad-hoc loops, scores mixed with pass/fail rules, "failed to score" confused with "scored 0", only the
winner saved.

The pitch that came out of the research: *"Best-of-N for anything: bring your own generator and your own
definition of good (code or a prompt), and get a reproducible, recorded, budget-aware winner."*

## 2. What already existed

- **DSPy `BestOfN` / `Refine`:** good stopping rules (a threshold or N) and a feedback loop, but tied to
  DSPy modules and a single reward function.
- **Inspect AI:** a clean solver / scorer split, but built for evaluation, not production selection.
- **DeepEval (G-Eval), Promptfoo:** rubrics and LLM judges, but no selection engine.
- **Instructor's Universal Self-Consistency:** one selection strategy.

Nothing combined gates, cascaded scorers, code-or-prompt scorers, pluggable judges, selection policies,
tie escalation, budget, caching and full recording in a small package.

## 3. Where the ideas came from

The first user was OneShotStudio, a local AI content pipeline (song ideas → lyrics → music → video) that
already ran generate-score-select in five places, each hand-written:

| Stage | Generate | Score | Select |
|---|---|---|---|
| Song ideas | an LLM writes 12 ideas | code checks (rhyme, syllables, meter) plus an LLM rubric; hard rejects | top-N non-rejected, fall back to rejected |
| Lyrics | N variants per idea | an LLM rubric (hook, narrative, singability, consistency, overall) | argmax, unscored variants last |
| Songs | N music-model renders | source separation → transcription and lyric recall → an audio quality model → signal checks | argmax |
| Style proposals | an LLM writes 3 styles | a critic LLM | best, or a human lock |
| Keyframes | N images per shot, retry rounds | a vision-LLM rubric | best above a threshold, else the first |

Lessons the new package had to encode:

- **`None` is not `0`.** Failed judges returned `0.0`, which made real lyrics look terrible.
- **Gates are not scores.** Hard rejects stay apart from the weighted score; rejected items are still
  scored so they can serve as a fallback.
- **Most judging is measurement, not an LLM.** Song quality came from transcription recall, an audio
  quality model and signal checks; the LLM scored only the subjective parts. Hence the "custom script"
  path.
- **The judge must not be the generator** (self-preference bias).
- **Odd or truncated model output happens.** Scorers must fail soft (`None` plus an error), never crash
  the loop.
- **Fallbacks matter.** If every candidate fails, return something and flag it.

## 4. The concepts

```
   task ──▶ Generator ──▶ N candidates (varied seed / temperature / prompt)
                 │
              [Dedup]            exact / near-duplicate removal
                 │
              [Gates]            pass/fail; failures kept but flagged
                 │
         [Scorer cascade]        stage 1 cheap on all → keep top-k → stage 2 expensive …
                 │
            [Aggregate]          weighted / min / floor → one 0..1 value per candidate
                 │
            [Selector]           argmax | first_above | pairwise | panel | usc | human
                 │                 └─ tie within a margin or low confidence → escalate
              [Stop?]            threshold met, N or budget used up; optional refine loop
                 │
             [Record]            every candidate, every score, the decision trace
```

| Concept | Responsibility | Knows the domain? |
|---|---|---|
| Candidate | one output: structured `data`, optional `files`, `meta` | holds it, does not interpret it |
| Generator | produces one candidate for a task, given a variation | yes (user code) |
| Gate | pass/fail check, never averaged into the score | yes (script or prompt) |
| Scorer | scores one candidate on one axis | yes (script or prompt) |
| Pairwise judge | compares two candidates: A, B or tie, with confidence | yes (script or prompt) |
| Judge backend | runs prompt-based checks: an LLM or a decision model | no (plugged in) |
| Aggregator, Selector, Recorder, Engine | combine, pick, persist, orchestrate | no |

**Who decides the winner? No master LLM.** The selector applied to scores decides. A model is one
possible source of scores, often not the best one.

## 5. Two ways to define "good"

Every gate, scorer and pairwise judge can be written either way; both produce the same `Score` or
`GateResult`, so they mix in one run.

**Custom script**, for anything measurable: a Python function, or an executable in any language that
reads the candidate as JSON on stdin and writes a score as JSON on stdout (a non-zero exit means "could
not score").

**Prompt plus a chosen judge**, for taste and subjective quality: criteria in plain English and a judge
backend. The criteria stay the same whichever judge runs them. Question types shared by all backends:
`yes_no` (probability the statement is true: gates, checklist items), `choice` (probability per option:
pairwise judging), `score` (0..1 value, confidence, rationale: pointwise scorers).

Two kinds of judge backend were compared:

| Backend | Strengths | Weaknesses |
|---|---|---|
| LLM, local or hosted | gives a rationale, flexible, can see images | slower, costlier, confidence often unreliable, verbosity and self-preference bias |
| Decision model (built for yes/no, choice and score with calibrated probabilities) | much faster and cheaper than chat models | no rationale; hosted |

Techniques for prompt judges: checklist decomposition (one criterion per question), anchored scales,
rationale before score for LLMs, an optional G-Eval mode (criteria expanded into evaluation steps, scores
weighted by token probabilities), panels of judges, and a warning when judge and generator share a model
family. A typical cascade: a cheap decision model on every candidate, an LLM rubric on the top few.

## 6. Model access stays outside

The package never imports a model SDK. It defines two small protocols, a `DecisionClient` (structured
questions about a state) and a `TextClient` (prompt in, text or JSON out), and prompt judges accept any
object that implements them: a sister model-access package, a user's own client, a LangChain model behind
a short adapter, or a test fake. An LLM implements `DecisionClient` by prompting with a JSON schema; a
decision model implements it natively.

**Observability boundary:** the model-access layer records every model call; the selection package
records only its own layer (candidates, scores, decisions) and passes the trace id, candidate id and
scorer name to every judge call, so a decision can be traced to the prompts behind it.

A later memory package over past runs should fit without engine changes: records in a documented,
versioned format, and "novelty against history" as just another scorer or gate.

## 7. Selection policies and best practices

| Policy | Winner | Use when |
|---|---|---|
| `argmax` | highest total among non-rejected | reliable pointwise scorers exist |
| `first_above` | generate one at a time, stop at the first above a threshold | generation is expensive and "good enough" is well defined |
| `pairwise_tournament` | knockout with a pairwise judge, both orders, a win counts only if it wins both | subjective quality where scores barely differ |
| `panel_vote` | several judges from different model families | high stakes, known judge bias |
| `usc` | an LLM picks the most consistent candidate | free-form answers with no scorer |
| `human` | a person picks from a ranked shortlist | final creative decisions |

Best practices the library should enforce: separate generate, score and select; never average gates into
scores; `None` is not `0`; everything on 0..1, higher is better (normalizer helpers); cheap before
expensive; moderate N, because past a point a proxy scorer selects outputs that fool it (Goodhart: several
independent scorers, pessimistic aggregation, a warning when N is large relative to the scorers); make
candidates differ on purpose and dedup before scoring; handle ties and low confidence explicitly; always
judge pairs in both orders; criteria are data and judges are swappable; fixed seeds and a score cache;
record everything; fail soft; a small core (standard library and pydantic).

## 8. Proposed scope

- **v1:** data model, decorators, registry, TOML config; generate with variation, exact dedup, gates,
  cascade with `keep_top`, aggregation and `missing` policies; script and command scorers; prompt scorers
  with anchored scales, checklists and simple panels; `argmax`, `first_above`, order-swapped
  `pairwise_tournament`; tie margin, confidence floor, escalation, fallbacks; budgets; a score cache and a
  JSON recorder (one folder per run); fakes so tests need no GPU or network.
- **v2:** G-Eval mode, `panel_vote` / `usc` / `human`, a refine loop, MinHash and embedding dedup, async
  generation, a CLI and an HTML report.
- **Later add-ons:** human calibration (pairwise picks fitted with Bradley-Terry), preference dataset
  export, OpenTelemetry tracing.
- **Non-goals:** orchestration, evaluation benchmarks, model clients, a hosted service.

The first real port was to be the pipeline's lyric selection, reproducing the old winners on existing run
data before switching over.

## 9. Where the design went from here

Decided while writing the v0.1 design ([`0001`](../changes/0001-initial-design.md)):

- **Records became spans.** Instead of a JSON folder per run, every step is an OpenTelemetry-shaped span in
  a local SQLite store, shared in format with the other packages the author was building, so one trace can
  link a selection to the model calls behind it. `explain` works from the store alone.
- **Pulled into v0.1:** embedding dedup (through an `Embedder` port) and the CLI (as an optional extra).
- **Left for later:** panels and `panel_vote`, `usc`, `human`, the G-Eval mode, the refine loop, MinHash
  dedup, async generation, the HTML report, calibration and dataset export.
- **Prompt gates and pairwise judges became their own classes** (`PromptGate`, `PromptPairwise`) instead of
  an `as_gate` flag and a `[pairwise.*]` config section; config keeps `[scorers.<name>]` for prompt and
  command scorers.
- **Judges in config resolve by entry-point name** (`[judges.<name>] client = "..."`), never by importing
  an arbitrary `module:factory` path.
- **Library first:** `Engine.run` is the product; the CLI is thin.
- **Name, license, Python:** `hone-select`, Apache-2.0, Python ≥ 3.11.

## References

**Selection and best-of-N**
- [DSPy: BestOfN and Refine](https://dspy.ai/tutorials/output_refinement/best-of-n-and-refine/)
- [RLHF Book: Rejection Sampling](https://rlhfbook.com/c/09-rejection-sampling)
- [Universal Self-Consistency (arXiv 2311.17311)](https://arxiv.org/abs/2311.17311)
- [JETTS: LLM judges as test-time-scaling evaluators](https://arxiv.org/pdf/2504.15253)

**Judges: pointwise vs pairwise, bias, G-Eval**
- [Judging the Judges: position bias](https://arxiv.org/abs/2406.07791)
- [DeepEval G-Eval](https://deepeval.com/docs/metrics-llm-evals)

**Over-optimization (Goodhart)**
- [Scaling Laws for Reward Model Overoptimization](https://proceedings.mlr.press/v202/gao23h/gao23h.pdf)
- [Reward Hacking in RL (Lilian Weng)](https://lilianweng.github.io/posts/2024-11-28-reward-hacking/)

**Cascades and budget**
- [EcoRank: budget-constrained re-ranking](https://arxiv.org/pdf/2402.10866)

**Architecture to learn from**
- [Inspect AI scorer reference](https://inspect.aisi.org.uk/reference/inspect_ai.scorer.html)
- [Instructor](https://github.com/567-labs/instructor)
