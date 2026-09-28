"""The ``hone-select`` command (extra ``hone-select[cli]``).

hone-select run selection.toml --task task.json --registry mymodule [--json]
hone-select explain <run_id> [--db .hone/select/spans.db]
hone-select show <run_id> [--db ...] [--json]
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any

try:
    import typer
except ModuleNotFoundError as e:  # the command is installed with the core; typer comes with the extra
    raise SystemExit("the hone-select command needs the cli extra: pip install 'hone-select[cli]'") from e

from hone_select._records import hone_home, read_spans
from hone_select.engine import Engine
from hone_select.errors import ConfigError, HoneSelectError
from hone_select.explain import explain_run, load_decision
from hone_select.registry import KINDS, Component
from hone_select.types import Result

app = typer.Typer(help="Generate, score and select the best of N candidates.", no_args_is_help=True)
DbOption = Annotated[Path | None, typer.Option("--db", help="span store; default $HONE_HOME/select/spans.db")]


def registry_items(module_name: str) -> list[Any]:
    """Every decorated function or scorer object defined at the top level of ``module_name``."""
    sys.path.insert(0, str(Path.cwd()))  # so `--registry mymodule` finds mymodule.py in the current folder
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        raise ConfigError(f"cannot import registry module {module_name!r}: {e}; run from its folder") from e
    return [
        value
        for value in vars(module).values()
        if isinstance(value, Component)
        or (not isinstance(value, type) and callable(value) and getattr(value, "kind", None) in KINDS)
    ]


def result_json(result: Result) -> dict[str, Any]:
    """The ``--json`` output of ``run``: ids, winner, ranked table, decision trace and budget."""
    ranked = [dataclasses.asdict(s) for s in result.ranked]
    return {
        "run_id": result.run_id,
        "trace_id": result.trace_id,
        "winner": ranked[result.ranked.index(result.winner)] if result.winner else None,
        "ranked": ranked,
        "decision": result.decision,
        "budget": result.budget,
    }


def _store(db: Path | None) -> Path:
    path = db or hone_home() / "select" / "spans.db"
    if not path.exists():
        raise HoneSelectError(f"no span store at {str(path)!r}; pass --db or set HONE_HOME")
    return path


@app.command()
def run(
    config: Annotated[Path, typer.Argument(help="selection TOML file")],
    task: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="JSON file with the task for the generator")
    ],
    registry: Annotated[list[str], typer.Option(help="module with your decorated functions; repeatable")],
    seed: Annotated[int | None, typer.Option(help="base seed")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print the full result as JSON")] = False,
) -> None:
    """Run a selection and print the winner."""
    items = [item for name in registry for item in registry_items(name)]
    try:
        task_value = json.loads(task.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"--task {str(task)!r} is not valid JSON: {e}") from e
    result = Engine(config, registry=items).run(task_value, seed=seed)
    if as_json:
        typer.echo(json.dumps(result_json(result), indent=2, ensure_ascii=False, default=str))
    elif result.winner is None:
        typer.echo(f"no winner (run {result.run_id}); see: hone-select explain {result.run_id}")
    else:
        winner = result.winner
        typer.echo(f"winner {winner.candidate.id}  total={winner.total}  run={result.run_id}")
        typer.echo(json.dumps(winner.candidate.data, indent=2, ensure_ascii=False, default=str))


@app.command()
def explain(run_id: str, db: DbOption = None) -> None:
    """Re-explain a past run from the span store alone."""
    typer.echo(explain_run(_store(db), run_id))


@app.command()
def show(
    run_id: str,
    db: DbOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="print the spans as JSON")] = False,
) -> None:
    """Show every span recorded for a run."""
    store = _store(db)
    trace_id = load_decision(store, run_id)["trace_id"]
    spans = [s for s in read_spans(store) if s["trace_id"] == trace_id]
    if as_json:
        typer.echo(json.dumps(spans, indent=2, ensure_ascii=False))
        return
    for s in spans:
        status = s["status"]["code"] + (f" ({s['status']['message']})" if s["status"]["message"] else "")
        typer.echo(f"{s['start_time']}  {s['name']}  {s['span_id']}  {status}")


def main() -> None:
    """Entry point: errors of this package print as one line and exit with status 1."""
    try:
        app()
    except HoneSelectError as e:
        typer.echo(f"error: {e}", err=True)
        raise SystemExit(1) from e


if __name__ == "__main__":  # python -m hone_select.cli ... (the same as the hone-select command)
    main()
