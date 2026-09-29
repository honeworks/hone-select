# hone-select examples

Each file shows one concept, top to bottom, and is the pattern to copy when you write code that does the
same thing. Each one opens with a docstring in three parts: **What** it shows, **How** (the calls, in
order) and **Why** (when to use it, and the pitfalls). Every example runs offline and deterministically
with the package's fakes, prints what it shows, and `assert`s the key facts. The test suite runs all of
them (`tests/e2e/test_ac21_examples.py`).

```bash
uv run python examples/quickstart.py     # or any file below
```

They use only the public API: `hone_select`, `hone_select.testing`, `hone_select.ports`,
`hone_select.adapters.*`, `hone_select.cache`, `hone_select.explain` and `hone_select.experiments`. The Design column points to
the sections of [`design/current.md`](../design/current.md) that define the behaviour. Read them in this
order.

## Basics
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`quickstart.py`](quickstart.py) | generator, gate, scorer, argmax | The smallest complete selection, and how to read a `Result`. | §4.1, §5.1 |
| [`config_file.py`](config_file.py) | TOML config file + registry | Settings in [`config_file.toml`](config_file.toml), code in the registry, a prompt scorer without code, `ConfigError`. | §6 |
| [`scoring_existing.py`](scoring_existing.py) | `engine.score`, `engine.select` | Rank candidates you already have, or get the full `Result` with the decision trace; `Candidate.of` and stable ids. | §5.1 |

## Scoring
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`gates_and_fallbacks.py`](gates_and_fallbacks.py) | gates, `fallback=` | Pass/fail checks cheapest first, gate errors reject visibly, what happens when nothing passes. | §5.3, §5.8 |
| [`aggregation_and_missing.py`](aggregation_and_missing.py) | weights, aggregators, `missing=`, normalizers | How scores become a total, and why `None` is not 0. | §5.5 |
| [`cascade_and_budget.py`](cascade_and_budget.py) | cascade, `keep_top`, budgets | Expensive scorers only on finalists; cost and money caps that stop a run cleanly. | §5.4, §5.9 |
| [`variations_and_seeds.py`](variations_and_seeds.py) | variation schedules, seeds | Per-candidate seeds and params; the same seed gives the same run. | §5.10 |
| [`selection_policies.py`](selection_policies.py) | `first_above`, tie margin, min confidence, pairwise | Stop early when good enough; settle near-ties with an order-swapped pairwise judge. | §5.7 |

## Judges and external scorers
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`prompt_scorers.py`](prompt_scorers.py) | `PromptScorer`, `PromptGate`, `PromptPairwise` | Plain-English criteria over a `DecisionClient`; the questions it is asked; the self-judging warning. | §4.2 |
| [`judging_images.py`](judging_images.py) | `images_from` on `PromptScorer` and `PromptPairwise` | Show a vision judge a reference and a candidate picture, and settle image ties pairwise. | §4.2 |
| [`bring_your_own_client.py`](bring_your_own_client.py) | the `DecisionClient` port | An OpenAI-SDK-shaped client through `OpenAIDecisionClient`, and a judge written from scratch plus its contract check. | §7.2, §9.1 |
| [`command_scorer.py`](command_scorer.py) | `CommandScorer` | Score files with a program in any language ([`command/count_words.py`](command/count_words.py)), JSON on stdin and stdout. | §4.3 |

## Run controls
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`dedup.py`](dedup.py) | exact and embedding dedup | Drop repeats and paraphrases before scoring; a failing embedder falls back to exact. | §5.11 |
| [`score_cache.py`](score_cache.py) | score cache | Never pay twice for a score; bump `version` to recompute one scorer; your own cache. | §5.12 |

## Records and the CLI
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`records_and_explain.py`](records_and_explain.py) | spans, trace context, `explain` | Join a caller's trace, query the SQLite store, re-explain a run later, keep private content out. | §8 |
| [`cli_run_and_explain.py`](cli_run_and_explain.py) | `hone-select run / explain / show` | Run a selection from the shell with the files in [`cli/`](cli/) (extra `cli`). | §9.2 |

## Experiments
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`experiments.py`](experiments.py) | `hone_select.experiments`: new, plan, approve, start, results | Compare setups (factors, a baseline) over test cases with a plan a person approves; read which setup and which factor level win. | §12 |

## With the rest of honeworks
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`other_packages_through_ports.py`](other_packages_through_ports.py) | scorer ports, trace context | A hone-taste-shaped scorer and a hone-flow-shaped caller, without importing either. | §7.1, §7.7 |
| [`hone_models_judge.py`](hone_models_judge.py) | `[judges.*]` entry point | `client = "hone_models:decision"` in the config (extra `models`; skipped without it). | §7.8 |

## Putting it together
| File | Concept | What you learn | Design |
|---|---|---|---|
| [`lyrics_selection.py`](lyrics_selection.py) | the whole pipeline | The OneShotStudio chorus picker: code gate, cheap scorer, prompt judges on finalists, pairwise tie-break, fallback. | §5.2 |
