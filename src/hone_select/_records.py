"""Span sinks (design/current.md §8.2): SQLite (default), JSON lines, memory and null. They never raise."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from hone_select._tracing import now
from hone_select.config import RecordConfig
from hone_select.errors import ConfigError
from hone_select.ports import RecordSink

log = logging.getLogger("hone_select")
BLOB_LIMIT = 64 * 1024
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'internal',
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  status_code    TEXT NOT NULL DEFAULT 'unset',
  status_message TEXT NOT NULL DEFAULT '',
  attributes     TEXT NOT NULL DEFAULT '{}',
  events         TEXT NOT NULL DEFAULT '[]',
  resource       TEXT NOT NULL DEFAULT '{}',
  links          TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_name_time ON spans(name, start_time);
CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, mime TEXT, size INTEGER, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, span_id TEXT NOT NULL,
                                    op TEXT NOT NULL, at TEXT NOT NULL);
"""


def hone_home() -> Path:
    """``$HONE_HOME`` or ``.hone`` in the current directory."""
    return Path(os.environ.get("HONE_HOME") or ".hone")


def scrub(value: Any) -> Any:
    """Replace anything that looks like an API key or bearer token with ``***``, recursively."""
    if isinstance(value, str):
        return _SECRET.sub("***", value)
    if isinstance(value, Mapping):
        return {k: scrub(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class _SafeSink:
    """Shared failure handling: log the first failure to stderr, count all of them, never raise."""

    failures: int = 0

    def _failed(self, error: Exception) -> None:
        if self.failures == 0:
            print(f"hone-select: record sink failed ({error}); further failures are counted", file=sys.stderr)
            log.error("record sink failed: %s", error)
        self.failures += 1


def enable_wal(db: sqlite3.Connection, attempts: int = 50) -> None:
    """Switch to WAL. The switch ignores busy_timeout when another process is creating the same store at
    the same moment, so retry briefly."""
    for attempt in range(attempts):
        try:
            db.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1)


class SqliteSpanSink(_SafeSink):
    """Writes spans into the SQLite schema of design/current.md §8.2 (WAL mode, several writers allowed)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout=5000")
        enable_wal(self._db)
        with self._db:
            self._db.executescript(SCHEMA)
            rows = [
                ("schema", "hone-spans"),
                ("schema_version", "1"),
                ("package", "hone-select"),
                ("created_at", now()),
            ]
            self._db.executemany("INSERT OR IGNORE INTO meta VALUES (?, ?)", rows)

    def emit(self, span: Mapping[str, Any]) -> None:
        try:
            clean: dict[str, Any] = scrub(span)
            attributes = {k: self._maybe_blob(v) for k, v in clean["attributes"].items()}
            status: dict[str, Any] = clean.get("status") or {}
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        clean["span_id"],
                        clean["trace_id"],
                        clean.get("parent_span_id"),
                        clean["name"],
                        clean.get("kind", "internal"),
                        clean["start_time"],
                        clean.get("end_time"),
                        status.get("code", "unset"),
                        status.get("message", ""),
                        json.dumps(attributes, default=str),
                        json.dumps(clean.get("events", []), default=str),
                        json.dumps(clean.get("resource", {})),
                        json.dumps(clean.get("links", [])),
                    ),
                )
                self._db.execute(
                    "INSERT INTO changes (span_id, op, at) VALUES (?, 'insert', ?)", (clean["span_id"], now())
                )
        except Exception as e:
            self._failed(e)

    def _maybe_blob(self, value: Any) -> Any:
        """Values over 64 KiB (as JSON) go to the ``blobs`` table and are referenced by hash."""
        text = json.dumps(value, default=str)
        if len(text) <= BLOB_LIMIT:
            return value
        data = text.encode()
        digest = hashlib.sha256(data).hexdigest()
        self._db.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?, 'application/json', ?, ?)", (digest, len(data), data)
        )
        return {"$blob": digest}

    def flush(self) -> None:
        pass  # every emit commits

    def close(self) -> None:
        self._db.close()


class JsonlSpanSink(_SafeSink):
    """Appends one JSON span per line."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, span: Mapping[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(scrub(span), default=str) + "\n")
        except Exception as e:
            self._failed(e)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class NullSink:
    """Discards spans (``[record] sink = "none"``)."""

    def emit(self, span: Mapping[str, Any]) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class MemorySink:
    """Keeps spans in a list (``sink.spans``) for tests."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def emit(self, span: Mapping[str, Any]) -> None:
        self.spans.append(scrub(span))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def named(self, name: str) -> list[dict[str, Any]]:
        """Spans with this name, in emit order."""
        return [s for s in self.spans if s["name"] == name]


def default_sink(config: RecordConfig) -> RecordSink:
    """The sink named in ``[record]``; default path ``$HONE_HOME/select/spans.db`` (or ``spans.jsonl``)."""
    if config.sink == "none":
        return NullSink()
    if config.sink == "jsonl":
        return JsonlSpanSink(config.path or hone_home() / "select" / "spans.jsonl")
    path = config.path or hone_home() / "select" / "spans.db"
    try:
        return SqliteSpanSink(path)
    except (OSError, sqlite3.Error) as e:
        raise ConfigError(
            f"cannot open the span store {str(path)!r}: {e}; set [record] path or HONE_HOME to a writable "
            'folder, or [record] sink = "none"'
        ) from e


def _resolve_blob(value: Any, blobs: Mapping[str, Any]) -> Any:
    """``{"$blob": sha}`` -> the stored value; anything else as is."""
    if isinstance(value, dict) and "$blob" in value:
        return blobs.get(str(cast("dict[str, Any]", value)["$blob"]), value)
    return cast(Any, value)


def read_spans(
    path: str | Path, *, name: str | None = None, parent_span_id: str | None = None
) -> list[dict[str, Any]]:
    """Spans in a SQLite store (optionally only one name / parent), as JSON (design/current.md §8.1)."""
    db = sqlite3.connect(Path(path))
    db.row_factory = sqlite3.Row
    try:
        blobs = {sha: json.loads(data) for sha, data in db.execute("SELECT sha256, data FROM blobs")}
        query = (
            "SELECT * FROM spans WHERE (:name IS NULL OR name = :name)"
            " AND (:parent IS NULL OR parent_span_id = :parent) ORDER BY start_time, rowid"
        )
        spans: list[dict[str, Any]] = []
        for row in db.execute(query, {"name": name, "parent": parent_span_id}):
            span = dict(row)
            span["status"] = {"code": span.pop("status_code"), "message": span.pop("status_message")}
            for key in ("events", "resource", "links"):
                span[key] = json.loads(span[key])
            attributes: dict[str, Any] = json.loads(span["attributes"])
            span["attributes"] = {k: _resolve_blob(v, blobs) for k, v in attributes.items()}
            spans.append(span)
        return spans
    finally:
        db.close()
