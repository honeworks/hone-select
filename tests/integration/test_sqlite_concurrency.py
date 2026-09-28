"""Two processes writing the same span store (WAL) don't corrupt it."""

import subprocess
import sys
import time
from pathlib import Path

from hone_select._records import read_spans

WRITER = """
import sys, secrets
from hone_select._records import SqliteSpanSink
from hone_select.testing.contracts import example_span
sink = SqliteSpanSink(sys.argv[1])
for _ in range(50):
    sink.emit({**example_span(), "span_id": secrets.token_hex(8)})
assert sink.failures == 0, sink.failures
"""


def test_two_writers(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(db)]) for _ in range(2)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    assert len(read_spans(db)) == 100


CREATOR = """
import sys, time, secrets
from pathlib import Path
from hone_select._records import SqliteSpanSink
from hone_select.cache import SqliteScoreCache
from hone_select.testing.contracts import example_span
folder, start = Path(sys.argv[1]), float(sys.argv[2])
for store in range(4):  # every process creates the same fresh stores at the same moment, four times
    time.sleep(max(0.0, start + 0.4 * store - time.time()))
    sink = SqliteSpanSink(folder / f"{store}.db")
    sink.emit({**example_span(), "span_id": secrets.token_hex(8)})
    assert sink.failures == 0, sink.failures
    SqliteScoreCache(folder / f"cache-{store}.db").close()
"""


def test_processes_creating_the_same_fresh_store_never_drop_spans(tmp_path: Path) -> None:
    start = time.time() + 1.5  # after every interpreter has started
    procs = [subprocess.Popen([sys.executable, "-c", CREATOR, str(tmp_path), str(start)]) for _ in range(8)]
    assert [p.wait(timeout=60) for p in procs] == [0] * 8
    assert [len(read_spans(tmp_path / f"{store}.db")) for store in range(4)] == [8] * 4
