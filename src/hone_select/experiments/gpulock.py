"""The machine-wide GPU lock, held for a whole run (design change 0010 §8).

The same `flock` as every honeworks repo's `scripts/gpu-lock.sh`: `$HONE_GPU_LOCK`, default
`/tmp/honeworks-gpu.lock`, and a `<lock>.holder` file that says who holds it. The kernel releases the lock
when the process dies, so a crash never leaves the GPU locked.
"""

from __future__ import annotations

import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LOCK = "/tmp/honeworks-gpu.lock"  # noqa: S108 - the family's machine-wide lock, by design
HELD = "HONE_GPU_LOCK_HELD"  # set by whoever holds the lock, for its children
BY_RUN, BY_PARENT, FREE = "held by this run", "held by the parent process", "free"


def lock_path(value: bool | str) -> Path | None:
    """`[conditions] gpu_lock`: true is the family's path ($HONE_GPU_LOCK or the default), a string a path."""
    if value is True:
        return Path(os.environ.get("HONE_GPU_LOCK", DEFAULT_LOCK))
    return Path(value) if isinstance(value, str) and value else None


def held_by_parent() -> bool:
    return os.environ.get(HELD) == "1"


def try_lock(path: Path) -> int | None:
    """The lock's file descriptor when this process got the lock, None when another process holds it."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def holder(path: Path) -> str:
    try:
        return Path(f"{path}.holder").read_text().strip() or "unknown holder"
    except OSError:
        return "unknown holder"


def write_holder(path: Path, root: Path, eid: str) -> None:
    """`<project folder> <pid> <time> <experiment>`, the format of gpu-lock.sh plus the experiment id."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    Path(f"{path}.holder").write_text(f"{root.name} {os.getpid()} {stamp} {eid}\n")


def release(fd: int, path: Path) -> None:
    Path(f"{path}.holder").unlink(missing_ok=True)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def state_now(path: Path) -> str:
    """Who holds the lock right now, without keeping it (for the plan)."""
    if held_by_parent():
        return BY_PARENT
    fd = try_lock(path)
    if fd is None:
        return f"held by {holder(path)}"
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return FREE
