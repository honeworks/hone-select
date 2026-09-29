---
name: sync-docs
description: Update every file a change affects - design/current.md, docs/, README, examples, CHANGELOG, AGENTS.md, the change record. Use after implementing any change and before verify-before-done; reviewers use the same table to find missing updates.
---

# Keep the docs in sync

Look at `git diff main...HEAD --stat` and the diff itself, then apply every row that matches:

| If the change... | Update |
|---|---|
| changes behaviour or the public API | `design/current.md` (the section, §2 guarantees, §10 acceptance cases), the matching `docs/*.md` page, `README.md` if the quickstart or feature list changes |
| adds or changes a scorer kind or normalizer | `docs/scorers.md`, `design/current.md` §4 |
| adds or changes configuration keys | `docs/config.md`, `design/current.md` §6 |
| adds or changes a CLI command or flag | `design/current.md` §9, the CLI part of `README.md` |
| adds or changes span names or attributes | `docs/records.md`, `design/current.md` §8 |
| adds an extra or a dependency | `pyproject.toml`, the install section of `README.md`, `docs/adapters.md`, the reason in `design/decisions.md` |
| adds, moves or renames a module | the "Layout" of `AGENTS.md`, `design/current.md` §4 |
| adds a concept users should know | a new example (`add-example`) |
| is visible to users | `CHANGELOG.md`, top (unreleased) section: Added / Changed / Deprecated / Removed / Fixed, linking the change record |
| implements a change record | its status: `implemented in <next version>` |
| is a judgment call without a record | `design/decisions.md`, next `D-` number |
| changes a rule or command contributors use | `CONTRIBUTING.md`, `AGENTS.md`, and the skill in `.claude/skills/` that describes it |

Rules:
- The code in `README.md` and `docs/` runs in `tests/e2e/test_docs_examples.py`; write blocks that must not run
  as `text`.
- Write for users of the package: plain words, the style of the page around it, no internal notes.
- Run `uv run pytest tests/e2e/test_docs_examples.py tests/e2e/test_ac21_examples.py -q` when done.
