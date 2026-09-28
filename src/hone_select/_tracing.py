"""Trace context (design/current.md §7.1) and span creation (§8.1)."""

from __future__ import annotations

import logging
import os
import re
import secrets
import socket
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from hone_select._version import __version__
from hone_select.ports import RecordSink

log = logging.getLogger("hone_select")
SCHEMA_VERSION = "1"
# design/current.md §8.3: copied from the trace context onto every span
# (a hone-lens replay carries its finding id)
SHARED_KEYS = (
    "hone.run_id",
    "hone.item",
    "hone.step",
    "hone.candidate_id",
    "hone.scorer",
    "hone.lens.finding_id",
)
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")
RESOURCE = {
    "service.name": os.environ.get("OTEL_SERVICE_NAME", "hone-select"),
    "hone.package": "hone-select",
    "hone.package.version": __version__,
    "host.name": socket.gethostname(),
    "process.pid": os.getpid(),
}
_current: ContextVar[Mapping[str, str] | None] = ContextVar("hone_select_trace", default=None)


def current_trace() -> dict[str, str]:
    """The active trace context; pass it as ``trace=`` when calling another package."""
    return dict(_current.get() or {})


def now() -> str:
    """ISO-8601 UTC with milliseconds, e.g. ``2026-09-27T14:03:11.120Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_traceparent(value: str | None) -> tuple[str | None, str | None]:
    """``(trace_id, parent_span_id)`` from a W3C traceparent, or ``(None, None)``."""
    match = _TRACEPARENT.match(value or "")
    return (match.group(1), match.group(2)) if match else (None, None)


@contextmanager
def span(
    sink: RecordSink,
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    trace: Mapping[str, str] | None = None,
    context: Mapping[str, str] | None = None,
) -> Generator[dict[str, Any]]:
    """Open a span, make it the current trace context, and emit it to ``sink`` on exit.

    ``trace`` replaces the inherited context (an incoming caller context); ``context`` adds keys such
    as ``hone.candidate_id`` for nested calls. The yielded dict's ``attributes`` may be extended.
    """
    ctx = {**(dict(trace) if trace is not None else current_trace()), **(context or {})}
    trace_id, parent_id = parse_traceparent(ctx.get("traceparent"))
    if ctx.get("traceparent") and trace_id is None:
        log.warning("ignoring malformed traceparent %r; starting a new trace", ctx["traceparent"])
    trace_id = trace_id or secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    shared = {key: ctx[key] for key in SHARED_KEYS if key in ctx}
    record: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_id,
        "name": name,
        "kind": "internal",
        "start_time": now(),
        "end_time": None,
        "status": {"code": "ok", "message": ""},
        "attributes": {"hone.schema_version": SCHEMA_VERSION, **shared, **(attributes or {})},
        "events": [],
        "resource": dict(RESOURCE),
        "links": [],
    }
    token = _current.set({**ctx, "traceparent": f"00-{trace_id}-{span_id}-01"})
    try:
        yield record
    except BaseException as e:
        record["status"] = {"code": "error", "message": f"{type(e).__name__}: {e}"}
        raise
    finally:
        _current.reset(token)
        record["end_time"] = now()
        sink.emit(record)
