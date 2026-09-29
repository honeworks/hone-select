---
name: add-adapter
description: Add an implementation of one of hone-select's ports, or another optional integration, with its extra, contract test and docs. Use when adding support for a new service, library or data source.
---

# Add an adapter

The ports are in `src/hone_select/ports.py` (`design/current.md` §7): `DecisionClient`, `TextClient`,
`Embedder`, `RecordSink`, `ScoreCache`. A new port, or a change to one, is a design change: `plan-change`
first.

1. **Where.** Adapters go in `src/hone_select/adapters/` (like `src/hone_select/adapters/openai.py`), one
   module per extra.
2. **Optional dependency.** Add an extra in `pyproject.toml`; import the third-party library only inside
   the adapter module, and the module only when it is used. The core must still import without it:
   `tests/unit/test_import_boundaries.py`.
3. **Contract.** Run the checker from `hone_select.testing.contracts` (`check_decision_client`,
   `check_text_client`, `check_embedder`, `check_record_sink`) against it in `tests/contract/`.
4. **Behaviour the port promises.** A judge that can't answer gives `None`, never `0`, and never falls
   back to another service silently.
5. **Tests with recorded HTTP**, never a live service, in the default suite; real calls only in
   `tests/gpu/` (`real-model-tests`).
6. **Entry point** when judges are chosen by name: the `hone.decision_clients` group in `pyproject.toml`.
7. `sync-docs`: the install line, `docs/adapters.md`, an example if it's a new kind of judge.
