"""Experiments in a project folder (design change 0009 §2-3): create, find, plan, review and status.

Status comes from the files, never from a stored flag that could disagree with them:
no `plan.json` -> draft; a plan for an older definition -> draft (plan again); plan, no decision -> proposed;
the last decision on this plan -> approved / denied; `run.json` -> running / stopped / completed.
"""

from __future__ import annotations

import getpass
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hone_select.errors import ConfigError, HoneSelectError
from hone_select.experiments import cases as cases_mod
from hone_select.experiments import definition as d
from hone_select.experiments.template import TEMPLATE

EXPERIMENTS = "experiments"
EID = re.compile(r"^E(\d{4})-")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, value: Any) -> None:
    """Write JSON atomically (a reader never sees half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.is_file() else default


class Project:
    """The experiments of one project: `<root>/experiments/E0001-slug/`."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).resolve()
        self.folder = self.root / EXPERIMENTS

    def eids(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(
            p.name.split("-", 1)[0] for p in self.folder.iterdir() if p.is_dir() and EID.match(p.name)
        )

    def path(self, eid: str) -> Path:
        """The folder of `eid` (`E0001`, or the full folder name)."""
        short = eid.split("-", 1)[0]
        found = sorted(self.folder.glob(f"{short}-*")) if self.folder.is_dir() else []
        if not found:
            raise HoneSelectError(
                f"no experiment {eid!r} in {str(self.folder)!r}; see `hone-select experiments list`"
            )
        return found[0]

    def new(self, title: str) -> Path:
        """Create the next experiment's folder with a commented template."""
        numbers = [int(m.group(1)) for p in self.folder.glob("E*-*") if (m := EID.match(p.name))] or [0]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "experiment"
        folder = self.folder / f"E{max(numbers) + 1:04d}-{slug}"
        for sub in ("cases", "prompts", "scripts"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / d.DEFINITION).write_text(TEMPLATE.format(title=title.replace('"', "'")))
        (folder / "cases" / "cases.toml").write_text(
            '[[case]]\nid = "example"\ntopic = "a lighthouse keeper"\n'
        )
        (folder / "prompts" / "plain.md").write_text("Write a short story about {topic}.\n")
        return folder

    def load(self, eid: str) -> tuple[Path, d.ExperimentSpec, list[cases_mod.Case]]:
        folder = self.path(eid)
        spec = d.load(folder)
        return folder, spec, cases_mod.load(folder, spec.cases, self.folder)

    # -- plan and review --------------------------------------------------------------------------

    def plan(self, eid: str, *, pilot: bool = False) -> dict[str, Any]:
        """Validate, expand every cell, estimate, and write `plan.json` (status: proposed)."""
        from hone_select.experiments import selection, subjects  # noqa: PLC0415 - they import this module

        folder, spec, cases = self.load(eid)
        setups = d.setups(spec)
        selection.check_criteria(spec, self.root)
        outputs = len(cases) * len(setups) * spec.samples
        plan: dict[str, Any] = {
            "eid": folder.name.split("-", 1)[0],
            "title": spec.title,
            "question": spec.question,
            "definition_hash": d.definition_hash(folder),
            "planned_at": now(),
            "cases": [c["id"] for c in cases],
            "setups": {d.setup_id(s): s for s in setups},
            "baselines": d.baselines(spec),
            "samples": spec.samples,
            "outputs": outputs,
            "judge_calls": outputs * sum(1 for s in spec.scorers.values() if s.get("kind") == "prompt"),
            "subject": spec.generate.kind,
            "commands": subjects.preview(spec, cases[0], setups[0], folder),
            "estimate": {
                "seconds": None,
                "money_usd": None,
                "from": "unknown (run `plan --pilot` to measure)",
            },
        }
        if pilot:
            sample = subjects.pilot(spec, cases[0], setups[0], folder, self.root)
            plan["pilot"] = sample
            per_s, per_usd = sample["measurements"].get("seconds", 0.0), sample.get("cost_usd")
            plan["estimate"] = {
                "seconds": round(per_s * outputs, 1),
                "money_usd": round(per_usd * outputs, 4) if per_usd is not None else None,  # unknown, not $0
                "from": "one pilot sample (generation only; judges not included)",
            }
        write_json(folder / "plan.json", plan)
        return plan

    def review(self, eid: str, decision: str, note: str = "", by: str | None = None) -> dict[str, Any]:
        """Approve or deny the current plan (`review.json` keeps every decision)."""
        if decision not in ("approved", "denied"):
            raise ConfigError(f"a decision is 'approved' or 'denied', not {decision!r}")
        folder = self.path(eid)
        state = self.status(eid)["status"]
        if state != "proposed":
            raise HoneSelectError(
                f"{eid} is {state}; only a proposed experiment can be reviewed (run `plan` first)"
            )
        plan = read_json(folder / "plan.json")
        entry = {
            "decision": decision,
            "note": note,
            "by": by or _who(),
            "at": now(),
            "definition_hash": plan["definition_hash"],
        }
        write_json(folder / "review.json", [*read_json(folder / "review.json", []), entry])
        return entry

    # -- status ----------------------------------------------------------------------------------

    def status(self, eid: str) -> dict[str, Any]:
        folder = self.path(eid)
        spec = d.load(folder)
        plan, reviews = read_json(folder / "plan.json"), read_json(folder / "review.json", [])
        run = read_json(folder / "run.json")
        current = d.definition_hash(folder)
        done = sum(1 for _ in (folder / "outputs").glob("*/*/*/result.json"))
        out: dict[str, Any] = {
            "eid": folder.name.split("-", 1)[0],
            "folder": folder.name,
            "title": spec.title,
            "question": spec.question,
            "done": done,
            "outputs": plan["outputs"] if plan else None,
            "run": run,
            "changed_since_plan": bool(plan) and plan["definition_hash"] != current,
        }
        decision = next(
            (r for r in reversed(reviews) if plan and r["definition_hash"] == plan["definition_hash"]), None
        )
        if not plan or out["changed_since_plan"]:
            out["status"] = "draft"
        elif run and run.get("definition_hash") == plan["definition_hash"]:
            out["status"] = _run_state(run)
        else:
            out["status"] = decision["decision"] if decision else "proposed"
        out["review"] = decision
        return out

    def list(self) -> list[dict[str, Any]]:
        return [self.status(eid) for eid in self.eids()]


def _run_state(run: dict[str, Any]) -> str:
    if run.get("state") == "running" and not _alive(run.get("pid")):
        return "stopped"  # the process died without saying so
    return str(run.get("state"))


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _who() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):  # no user name in some containers
        return "unknown"
