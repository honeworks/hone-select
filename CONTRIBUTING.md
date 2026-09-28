# Contributing to hone-select

Thanks for helping. This file has the conventions the code follows; the design itself is in
[`design/`](design/README.md).

## Setup and the quality gates

```bash
uv sync --all-extras          # install everything, dev tools included
scripts/check.sh              # all gates: ruff, format, pyright, tests + coverage, build, wheel smoke test
uv run pytest                 # the default suite: fast, offline, deterministic
uv run pytest tests/e2e       # the acceptance cases only
```

"Green" means `scripts/check.sh` exits 0. Never weaken a test to make it pass.

The development tools (pytest, ruff, pyright and friends) are in the `dev` dependency group, which
`uv sync` installs by default; they are not a published extra. Until hone-models is on PyPI, uv resolves
the `models` extra from [its GitHub repository](https://github.com/honeworks/hone-models) (a uv git source
in `pyproject.toml`, used for development only). To work on both packages at once, point that source at a
local checkout for your session, e.g. `uv add --editable ../hone-models --optional models`, and do not
commit the change.

## Keep it simple

Simple, easy to understand, maintainable code comes first. When simplicity conflicts with anything except
correctness and the acceptance cases, simplicity wins.

- Build the simplest thing that passes the acceptance cases in [`design/current.md`](design/current.md).
- No speculative generality: add an abstraction only with two real uses today, or where the design names
  an extension point (a port, an entry point, a user-supplied function).
- Plain Python first: functions and small dataclasses; composition over inheritance; a dict of functions
  over a class hierarchy; the standard library over dependencies.
- Flat and explicit: short call chains, no magic (metaclasses, import hooks, monkey-patching).
- One obvious way to configure and call each thing.
- Names over comments; comments say *why*.
- Delete dead code, unused parameters and one-caller helper layers.

Limits, checked in review: cyclomatic complexity ≤ 10 per function (ruff `C901`), about 40 lines per
function, about 300 lines per module, at most 6 parameters. Core dependencies are the standard library
and pydantic; a new one needs an entry in [`design/decisions.md`](design/decisions.md) saying why.

## Code conventions

1. **Useful alone.** The core never imports an optional extra or another honeworks package
   (`tests/unit/test_import_boundaries.py` checks this). Integrations go through the ports in
   `src/hone_select/ports.py` and adapters in `src/hone_select/adapters/`, imported lazily, one module per
   extra.
2. **Ports.** A port is a small `typing.Protocol` owned by this package, with a public fake in
   `hone_select.testing` and a contract checker in `hone_select.testing.contracts`. Changing a port's
   signature is a breaking change.
3. **Explicit failure.** Never swallow an error silently. "Could not score" is `None`, never `0`.
   Exceptions that cross the public API are typed (`HoneSelectError` and subclasses), and messages say
   what happened and what to do.
4. **Records.** Spans use the names and attributes in [`design/current.md`](design/current.md) §8. Never
   record secrets.
5. **Determinism.** Seeds are explicit. Never use the built-in `hash()` for ids; use `hashlib`.
6. **Style.** `ruff` (line length 110), `pyright` strict for `src/`, `pathlib` for paths,
   `datetime.now(UTC)`, `logging.getLogger("hone_select")` (no prints in library code), config as
   pydantic models with unknown keys rejected.

## Tests

| Suite | Folder | Runs by default | Purpose |
|---|---|---|---|
| Unit | `tests/unit/` | yes | each module's behaviour, edge cases, error paths |
| Contract | `tests/contract/` | yes | the fakes and checkers of every port |
| Integration | `tests/integration/` | yes | modules together with real local resources (SQLite, subprocesses) |
| Acceptance | `tests/e2e/` | yes | the acceptance cases, through the public API and CLI |
| Real model | `tests/gpu/` | no | acceptance cases against a real local model |

- Write tests first for public behaviour.
- Every acceptance case in [`design/current.md`](design/current.md) §10 has a test named
  `test_ac<N>_<slug>` in `tests/e2e/`, using only the public API. It must fail if the feature is removed.
- The README, every Python block in `docs/` and every file in `examples/` are executed by the suite.
  Examples open with a What / How / Why docstring and are listed in `examples/README.md`.
- Recorded HTTP fixtures live in `tests/fixtures/http/`; default tests never touch the network.
- Coverage on `src/` stays at 90 % or more.

### Real-model tests and `scripts/gpu-lock.sh`

Tests that load a real model are marked `gpu` and are not part of the default suite. Run them through
the lock script, which serializes GPU use across every process on the machine:

```bash
scripts/gpu-lock.sh uv run pytest -m gpu
```

They need a local Ollama with an OpenAI-compatible `/v1` endpoint; the model comes from
`HONE_TEST_TEXT_MODEL` and the server from `HONE_TEST_OLLAMA_URL`. When the server or model is missing
they skip with a reason. They unload the model afterwards.

## Changing the design

Design changes are written down before they are built:

1. Write `design/changes/NNNN-<short-name>.md` with status `proposed` and the sections Status, Context,
   Problem, Options, Decision, Consequences, and Migration and compatibility.
2. The maintainer reviews it; the status becomes `accepted` (or `rejected`).
3. Implement it from [`design/current.md`](design/current.md) and the accepted change records, tests
   first.
4. Update `design/current.md`, set the record to `implemented in <version>`, and add a `CHANGELOG.md`
   entry that links to it.

Smaller implementation choices that need no change record go into
[`design/decisions.md`](design/decisions.md).

## Commits

Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`), subject
of at most 72 characters, a body that explains why, one logical change per commit, and `scripts/check.sh`
green at every commit. Commits written with an AI tool end with a `Co-Authored-By:` line naming it.
Never commit secrets, `.hone/` stores, model weights or large binaries.

Contributors using AI coding tools will find a short brief for them in [`AGENTS.md`](AGENTS.md).
