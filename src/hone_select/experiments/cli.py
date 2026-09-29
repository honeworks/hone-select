"""`hone-select experiments ...` (extra `cli`; design change 0009 §3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from hone_select.errors import HoneSelectError
from hone_select.experiments import results, runner
from hone_select.experiments.outside import conditions_line
from hone_select.experiments.project import Project, read_json

app = typer.Typer(help="Define, plan, approve, run and read experiments.", no_args_is_help=True)
ProjectOpt = Annotated[Path, typer.Option("--project", help="the project folder (holds experiments/)")]
JsonOpt = Annotated[bool, typer.Option("--json", help="print JSON")]


def _line(s: dict[str, Any]) -> str:
    progress = f"{s['done']}/{s['outputs']}" if s["outputs"] else f"{s['done']}/?"
    return f"{s['eid']}  {s['status']:<10} {progress:>9}  {s['title']}{_why(s)}"


def _why(s: dict[str, Any]) -> str:
    """The first reason a run waits, or why the run conditions stopped it (design change 0010 §4)."""
    run: dict[str, Any] = s.get("run") or {}
    if s["status"] == "waiting":
        waiting: dict[str, Any] = run.get("waiting") or {}
        reasons: list[str] = waiting.get("reasons") or []
        return f"  ({reasons[0]})" if reasons else ""
    if s["status"] == "stopped" and run.get("stopped_because"):
        return f"  (stopped: {run['stopped_because'][0]})"
    return ""


@app.command()
def new(title: str, project: ProjectOpt = Path(".")) -> None:
    """Create the next experiment's folder with a commented experiment.toml."""
    folder = Project(project).new(title)
    typer.echo(
        f"created {folder}\nedit {folder / 'experiment.toml'}, then: hone-select experiments plan "
        f"{folder.name.split('-', 1)[0]}"
    )


@app.command()
def plan(
    eid: str,
    pilot: Annotated[
        bool, typer.Option("--pilot", help="run one real sample to measure time and cost")
    ] = False,
    project: ProjectOpt = Path("."),
) -> None:
    """Validate the definition, expand every cell, estimate, write plan.json (status: proposed)."""
    p = Project(project).plan(eid, pilot=pilot)
    est = p["estimate"]
    typer.echo(
        f"{p['eid']}: {len(p['cases'])} cases x {len(p['setups'])} setups x {p['samples']} samples = "
        f"{p['outputs']} outputs, {p['judge_calls']} judge calls"
    )
    typer.echo(f"estimate: {est['seconds']} s, ${est['money_usd']} ({est['from']})")
    for command in p["commands"]:
        typer.echo(f"runs: {command}")
    if "conditions" in p:
        typer.echo(f"run conditions now: {p['conditions']['now']['would']}")
    for cell in p.get("applicability", {}).get("not_applicable", []):
        typer.echo(f"not applicable: {cell['case']} x {cell['setup']}: {'; '.join(cell['unmet'])}")
    for model, entry in p.get("models", {}).items():
        if entry["installed"] == "no":
            typer.echo(f"not installed: {model} (start refuses until it is): {entry['install']}")
    typer.echo("approve it in the dashboard or: hone-select experiments approve " + p["eid"])


@app.command()
def approve(
    eid: str,
    note: Annotated[str, typer.Option(help="why")] = "",
    by: Annotated[str | None, typer.Option(help="who decides (default: your user name)")] = None,
    project: ProjectOpt = Path("."),
) -> None:
    """Approve the current plan."""
    entry = Project(project).review(eid, "approved", note, by)
    typer.echo(f"{eid} approved by {entry['by']}; start it with: hone-select experiments start {eid}")


@app.command()
def deny(
    eid: str,
    note: Annotated[str, typer.Option(help="why (required)")],
    by: Annotated[str | None, typer.Option(help="who decides (default: your user name)")] = None,
    project: ProjectOpt = Path("."),
) -> None:
    """Deny the current plan."""
    Project(project).review(eid, "denied", note, by)
    typer.echo(f"{eid} denied: {note}")


@app.command()
def start(eid: str, project: ProjectOpt = Path(".")) -> None:
    """Run an approved experiment (again: resume where it stopped)."""
    p = Project(project)
    total = p.status(eid)["outputs"]

    def progress(r: dict[str, Any]) -> None:
        mark = "error: " + r["error"] if r.get("error") else f"{r['measurements'].get('seconds', 0):.1f} s"
        status = r.get("environment", {}).get("status", "not_checked")
        flag = f"  [{status}]" if status in ("outside", "unknown") else ""
        typer.echo(f"  {r['sample_id']}  {mark}{flag}")

    typer.echo(f"{eid}: {total} outputs")
    status = runner.start(p, eid, on_sample=progress)
    typer.echo(_line(status))
    if status["status"] == "completed":
        typer.echo(f"results: {p.path(eid) / 'results' / 'summary.md'}")


@app.command()
def stop(eid: str, project: ProjectOpt = Path(".")) -> None:
    """Ask a running experiment to stop after the current sample."""
    runner.stop(Project(project), eid)
    typer.echo(f"{eid}: stop requested")


@app.command()
def status(
    eid: Annotated[str | None, typer.Argument()] = None,
    project: ProjectOpt = Path("."),
    as_json: JsonOpt = False,
) -> None:
    """One experiment's status, or every experiment's."""
    p = Project(project)
    rows = [p.status(eid)] if eid else p.list()
    if as_json:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    for row in rows:
        typer.echo(_line(row))


@app.command(name="list")
def list_(project: ProjectOpt = Path("."), as_json: JsonOpt = False) -> None:
    """Every experiment in the project."""
    status(None, project, as_json)


@app.command()
def report(
    eid: str,
    project: ProjectOpt = Path("."),
    include_outside: Annotated[
        bool, typer.Option("--include-outside", help="count samples that ran outside the run conditions")
    ] = False,
) -> None:
    """(Re)write results/ from the outputs (after rating human criteria, for example)."""
    p = Project(project)
    folder, spec, _ = p.load(eid)
    plan_ = read_json(folder / "plan.json")
    if plan_ is None:
        raise HoneSelectError(f"{eid} has no plan yet: run `hone-select experiments plan {eid}` first")
    res = results.report(folder, spec, plan_, include_outside=include_outside)
    line = conditions_line(res)
    if line:
        typer.echo(line)
    typer.echo(f"best setup: {res['best']} {res['setups'][res['best']]['params'] if res['best'] else ''}")
    typer.echo(f"results: {folder / 'results' / 'summary.md'}")
