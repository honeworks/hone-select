"""The subject under test (design change 0009 §2a): a prompt, a Python function or a command.

`run_sample` runs one sample in its own folder and always returns a result dict: the data, the files
produced, measurements (seconds, peak memory, exit code, output bytes), the log, or the error. A failure
is a result, never an exception.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, cast

from hone_select.errors import ConfigError
from hone_select.experiments.definition import ExperimentSpec, GenerateSpec

LOG_LIMIT = 64 * 1024
TEMPFAIL = 75  # a command exits with 75 (EX_TEMPFAIL) to ask for a retry


class TransientError(Exception):
    """Raise it from a python subject to ask for a retry (`[generate] retries`)."""


class Ctx:
    """What a python subject receives besides the case and the setup."""

    def __init__(self, workdir: Path, seed: int, case_files: dict[str, str]) -> None:
        self.workdir, self.seed, self.case_files = workdir, seed, case_files


class _Attrs(dict[str, Any]):
    """A dict whose keys are also attributes, for `{case.topic}` / `{case.files.scene}` placeholders."""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError as e:
            raise ConfigError(f"placeholder field {name!r} does not exist") from e
        return _Attrs(cast(dict[str, Any], value)) if isinstance(value, dict) else value


def import_object(path: str) -> Any:
    """`module:attribute` -> the attribute (the project root is on sys.path)."""
    module, _, attr = path.partition(":")
    if not attr:
        raise ConfigError(f"{path!r} must look like 'module:name'")
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ConfigError(f"cannot import {path!r}: {e}") from e


class Where:
    """Where a sample runs: the project root (the working directory of commands), the experiment folder
    and the sample's own folder for the files it writes."""

    def __init__(self, root: Path, folder: Path, workdir: Path) -> None:
        self.root, self.folder, self.workdir = root, folder, workdir


def placeholders(case: dict[str, Any], setup: dict[str, Any], where: Where, seed: int) -> dict[str, Any]:
    return {
        "case": _Attrs({"id": case["id"], **case["fields"], "files": case["files"]}),
        "setup": _Attrs(setup),
        "setup_json": json.dumps(setup, sort_keys=True),
        "workdir": str(where.workdir),
        "seed": seed,
        "experiment": str(where.folder),
        "root": str(where.root),
    }


def preview(spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], folder: Path) -> list[str]:
    """What will run, shown in the plan for approval (the exact command for `command` subjects)."""
    g = spec.generate
    if g.kind == "command":
        values = placeholders(
            case, setup, Where(Path("."), folder, folder / "outputs/<case>/<setup>/<sample>/files"), 0
        )
        return [" ".join([*g.wrap, *(a.format_map(values) for a in g.command or [])])]
    if g.kind == "python":
        return [f"python: {g.function}(case, setup, ctx)"]
    return [f"prompt: {g.client}(<model>).complete(<{g.prompt}>)"]


def run_sample(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    """Run one sample (with retries) and describe what happened."""
    workdir = where.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    runner = {"prompt": _prompt, "python": _python, "command": _command}[spec.generate.kind]
    out: dict[str, Any] = {}
    for attempt in range(spec.generate.retries + 1):
        start = time.monotonic()
        out = runner(spec, case, setup, seed, where)
        out.setdefault("measurements", {})["seconds"] = round(time.monotonic() - start, 3)
        if not out.pop("transient", False) or attempt == spec.generate.retries:
            break
    files = _files(workdir)
    out["files"] = files
    out["measurements"]["output_bytes"] = sum(f["size"] for f in files.values())
    return out


def pilot(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], folder: Path, root: Path
) -> dict[str, Any]:
    """One real sample in a throw-away folder, to measure time and cost for the plan."""
    out = run_sample(
        spec, case, setup, spec.seed, Where(root, folder, Path(tempfile.mkdtemp(prefix="hone-pilot-")))
    )
    return {
        "measurements": out["measurements"],
        "cost_usd": out.get("cost_usd", 0.0),
        "error": out.get("error"),
    }


# -- prompt ----------------------------------------------------------------------------------------

_clients: dict[tuple[str, str], Any] = {}


def _client(g: GenerateSpec, model: Any) -> Any:
    key = (str(g.client), str(model))
    if key not in _clients:
        factory: Callable[..., Any] = import_object(str(g.client))
        _clients[key] = factory(model, **g.client_args) if model is not None else factory(**g.client_args)
    return _clients[key]


def _text(template: str | None, values: dict[str, Any], prompts: Path) -> str:
    if not template:
        return ""
    if template.endswith(".md") and (prompts / template).is_file():
        template = (prompts / template).read_text()
    return template.format_map(values).strip()


def _prompt(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    g, prompts = spec.generate, where.folder / "prompts"
    values = _Attrs({"case_id": case["id"], **case["fields"], **setup, "seed": seed})
    if "prompt" in setup:
        values["prompt"] = _text(str(setup["prompt"]), values, prompts)
    messages = [{"role": "system", "content": _text(g.system, values, prompts)}] if g.system else []
    messages.append({"role": "user", "content": _text(g.prompt, values, prompts)})
    params = {
        k: setup[k] for k in (g.params if g.params is not None else setup) if k not in ("model", "prompt")
    }
    try:
        result = _client(g, setup.get("model")).complete(messages, seed=seed, **params)
    except Exception as e:  # a model call that fails is a result; retries apply
        return {"error": f"{type(e).__name__}: {e}", "transient": True}
    text, error = getattr(result, "text", str(result)), getattr(result, "error", None)
    usage = dict(getattr(result, "usage", {}) or {})
    out: dict[str, Any] = {
        "data": text,
        "error": error,
        "log": "",
        "cost_usd": float(usage.get("cost_usd", 0.0)),
    }
    out["measurements"] = {k: v for k, v in usage.items() if isinstance(v, int | float)}
    if g.output == "json" and error is None:
        out["data"], out["error"] = _json(getattr(result, "parsed", None), text)
    return out


def _json(parsed: Any, text: str) -> tuple[Any, str | None]:
    if parsed is not None:
        return (parsed.model_dump() if hasattr(parsed, "model_dump") else parsed), None
    try:
        return json.loads(text), None
    except ValueError:
        return None, "the output is not valid JSON"


# -- python ----------------------------------------------------------------------------------------


def _python(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    """Run the function in its own interpreter (hone_select.experiments._child): killable on timeout, its own
    peak memory, and a crash cannot take the experiment down."""
    result = where.workdir.parent / "subject.json"
    result.unlink(missing_ok=True)
    payload = json.dumps(
        {
            "function": spec.generate.function,
            "case": case,
            "setup": setup,
            "seed": seed,
            "workdir": str(where.workdir),
            "result": str(result),
            "paths": [str(where.root), str(where.folder / "scripts")],
        }
    )
    argv = [*spec.generate.wrap, sys.executable, "-m", "hone_select.experiments._child"]
    env = {**os.environ, **spec.generate.env}
    try:
        code, stdout, stderr, rss = _spawn(argv, payload, env, where.root, spec.generate.timeout)
    except OSError as e:
        return {"error": f"cannot start the subject process: {e}", "measurements": {}}
    log = (stdout + ("\n--- stderr ---\n" + stderr if stderr else ""))[-LOG_LIMIT:]
    if code is None:
        return {
            "error": f"timeout after {spec.generate.timeout:g} s",
            "log": log,
            "measurements": {"peak_memory_mb": rss},
        }
    if not result.is_file():
        return {
            "error": f"the subject process died (exit {code}): {stderr.strip()[-300:]}",
            "log": log,
            "measurements": {"peak_memory_mb": rss},
        }
    out: dict[str, Any] = json.loads(result.read_text())
    result.unlink()
    out["log"] = log
    out["measurements"] = {**out.get("measurements", {}), "peak_memory_mb": rss}
    return out


# -- command ---------------------------------------------------------------------------------------


def _command(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    g, workdir, root = spec.generate, where.workdir, where.root
    values = placeholders(case, setup, where, seed)
    argv = [*g.wrap, *(a.format_map(values) for a in g.command or [])]
    payload = json.dumps(
        {
            "case": {"id": case["id"], **case["fields"], "files": case["files"]},
            "setup": setup,
            "seed": seed,
            "workdir": str(workdir),
        }
    )
    env = {**os.environ, **g.env, "HONE_WORKDIR": str(workdir), "HONE_SEED": str(seed)}
    try:
        code, stdout, stderr, rss = _spawn(argv, payload, env, root, g.timeout)
    except OSError as e:
        return {"error": f"cannot run {argv[0]!r}: {e}", "measurements": {}}
    log = (stdout + ("\n--- stderr ---\n" + stderr if stderr else ""))[-LOG_LIMIT:]
    out: dict[str, Any] = {
        "data": None,
        "error": None,
        "log": log,
        "measurements": {"exit_code": code, "peak_memory_mb": rss},
    }
    reply = _stdout_json(stdout)
    if reply is not None:
        out["data"] = reply.get("data")
        out["measurements"] |= dict(reply.get("measurements", {}))
        out["cost_usd"] = float(reply.get("cost_usd", 0.0))
    if code is None:
        out["error"] = f"timeout after {g.timeout:g} s"
    elif code != 0:
        out["error"] = f"exit code {code}: {stderr.strip()[-300:]}"
        out["transient"] = code == TEMPFAIL
    return out


def _stdout_json(stdout: str) -> dict[str, Any] | None:
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except ValueError:
        return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _spawn(
    argv: list[str], payload: str, env: dict[str, str], cwd: Path, timeout: float
) -> tuple[int | None, str, str, float]:
    """Run a process; (exit code or None on timeout, stdout, stderr, its peak memory in MB) via wait4."""
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
    return code, chunks.get("out", ""), chunks.get("err", ""), rss


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


def _files(workdir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(workdir))] = {
                "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
    return out
