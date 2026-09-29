"""Running a subject's process (design change 0009 §2a): stdin JSON, stdout / stderr, a timeout that kills,
its own peak memory from `wait4`, the files it wrote, and a log with secrets scrubbed."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, cast

from hone_select._records import scrub

LOG_LIMIT = 64 * 1024
TEMPFAIL = 75  # a process exits with 75 (EX_TEMPFAIL) to ask for a retry


@dataclass(frozen=True, slots=True)
class Finished:
    code: int | None  # None: killed at the timeout
    stdout: str
    stderr: str
    peak_memory_mb: float

    def log(self) -> str:
        """stdout, then stderr, capped and with anything that looks like a secret replaced by ***."""
        text = self.stdout + ("\n--- stderr ---\n" + self.stderr if self.stderr else "")
        return str(scrub(text[-LOG_LIMIT:]))

    def tail(self) -> str:
        return str(scrub(self.stderr.strip()[-300:]))


def environment(extra: Mapping[str, str], **fixed: str) -> dict[str, str]:
    """The parent's environment plus `extra`, where `$NAME` / `${NAME}` take the parent's value (so a
    definition names a secret instead of containing it)."""
    return {**os.environ, **{k: os.path.expandvars(v) for k, v in extra.items()}, **fixed}


def run(argv: list[str], payload: str, env: dict[str, str], cwd: Path, timeout: float) -> Finished:
    """Run a process with `payload` on stdin. Raises OSError when it cannot start."""
    proc = subprocess.Popen(  # noqa: S603 - the command of an experiment a person approved
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    chunks: dict[str, str] = {}

    def read(name: str, stream: IO[str] | None) -> None:
        chunks[name] = stream.read() if stream is not None else ""

    readers = [
        threading.Thread(target=read, args=(n, s), daemon=True)
        for n, s in (("out", proc.stdout), ("err", proc.stderr))
    ]
    for r in readers:
        r.start()
    try:
        if proc.stdin is not None:
            proc.stdin.write(payload)
            proc.stdin.close()
    except BrokenPipeError:
        pass
    code, rss = _wait(proc, timeout)
    for r in readers:
        r.join(5)
    return Finished(code, chunks.get("out", ""), chunks.get("err", ""), rss)


def _wait(proc: subprocess.Popen[str], timeout: float) -> tuple[int | None, float]:
    """Reap the process with wait4 (its own peak memory); kill it at the timeout (exit code None)."""
    deadline = time.monotonic() + timeout
    while True:
        pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
        if pid:
            code: int | None = os.waitstatus_to_exitcode(status)
            break
        if time.monotonic() > deadline:
            proc.kill()
            _, _, usage = os.wait4(proc.pid, 0)
            code = None
            break
        time.sleep(0.02)
    proc.returncode = 0  # reaped by wait4; keeps Popen from waiting again
    return code, round(usage.ru_maxrss / 1024, 1)


def stdout_json(stdout: str) -> dict[str, Any] | None:
    """The last non-empty stdout line, when it is a JSON object (a command's reply)."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except ValueError:
        return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def files(workdir: Path) -> dict[str, dict[str, Any]]:
    """Every file under `workdir`: size and sha256."""
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(workdir))] = {
                "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
    return out


def cost(value: Any) -> float | None:
    """A cost in USD when the subject reported one; None (unknown), never 0, otherwise."""
    return float(value) if isinstance(value, int | float) else None
