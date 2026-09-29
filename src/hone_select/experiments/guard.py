"""Keeping a run inside its conditions (design change 0010 §2, §4, §6, §8, §9): the GPU lock for the whole
run, the check between samples (the probe's `prepare`, warm-up, a reading, the judgement), waiting (shown
in `run.json`, ended by `STOP` or `wait_timeout`) and each sample's `environment`."""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from hone_select.errors import HoneSelectError
from hone_select.experiments import checks, conditions, gpulock, needs
from hone_select.experiments.definition import ExperimentSpec
from hone_select.experiments.project import write_json

T = TypeVar("T")
PREPARED = ("blocked_by", "unloaded", "released", "errors", "missing", "need_gb", "error")
FIX = (
    "make it readable (install the NVIDIA driver tools for GPU readings, set probe = "
    '"hone_models:machine" for model state) or remove the key from [conditions]'
)


class RunStoppedError(Exception):
    """The run stops: the conditions did not hold (`reasons`), or a person asked (`STOP`: reasons None)."""

    def __init__(self, reasons: list[str] | None) -> None:
        super().__init__("; ".join(reasons or ["stop requested"]))
        self.reasons = reasons


class StartRefusedError(HoneSelectError):
    """`start` refuses: a declared condition cannot be measured on this machine."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(f"a declared run condition cannot be measured: {'; '.join(reasons)}. {FIX}")
        self.reasons = reasons


@dataclass
class Check:
    """One check between samples: what it prepared for, the reading and the judgement."""

    needed: list[str]
    reading: dict[str, Any]
    found: dict[str, checks.Check]
    prepared: dict[str, Any] | None = None
    loads: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    cold: bool = False

    @property
    def holds(self) -> bool:
        return checks.holds(self.found)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Guard:
    """The conditions of one run. `run` is the `run.json` content, updated while the run waits."""

    def __init__(
        self, spec: ExperimentSpec, folder: Path, run: dict[str, Any], src: conditions.Sources
    ) -> None:
        self.c, self.folder, self.run, self.src = spec.conditions, folder, run, src
        self.probe = needs.probe_for(spec, src)
        self.lock = gpulock.lock_path(self.c.gpu_lock)
        self.lock_state: str | None = None
        self.fd: int | None = None
        self.first = True
        self._held_before: str | None = None

    # -- the GPU lock ------------------------------------------------------------------------------

    def take(self, eid: str) -> list[str]:
        """Take the lock now; the reasons it could not be taken otherwise."""
        assert self.lock is not None  # noqa: S101 - only called with a lock path
        if gpulock.held_by_parent():
            self.lock_state = gpulock.BY_PARENT
            return []
        fd = gpulock.try_lock(self.lock)
        if fd is None:
            return [f"another process holds the GPU lock: {gpulock.holder(self.lock)}"]
        self.fd, self.lock_state = fd, gpulock.BY_RUN
        gpulock.write_holder(self.lock, self.folder.parent.parent, eid)
        self._held_before = os.environ.get(gpulock.HELD)
        os.environ[gpulock.HELD] = "1"  # the probe and every subject see that the lock is held
        return []

    def hold_lock(self, eid: str) -> None:
        """Wait for the GPU lock (in every mode, up to `wait_timeout`) and hold it until `release`."""
        if self.lock is not None:
            self._wait_for(lambda: (None, self.take(eid)))

    def release(self) -> None:
        if self.fd is not None and self.lock is not None:
            gpulock.release(self.fd, self.lock)
            self.fd = None
            if self._held_before is None:
                os.environ.pop(gpulock.HELD, None)
            else:
                os.environ[gpulock.HELD] = self._held_before

    # -- one check -----------------------------------------------------------------------------------

    def check(self, needed: list[str], *, prepare: bool) -> Check:
        """`prepare` for the next sample (unload what it does not need, warm up what it needs), then a
        reading and the judgement. Without `prepare` (after the last sample) only the reading."""
        use = prepare and self.probe is not None
        prepared = self._prepare(needed) if use and self.c.only_needed_models else None
        window = self.src.window if self.c.max_cpu_load is not None else conditions.SHORT_WINDOW
        reading = conditions.read(self.src, self.probe, window=window, lock=self.lock_state)
        checks.free_for_run(reading, needed if self.probe is not None else [])
        missing = self._missing(needed, prepared, reading) if use else []
        loads = self._warm_up(missing)
        cold = any(x.get("loaded") is not True for x in loads) if loads else bool(missing)
        found = checks.judge(self.c, reading, needed, prepared, loads)
        return Check(needed, reading, found, prepared, loads, cold)

    def _prepare(self, needed: list[str]) -> dict[str, Any]:
        try:
            return dict(self.probe.prepare(needed, if_busy=self.c.if_busy))
        except Exception as e:  # a failing probe is an unknown reading, never a crash
            if self.first:
                raise StartRefusedError([f"only_needed_models: the probe's prepare failed: {e}"]) from e
            return {"error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _missing(needed: list[str], prepared: dict[str, Any] | None, reading: dict[str, Any]) -> list[str]:
        if prepared is not None and "missing" in prepared:
            return [str(m) for m in prepared["missing"]]
        loaded = {m.get("model_id") for m in reading.get("loaded_models", [])}
        return [m for m in needed if m not in loaded] if "loaded_models" in reading else []

    def _warm_up(self, missing: list[str]) -> list[dict[str, Any]]:
        if not (self.c.warm_up and missing and hasattr(self.probe, "load")):
            return []
        loads: list[dict[str, Any]] = []
        for model in missing:
            try:
                loads.append({"model_id": model, **dict(self.probe.load(model))})
            except Exception as e:  # a failed warm-up leaves the sample cold
                loads.append({"model_id": model, "loaded": False, "error": f"{type(e).__name__}: {e}"})
        return loads

    # -- waiting -------------------------------------------------------------------------------------

    def settle(self, first: Check, recheck: Callable[[], Check]) -> Check:
        """A check that lets the next sample run: at once when the conditions hold (or `record_only`),
        after waiting (`wait`), never (`stop`: raises `RunStoppedError`). Unknown at the first check
        refuses the start."""
        if self.first:
            self.first = False
            unknown = [v["reason"] for v in first.found.values() if v["state"] == checks.UNKNOWN]
            if unknown:
                raise StartRefusedError(unknown)
        if first.holds or self.c.on_violation == "record_only":
            return first
        if self.c.on_violation == "stop":
            raise RunStoppedError(checks.reasons(first.found))

        def attempt() -> tuple[Check, list[str]]:
            again = recheck()
            return again, checks.reasons(again.found)

        return self._wait_for(attempt, (first, checks.reasons(first.found)))

    def _wait_for(
        self, attempt: Callable[[], tuple[T, list[str]]], first: tuple[T, list[str]] | None = None
    ) -> T:
        """Poll `attempt` until it gives no reasons; `waiting` in run.json meanwhile; STOP or the timeout
        raise `RunStoppedError`."""
        result, why = first if first is not None else attempt()
        if not why:
            return result
        since = self.src.clock()
        until, outcome = since + self.c.wait_timeout, "timed out"
        self.run |= {
            "state": "waiting",
            "waiting": {"since": _iso(since), "until": _iso(until), "reasons": why},
        }
        write_json(self.folder / "run.json", self.run)
        try:
            while True:
                self.src.sleep(self.src.poll)
                if (self.folder / "STOP").is_file():
                    outcome = "stopped"
                    raise RunStoppedError(None)
                result, why = attempt()
                if not why:
                    outcome = "conditions met"
                    return result
                if self.src.clock() >= until:
                    raise RunStoppedError(why)
                self.run["waiting"]["reasons"] = why
                write_json(self.folder / "run.json", self.run)
        finally:
            wait = {"since": _iso(since), "ended": _iso(self.src.clock()), "outcome": outcome, "reasons": why}
            self.run["waits"] = [*self.run.get("waits", []), wait]
            self.run["state"] = "running"
            self.run.pop("waiting", None)
            write_json(self.folder / "run.json", self.run)

    # -- the record ------------------------------------------------------------------------------------

    def environment(self, before: Check, after: Check, attempt: int) -> dict[str, Any]:
        """A sample's `environment`: both readings, each condition's state, what was prepared."""
        found = checks.merge(before.found, after.found)
        env: dict[str, Any] = {
            "status": checks.status(found, bool(self.c.declared())),
            "before": before.reading,
            "after": after.reading,
            "checks": found,
        }
        if before.prepared is not None:
            env["prepared"] = {k: before.prepared[k] for k in PREPARED if k in before.prepared}
        if before.loads:
            env["warm_up"] = before.loads
        return env | {"cold": before.cold, "attempt": attempt}


@contextmanager
def pilot(
    spec: ExperimentSpec, folder: Path, needed: list[str], src: conditions.Sources
) -> Generator[None, None, None]:
    """`plan --pilot` under the run's rules, without waiting: refused when the lock is taken or a
    condition does not hold."""
    guard = Guard(spec, folder, {}, src)
    try:
        why = guard.take(folder.name.split("-", 1)[0]) if guard.lock is not None else []
        found = [] if why else checks.reasons(guard.check(needed, prepare=True).found)
        if why or found:
            raise HoneSelectError(
                f"the pilot is refused, the run conditions do not hold: {'; '.join(why + found)}"
            )
        yield
    finally:
        guard.release()
