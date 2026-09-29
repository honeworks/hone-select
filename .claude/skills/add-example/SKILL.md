---
name: add-example
description: Add a runnable, explained example to examples/ that the tests execute. Use when a change adds a concept users should see, or the user asks for an example.
---

# Add an example

1. One concept per file: `examples/<name>.py`, in the shape of `examples/aggregation_and_missing.py`:
   - a docstring with **What:**, **How:** and **Why:**, in that order;
   - offline and deterministic: the package's fakes from `hone_select.testing`, no network, no GPU;
2. List it in `examples/README.md`, in reading order.
3. Add its file name to the `expected` set in `tests/e2e/test_ac21_examples.py`.
4. `uv run pytest tests/e2e/test_ac21_examples.py -q`.
