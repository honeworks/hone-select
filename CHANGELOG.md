# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Why the design changed is recorded in
[design/changes/](design/changes/).

## [0.1.0] - unreleased

First release. Design: [0001 initial design](design/changes/0001-initial-design.md),
[0002 pass the trace context to components](design/changes/0002-trace-to-components.md), and the
changes found in the demo apps: [0003](design/changes/0003-name-a-position-biased-pairwise-judge.md),
[0004](design/changes/0004-gate-details.md), [0005](design/changes/0005-prompt-scorer-with-several-images.md),
[0006](design/changes/0006-score-returns-the-decision.md), [0007](design/changes/0007-pairwise-over-images.md).

### Added (from the demo apps)
- `Engine.select(candidates) -> Result`: the `run()` twin for candidates you already have, keeping the
  decision trace, `run_id` and budget; `score()` stays as the short form returning `ranked`
  ([design change 0006](design/changes/0006-score-returns-the-decision.md)).
- `GateResult.details` (per-item verdicts), also read from GateLike objects, kept in
  `Scored.gates[name]` and recorded as `hone.select.gate.details` on the gate span
  ([design change 0004](design/changes/0004-gate-details.md)).
- `PromptScorer(images_from=...)` accepts a sequence of file keys and sends the images in that order, for
  example a reference sheet and the candidate's picture; config `images_from = ["reference", "image"]`
  ([design change 0005](design/changes/0005-prompt-scorer-with-several-images.md)).
- `PromptPairwise(images_from=...)` compares two candidates' images (A's, then B's; a shared file once),
  so image ties can escalate to a pairwise judge; score and pairwise spans record
  `hone.select.image_keys` ([design change 0007](design/changes/0007-pairwise-over-images.md)).
- `examples/judging_images.py`.
- A pairwise judge that chose by position in both orders is named: `reason: "position_bias"` on its
  `pairwise` entries and one warning when it did so in every comparison of the run; `[select]
  max_biased_pairwise = k` stops asking it after `k` such comparisons in a row (`escalation_skipped`)
  ([design change 0003](design/changes/0003-name-a-position-biased-pairwise-judge.md)).

### Changed (before publishing)
- Docs and examples name `qwen2.5vl-7b`, a model id in hone-models' packaged registry, instead of a local
  one; the Ollama example uses the `qwen2.5vl:7b` tag. Design change records mention the demo apps by
  name instead of workspace paths; `CONTRIBUTING.md` marks the sibling hone-models checkout as a
  development-only note.

### Added (design docs)
- `design/`: why the package exists, the current design with its acceptance cases, design change records,
  implementation decisions and the research history; `CONTRIBUTING.md` with the conventions.

### Fixed (concurrent writers)
- `SqliteSpanSink` and `SqliteScoreCache` set `busy_timeout` before switching to WAL and retry the switch
  briefly: when several processes created the same fresh store at once, the switch ignored the timeout and
  a writer failed with "database is locked" (dropped spans).

### Added (examples set)
- `examples/`: one runnable, explained example per public concept, each opening with a What / How / Why
  docstring and asserting its key facts, indexed in reading order in `examples/README.md`: config file,
  scoring existing candidates, gates and fallbacks, aggregation and missing scores, cascade and budget,
  variations and seeds, selection policies, prompt scorers, bring your own client, command scorer, dedup,
  score cache, records and explain, CLI, other packages through ports, hone-models judge.
- AC-21 (`tests/e2e/test_ac21_examples.py`): every example runs offline and is documented and indexed.
- `python -m hone_select.cli` runs the CLI (the same as the `hone-select` command).
- Docs sections link to their examples.

### Fixed (examples set)
- `examples/lyrics_selection.py`: the fake judge answered the pairwise question under the wrong name, so
  the tie-break always chose the first option shown.
- `examples/config_file.py` failed on a second run (the default score cache answered the judge), and the
  CLI example wrote a span store into `examples/cli/`.
- The budget is documented as a stop condition checked before each generation and later cascade stage,
  not a hard cap.

### Changed (examples set)
- `hone_select.cache` and `hone_select.explain` declare `__all__`; `SqliteScoreCache`, `explain_run`,
  `Engine.config` and `Engine.judges` are public ([decision D-010](design/decisions.md)).

### Changed (working with the other honeworks packages)
- Extra `models` = `hone-models>=0.1` (was empty, [decision D-001](design/decisions.md)).
- Gates, scorers, pairwise judges and generators that take a `trace` keyword are called with
  `trace=current_trace()`, so another package's spans (e.g. hone-taste's `for_select`) join the
  selection's trace ([design change 0002](design/changes/0002-trace-to-components.md)).
- Spans copy `hone.lens.finding_id` from the trace context (shared span attributes).

### Removed (cleanup)
- `rich` from the `cli` extra: never imported; `typer` already depends on it.

### Added
- The initial design ([design change 0001](design/changes/0001-initial-design.md)).
- Selection engine: generate -> dedup -> gates -> cascade (`keep_top`) -> aggregate -> select, with
  budgets (cost, seconds, money), variation schedules and seeds.
- Decorators `@generator`, `@gate`, `@scorer`, `@pairwise`; TOML config with validation (`ConfigError`).
- Aggregation (`weighted_mean`, `min`, `geometric`, `weighted_mean_with_floor`) and `missing` policies.
- Selectors `argmax`, `first_above`, `pairwise_tournament`; tie margin and low-confidence escalation to
  order-swapped pairwise; fallbacks `best_rejected` / `first_valid` / `none`.
- `PromptScorer`, `PromptGate`, `PromptPairwise` over the `DecisionClient` port; self-judging warning.
- `CommandScorer` (JSON on stdin/stdout); exact and embedding dedup; SQLite score cache.
- Records: SQLite / JSONL span sinks in the shared honeworks span format (blobs, secret scrubbing, content capture switch),
  W3C trace context (`current_trace()`), `Engine.explain` and `explain_run` from the store alone.
- Adapters: `OpenAIDecisionClient` / `OpenAITextClient` (extra `openai`, also for Ollama's `/v1`),
  `LangChainDecisionClient` / `LangChainTextClient` (extra `langchain`), and the `hone_models:decision`
  entry point for `[judges.*]` config.
- CLI `hone-select run | explain | show` (extra `cli`).
- Docs (`docs/`), runnable examples (`examples/`), and a real-model test suite (`tests/gpu`).
- Public fakes (`FakeDecisionClient`, `FakeTextClient`, `FakeEmbedder`, `MemorySink`) and contract checkers.
