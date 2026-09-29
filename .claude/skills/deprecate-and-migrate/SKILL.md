---
name: deprecate-and-migrate
description: Change or remove public API, CLI flags or stored formats without breaking users - deprecation warnings, a compatibility period, migrations. Use when a change renames, removes or changes the meaning of anything users or their saved data depend on.
---

# Deprecate and migrate

Users and their saved data outlive any version. Break nothing silently.

**Public API and CLI**
1. Keep the old name working for at least one minor release: it calls the new one and warns with
   `warnings.warn("<old> is deprecated, use <new>; it will be removed in <version>", DeprecationWarning, stacklevel=2)`.
   CLI: the same message on stderr.
2. Docs and examples show only the new form. `CHANGELOG.md`: **Deprecated** now, **Removed** when it goes.
3. Tests: the old form still works and warns; the new form works.

**Configuration files and stored data** (`docs/config.md`, `design/current.md` §6, §8.2, §5.12)
1. A new optional configuration key is fine. Renaming or removing one keeps the old name working with a
   warning for a release, or fails with a `ConfigError` that names the new key.
2. The span store and the score cache: new columns or attributes are optional; anything else is a change
   record with a "Migration and compatibility" section, and an old store either still opens or is
   refused with a `HoneSelectError` that says what to do.
3. Keep a small sample of the old format in `tests/fixtures/` and test both reading it and the refusal.
