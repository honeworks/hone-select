# AGENTS.md: hone-select

Notes for contributors who use AI coding tools (Claude Code, Codex, Cursor and others). Human
contributors: the same rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## What this is

`hone-select` (import `hone_select`): generate, score and select the best of N candidates with pluggable
code or prompt scorers. Read before changing behaviour:

- [`design/current.md`](design/current.md): the design and the acceptance cases it guarantees;
- [`design/changes/`](design/changes/): accepted and implemented design changes;
- [`design/decisions.md`](design/decisions.md): smaller implementation choices, numbered (code comments
  cite them as `D-00N`).

## Commands

```bash
uv sync --all-extras                        # install everything, dev tools included
scripts/check.sh                            # all quality gates; "green" means this exits 0
uv run pytest                               # default suite: fast, offline, deterministic
uv run pytest tests/e2e                     # acceptance cases only
scripts/gpu-lock.sh uv run pytest -m gpu    # real-model tests, through the machine-wide GPU lock
uv run ruff check . && uv run ruff format . # lint and format
uv run pyright                              # types (strict for src/)
```

## Layout

```
src/hone_select/     public API in __init__.py (explicit __all__)
  engine.py          the pipeline: generate, dedup, gates, cascade, aggregate, select, record
  ports.py           the Protocols this package owns (design/current.md §7)
  adapters/          optional integrations, imported lazily, one module per extra
  testing/           public fakes for every port, and contract checkers
  _tracing.py        trace context;  _records.py  span sinks (design/current.md §8)
tests/unit|contract|integration|e2e|gpu
docs/  examples/  design/  scripts/
```

## Rules

1. **Keep it simple:** the simplest code that passes the acceptance cases; no speculative abstractions;
   complexity ≤ 10 per function, about 40 lines per function, about 300 per module.
2. **Useful alone:** the core never imports an optional extra or another honeworks package; integrations go
   through ports and lazily imported adapters.
3. **Explicit failure:** "could not score" is `None`, never `0`; typed exceptions with messages that say
   what to do.
4. **Records:** span names and attributes exactly as in `design/current.md` §8; never record secrets.
5. **Determinism:** explicit seeds; `hashlib`, never `hash()`, for ids.
6. **Tests first** for public behaviour; each acceptance case has a `tests/e2e/test_ac<N>_*` test; the
   README, `docs/` and `examples/` are executed by the suite.
7. **Green means `scripts/check.sh` passes.** Never weaken a test to make it pass.
8. **GPU:** anything that loads a real model runs through `scripts/gpu-lock.sh` and unloads the model
   afterwards.
9. **Design changes** get a record in `design/changes/` (see `CONTRIBUTING.md`); smaller choices go into
   `design/decisions.md`.
10. **Git:** Conventional Commits, small commits, each one green. Never push, tag or publish unless the
    maintainer asks.
