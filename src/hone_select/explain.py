"""Human-readable explanations of a decision, from a Result or from the span store alone."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hone_select._records import read_spans
from hone_select.errors import HoneSelectError
from hone_select.types import Scored

__all__ = ["explain_run"]  # public (design/decisions.md D-010); the other functions serve the engine and CLI


def ranked_rows(ranked: Sequence[Scored]) -> list[dict[str, Any]]:
    """The ranked table as stored on the decision span (``hone.select.ranked``)."""
    return [
        {"id": s.candidate.id, "total": s.total, "rejected": s.rejected, "stage_reached": s.stage_reached}
        for s in ranked
    ]


def _describe(entry: dict[str, Any]) -> str:
    details = ", ".join(f"{k}={v}" for k, v in entry.items() if k != "event")
    return f"{entry['event']}: {details}" if details else entry["event"]


def explain_rows(rows: Sequence[dict[str, Any]], decision: Sequence[dict[str, Any]], run_id: str) -> str:
    """Format a ranked table and a decision trace as plain text."""
    lines = [f"run {run_id}", "ranking:"]
    for place, row in enumerate(rows, start=1):
        total = "None" if row["total"] is None else f"{row['total']:.4f}"
        flag = "  (rejected)" if row["rejected"] else ""
        lines.append(f"  {place}. {row['id']}  total={total}  stage={row['stage_reached']}{flag}")
    lines.append("decision trace:")
    lines.extend(f"  - {_describe(entry)}" for entry in decision)
    return "\n".join(lines)


def load_decision(db_path: str | Path, run_id: str) -> dict[str, Any]:
    """The ``hone.select.decision`` span of ``run_id`` (the ``hone.select.run`` span id) from a store."""
    for span in read_spans(db_path, name="hone.select.decision", parent_span_id=run_id):
        return span
    raise HoneSelectError(
        f"no decision for run {run_id!r} in {str(db_path)!r}; check the run id and --db path"
    )


def explain_run(db_path: str | Path, run_id: str) -> str:
    """Re-explain a past run from the span store alone."""
    attributes = load_decision(db_path, run_id)["attributes"]
    return explain_rows(attributes["hone.select.ranked"], attributes["hone.select.decision_trace"], run_id)
