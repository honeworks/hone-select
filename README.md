# hone-select

[![CI](https://github.com/honeworks/hone-select/actions/workflows/ci.yml/badge.svg)](https://github.com/honeworks/hone-select/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/honeworks/hone-select/blob/main/LICENSE)
![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)

**Generate, score and select the best of N candidates with pluggable code or prompt scorers.**

Part of **[honeworks](https://github.com/honeworks)**: small, standalone tools for reliable generative-AI
workflows. Works on its own; works better with its siblings.

## Why

Generative models are cheap to sample and unreliable one sample at a time. "Generate a few, keep the
best" works, but done by hand it turns into ad-hoc loops: an LLM judge that silently returns `0` when it
fails, expensive judges run on candidates a regex could have rejected, ties broken by list order, and no
way to say later *why* a candidate won.

You write a generator and a few scorers as plain functions (or ask an LLM judge, or run a script);
hone-select generates candidates, drops the ones that fail your gates, scores the rest in cheap-first
stages, picks a winner by policy, and records every decision so you can explain it later.

## Install
```bash
pip install hone-select              # or: uv add hone-select
```

Until the first release is on PyPI, install from GitHub:
```bash
pip install "git+https://github.com/honeworks/hone-select"
pip install "hone-select[openai,cli] @ git+https://github.com/honeworks/hone-select"   # with extras
```

| Extra | Adds | Install |
|---|---|---|
| *(none)* | engine, plain-function and command scorers, SQLite records | `pip install hone-select` |
| `openai` | LLM judges and text generation via the OpenAI SDK (also Ollama's `/v1` endpoint and other compatible servers) | `pip install "hone-select[openai]"` |
| `langchain` | LLM judges via any LangChain chat model | `pip install "hone-select[langchain]"` |
| `models` | judges from [hone-models](https://github.com/honeworks/hone-models) by registry id (`client = "hone_models:decision"`) | `pip install "hone-select[models]"` |
| `cli` | the `hone-select` command (`run`, `explain`) | `pip install "hone-select[cli]"` |

Python 3.11 or newer. The core depends only on pydantic; no extra pulls in torch or a GPU stack.

## Quickstart
```python
from hone_select import Engine, gate, generator, scorer


@generator()
def write(task, v):
    return f"{task} #{v['index']}" * (v["index"] + 1)


@gate()
def not_too_long(c):
    return len(c.data) < 80


@scorer(cost=1)
def shorter_is_better(c):
    return 1 - len(c.data) / 100


engine = Engine(
    """
[generate]
n = 5
[score]
gates = ["not_too_long"]
cascade = [{ scorers = ["shorter_is_better"] }]
[select]
policy = "argmax"
""",
    registry=[write, not_too_long, shorter_is_better],
)

result = engine.run("hello")
print(result.winner.candidate.data, result.winner.total)  # hello #0 0.92
print(engine.explain(result))
```

Runs offline in well under a second; the same program is
[`examples/quickstart.py`](https://github.com/honeworks/hone-select/blob/main/examples/quickstart.py).
A scorer returns a value in 0..1 (higher is better), a `Score`, or `None` for "could not score". A scorer
that raises gives `Score(None, error=...)` and the run goes on: a failure is never a silent `0`.

## What you get
- **Gates and cascades:** cheap checks reject early; expensive scorers (`keep_top`) only see finalists.
- **Prompt judges:** `PromptScorer`, `PromptGate`, `PromptPairwise` over any `DecisionClient`; pairwise
  judgements always run in both orders and count only when they agree.
- **Command scorers:** any executable, JSON on stdin and stdout.
- **Policies:** `argmax`, `first_above` (stop generating once good enough), `pairwise_tournament`; ties and
  low confidence escalate to a pairwise judge; fallbacks when everything is rejected.
- **Budgets, score cache, dedup, determinism:** cost / time / money caps, cached scores keyed by scorer
  version, exact or embedding dedup, seeded variation schedules.
- **Records:** every step is a span in `.hone/select/spans.db`; `hone-select explain <run_id>` rebuilds
  the decision from the store alone.
- **Experiments:** declare a comparison of models, parameters, prompts or programs over test cases in
  `experiments/E0001-.../experiment.toml`; `hone-select experiments plan` shows every run and an estimate,
  a person approves it (CLI or dashboard), `start` runs and resumes it, and the results say which setup,
  model, parameter or prompt wins, against a baseline, with intervals. The subject can be a prompt, your
  Python function or any command. Run conditions keep a busy machine out of the numbers: the run waits
  for free CPU, RAM and VRAM, unloads models it does not need, holds the GPU lock and marks every sample.
- **Dashboard:** `hone-select dashboard` serves a local web page over the store and the experiments: filterable runs, each
  run's configuration, task and candidate table (variation params, gates, scores, winner, reasons), and
  candidates across runs compared by any variation param.

## Use it with the rest of honeworks
hone-select depends on no other honeworks package. The links are ports (`hone_select.ports`) and shared
records:
- **hone-models** provides judges: `[judges.local] client = "hone_models:decision"` in your config, or
  `Engine(..., judges={"local": hone_models.decision("qwen2.5vl-7b")})` (`hone-select[models]`).
- **hone-taste** audience panels plug in as scorers (any callable returning a `Score`-like object).
- **hone-flow** runs a selection as a step; pass its trace context with `engine.run(task, trace=...)`.
- **hone-lens** reads the span store (`hone.select.*` spans in the shared honeworks span format) for analysis
  and replay.

## Documentation
- [Concepts](https://github.com/honeworks/hone-select/blob/main/docs/concepts.md): the pipeline, scores and `None`, aggregation, selection, budgets
- [Configuration](https://github.com/honeworks/hone-select/blob/main/docs/config.md): every `selection.toml` key
- [Scorers](https://github.com/honeworks/hone-select/blob/main/docs/scorers.md): code, prompt, command and pairwise scorers
- [Bring your own client](https://github.com/honeworks/hone-select/blob/main/docs/adapters.md): OpenAI, Ollama, LangChain, hone-models, your own
- [Records and the CLI](https://github.com/honeworks/hone-select/blob/main/docs/records.md): the span store, trace context, `explain`, the dashboard
- [Experiments](https://github.com/honeworks/hone-select/blob/main/docs/experiments.md): define, plan, approve, run and read a comparison of setups; subjects that are prompts, code or programs
- [Examples](https://github.com/honeworks/hone-select/blob/main/examples/README.md): one runnable, explained example per concept (What / How / Why), each executed by the test suite; the patterns to copy
- [Design](https://github.com/honeworks/hone-select/blob/main/design/README.md): why the package exists, the [current design](https://github.com/honeworks/hone-select/blob/main/design/current.md) with its guarantees, and every [design change](https://github.com/honeworks/hone-select/tree/main/design/changes)
- [Contributing](https://github.com/honeworks/hone-select/blob/main/CONTRIBUTING.md): setup, quality gates and conventions

## Status

Alpha, version 0.1.0. The public API (`hone_select.__all__`), the config format and the span format are
described in [design/current.md](https://github.com/honeworks/hone-select/blob/main/design/current.md) and
covered by acceptance tests, but may still change before 1.0; every change is recorded in the
[changelog](https://github.com/honeworks/hone-select/blob/main/CHANGELOG.md). Issues and pull requests
are welcome.

## How this was built
hone-select was specified by a human and built by AI coding agents (Claude) working against written
specifications and acceptance tests; a human reviewed the decisions they made, and commits written with
AI carry a `Co-Authored-By` line. Every design change, with what was found, what was decided and why, is
in [design/changes/](https://github.com/honeworks/hone-select/tree/main/design/changes); the smaller implementation choices,
including those still awaiting the owner's review, are in [design/decisions.md](https://github.com/honeworks/hone-select/blob/main/design/decisions.md).

## License
Apache-2.0 ([LICENSE](https://github.com/honeworks/hone-select/blob/main/LICENSE)). Copyright 2026 Bahman
Shadmehr. Dependencies and their licences:
[THIRD_PARTY_NOTICES.md](https://github.com/honeworks/hone-select/blob/main/THIRD_PARTY_NOTICES.md).
