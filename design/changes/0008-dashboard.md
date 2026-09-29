# 0008: A local dashboard over the span store

## Status

`implemented in 0.1.0` (requested by the owner 2026-09-29)

## Context

Found while running experiments for OneShotStudio (a honeworks app), where hone-select compares setups
(models, temperatures, prompt versions as variations) over many tasks. Everything needed to understand
those runs is already in the span store: every candidate with its variation, every gate and score with
its reason, pairwise judgements and the decision. But the only ways to read it are `hone-select explain`
(one run as text) and `show --json`. Going through dozens of runs, comparing candidates across them and
following one candidate's scores means writing SQL.

## Problem

1. There is no way to browse runs: list them, filter them, open one and see its candidates side by side.
2. A run does not record what was tested: the run span holds only a hash of the configuration, and nothing
   about the task.

## Options

1. **A dashboard in hone-select (chosen):** `hone-select dashboard` serves a small read-only web page from
   the span store, using only the standard library.
2. Export to an existing tool (Phoenix, Langfuse): heavier setup, and those tools know spans, not
   selections (gates, cascades, winners).
3. A notebook or script per experiment: repeated work, and no shared view.

## Decision

- **`hone-select dashboard [--db PATH] [--host 127.0.0.1] [--port 8788] [--open]`** (extra `cli`) serves:
  - **Runs:** every run, newest first: time, run id, policy, n, candidates, winner and its total, fallback /
    escalation, duration, the trace context it ran in (`hone.run_id`, `hone.item`, `hone.step`); a text
    filter over every column, sortable columns.
  - **A run:** its configuration and task (when recorded), budget used, the candidate table (variation
    params as columns, gate results, one column per scorer, total, rank, stage reached, rejected, winner),
    filterable and sortable, with every reason, gate detail, error and data preview a click away; the
    pairwise judgements and the decision trace.
  - **Candidates across runs:** every candidate of every run in one filterable table, and a group-by on any
    variation param (e.g. `model`) giving candidates, wins and mean total per value: how experiments that
    use variations are read.
- The page is one static HTML file with plain JavaScript, shipped in the package; the server is
  `http.server` answering `GET /`, `/api/runs` and `/api/runs/<run id>` with JSON built from the store. It
  is **read-only**, listens on localhost by default and never writes to the store.
- **Records:** the `hone.select.run` span gains `hone.select.config` (the validated configuration as JSON;
  values of keys named like a key, token, secret, password, authorization or credential are replaced with
  `***`, and prompt text (`criteria`, `anchors`) is content, hashed when capture is off) and `hone.select.task` (a preview of the task, first 2,000 characters; hashed like other content when
  content capture is off). Older runs show "not recorded".
- The data layer is public for other tools: `hone_select.dashboard.list_runs(db)`,
  `run_detail(db, run_id)` and `all_candidates(db)` return plain JSON-ready values.

## Consequences

- Runs can be read and compared without SQL, and an experiment's "which setup wins" is one group-by.
- One more command and ~400 lines (a module and an HTML file); no new dependencies.
- The page reads the whole store per request: fine for thousands of runs, not for millions (a later change
  can page or index if that ever matters).

## Migration and compatibility

Additive. Existing stores work unchanged (runs without the new attributes show "not recorded"); the new
run-span attributes are optional for readers. Schema version stays `1`.
