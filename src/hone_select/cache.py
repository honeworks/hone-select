"""Score cache: reuse a score when the candidate, scorer name, scorer version and judge are unchanged."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from hone_select._records import enable_wal, hone_home
from hone_select.config import RecordConfig
from hone_select.types import Score

__all__ = ["SqliteScoreCache"]  # public (design/decisions.md D-010); default_cache is for the engine

Key = tuple[str, str, str, str]  # (candidate id, scorer name, scorer version, judge model id)


class SqliteScoreCache:
    """Scores in a small SQLite table. Only clean values are cached; failures are retried next time."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout=5000")
        enable_wal(self._db)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS scores (candidate_id TEXT, scorer TEXT, version TEXT, judge TEXT,"
                " score TEXT NOT NULL, PRIMARY KEY (candidate_id, scorer, version, judge))"
            )

    def get(self, key: Key) -> Score | None:
        row = self._db.execute(
            "SELECT score FROM scores WHERE candidate_id=? AND scorer=? AND version=? AND judge=?", key
        ).fetchone()
        if row is None:
            return None
        return Score(**json.loads(row[0]))

    def put(self, key: Key, score: Score) -> None:
        data = json.dumps(dataclasses.asdict(score), default=str)
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?)", (*key, data))

    def close(self) -> None:
        self._db.close()


def default_cache(record: RecordConfig) -> SqliteScoreCache:
    """``cache.db`` next to the configured SQLite span store, else ``$HONE_HOME/select/cache.db``."""
    if record.sink == "sqlite" and record.path:
        return SqliteScoreCache(Path(record.path).parent / "cache.db")
    return SqliteScoreCache(hone_home() / "select" / "cache.db")
