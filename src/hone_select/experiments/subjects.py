"""The subject under test (design change 0009 §2a): a prompt, a Python function or a command (a generation
model is in `hone_select.experiments.generation`, design change 0011).

`run_sample` runs one sample in its own folder and always returns a result dict: the data, the files
produced, measurements (seconds, peak memory, exit code, output bytes), the log, or the error. A failure
is a result, never an exception; only a configuration mistake (a client that cannot be built) stops the run.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from hone_select._records import scrub
from hone_select.errors import ConfigError
from hone_select.experiments import guides, overrides, process
from hone_select.experiments.definition import ExperimentSpec, GenerateSpec


class TransientError(Exception):
    """Raise it from a python subject to ask for a retry (`[generate] retries`)."""


class Ctx:
    """What a python subject receives besides the case and the setup."""

    def __init__(
        self,
        workdir: Path,
        seed: int,
        case_files: dict[str, str],
        model_guide: dict[str, Any] | None = None,
    ) -> None:
        self.workdir, self.seed, self.case_files = workdir, seed, case_files
        self.model_guide = model_guide  # the setup's model guide from the plan (design change 0011 §4)


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
    """Where a sample runs: the project root (the working directory of commands), the experiment folder,
    the sample's own folder for the files it writes, and the guide the plan stored for its model."""

    def __init__(
        self, root: Path, folder: Path, workdir: Path, guide: Mapping[str, Any] | None = None
    ) -> None:
        self.root, self.folder, self.workdir, self.guide = root, folder, workdir, guide


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
    if g.kind == "generate":
        inputs = ", ".join(f"{k}={v}" for k, v in g.inputs.items())
        return [f"generate: {g.client}(<model>).generate(<{g.prompt}>, out=<workdir>/{g.output}, {inputs})"]
    return [f"prompt: {g.client}(<model>).complete(<{g.prompt}>)"]


def run_sample(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    """Run one sample (with retries of transient failures) and describe what happened. The subject sees
    the case as the setup's model is asked (its `per_model` override, design change 0011 §2)."""
    from hone_select.experiments import generation  # noqa: PLC0415 - generation imports this module

    workdir = where.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    case = overrides.case_for(case, overrides.model_of(spec, setup))
    runner = {"prompt": _prompt, "python": _python, "command": _command, "generate": generation.run}[
        spec.generate.kind
    ]
    out: dict[str, Any] = {}
    for attempt in range(spec.generate.retries + 1):
        start = time.monotonic()
        out = runner(spec, case, setup, seed, where)
        out.setdefault("measurements", {})["seconds"] = round(time.monotonic() - start, 3)
        if not out.pop("transient", False) or attempt == spec.generate.retries:
            break
    produced = process.files(workdir)
    out["files"] = produced
    out["measurements"]["output_bytes"] = sum(f["size"] for f in produced.values())
    out.setdefault("cost_usd", None)
    if out.get("error"):
        out["error"] = str(scrub(out["error"]))
    return out


def pilot(spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], where: Where) -> dict[str, Any]:
    """One real sample in a throw-away folder (`where.workdir`), to measure time and cost for the plan."""
    from hone_select.experiments import generation  # noqa: PLC0415 - generation imports this module

    try:
        out = run_sample(spec, case, setup, spec.seed, where)
    finally:
        generation.end_session()
    return {"measurements": out["measurements"], "cost_usd": out["cost_usd"], "error": out.get("error")}


# -- prompt ----------------------------------------------------------------------------------------

_clients: dict[tuple[str, str], Any] = {}


def client(g: GenerateSpec, model: Any) -> Any:
    """The prompt subject's client for `model`, built once. A client that cannot be built is a
    `ConfigError` that stops the run (not a per-sample failure)."""
    key = (str(g.client), str(model))
    if key not in _clients:
        factory: Callable[..., Any] = import_object(str(g.client))
        try:
            _clients[key] = factory(model, **g.client_args) if model is not None else factory(**g.client_args)
        except Exception as e:  # a factory that rejects its arguments is a configuration mistake
            raise ConfigError(f"[generate] client {g.client!r} could not be built for {model!r}: {e}") from e
    return _clients[key]


def render(template: str | None, values: dict[str, Any], where: Where, model: str | None) -> str:
    """A template, or the prompt file it names (`prompts/<model>/<file>` first), with `values` filled."""
    if not template:
        return ""
    if template.endswith(".md") and (
        found := overrides.prompt_file(where.folder / "prompts", template, model)
    ):
        template = found.read_text()
    return template.format_map(values).strip()


def prompt_values(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> _Attrs:
    """What a prompt template can name: the case's fields, the setup, the seed, `{model_guide}` and
    `{prompt}` (the text of the prompt file the setup names)."""
    values = _Attrs(
        {
            "case_id": case["id"],
            **case["fields"],
            **setup,
            "seed": seed,
            "model_guide": guides.text(where.guide),
        }
    )
    if "prompt" in setup:
        values["prompt"] = render(str(setup["prompt"]), values, where, overrides.model_of(spec, setup))
    return values


def _prompt(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    g, model = spec.generate, overrides.model_of(spec, setup)
    values = prompt_values(spec, case, setup, seed, where)
    messages = [{"role": "system", "content": render(g.system, values, where, model)}] if g.system else []
    messages.append(
        {"role": "user", "content": render(overrides.generate_for(g, model)[0], values, where, model)}
    )
    params = {
        k: setup[k] for k in (g.params if g.params is not None else setup) if k not in ("model", "prompt")
    }
    model_client = client(g, setup.get("model"))  # outside the try: a bad client stops the run
    try:
        result = model_client.complete(messages, seed=seed, **params)
    except TransientError as e:
        return {"error": f"TransientError: {e}", "transient": True}
    except Exception as e:  # a model call that fails is a result
        return {"error": f"{type(e).__name__}: {e}"}
    text, error = getattr(result, "text", str(result)), getattr(result, "error", None)
    usage = dict(getattr(result, "usage", {}) or {})
    out: dict[str, Any] = {
        "data": text,
        "error": error,
        "log": "",
        "cost_usd": process.cost(usage.get("cost_usd")),
    }
    out["measurements"] = {k: v for k, v in usage.items() if isinstance(v, int | float) and k != "cost_usd"}
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
    g, result = spec.generate, where.workdir.parent / "subject.json"
    result.unlink(missing_ok=True)
    payload = json.dumps(
        {
            "function": g.function,
            "case": case,
            "setup": setup,
            "seed": seed,
            "workdir": str(where.workdir),
            "result": str(result),
            "paths": [str(where.root), str(where.folder / "scripts")],
            "model_guide": where.guide,
        }
    )
    argv = [*g.wrap, sys.executable, "-m", "hone_select.experiments._child"]
    try:
        done = process.run(argv, payload, process.environment(g.env), where.root, g.timeout)
    except OSError as e:
        return {"error": f"cannot start the subject process: {e}", "measurements": {}}
    base: dict[str, Any] = {"log": done.log(), "measurements": {"peak_memory_mb": done.peak_memory_mb}}
    if done.code is None:
        return base | {"error": f"timeout after {g.timeout:g} s"}
    try:
        out: dict[str, Any] = json.loads(result.read_text())
        result.unlink()
    except (OSError, ValueError):  # no result, or half a result: the process died while writing it
        return base | {"error": f"the subject process died (exit {done.code}): {done.tail()}"}
    return out | {"log": base["log"], "measurements": {**out.get("measurements", {}), **base["measurements"]}}


# -- command ---------------------------------------------------------------------------------------


def _command(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: Where
) -> dict[str, Any]:
    g = spec.generate
    values = placeholders(case, setup, where, seed)
    argv = [*g.wrap, *(a.format_map(values) for a in g.command or [])]
    payload = json.dumps(
        {
            "case": {"id": case["id"], **case["fields"], "files": case["files"]},
            "setup": setup,
            "seed": seed,
            "workdir": str(where.workdir),
        }
    )
    env = process.environment(g.env, HONE_WORKDIR=str(where.workdir), HONE_SEED=str(seed))
    try:
        done = process.run(argv, payload, env, where.root, g.timeout)
    except OSError as e:
        return {"error": f"cannot run {argv[0]!r}: {e}", "measurements": {}}
    out: dict[str, Any] = {
        "data": None,
        "error": None,
        "log": done.log(),
        "measurements": {"exit_code": done.code, "peak_memory_mb": done.peak_memory_mb},
    }
    reply = process.stdout_json(done.stdout)
    if reply is not None:
        out["data"] = reply.get("data")
        out["measurements"] |= dict(reply.get("measurements", {}))
        out["cost_usd"] = process.cost(reply.get("cost_usd"))
    if done.code is None:
        out["error"] = f"timeout after {g.timeout:g} s"
    elif done.code != 0:
        out["error"] = f"exit code {done.code}: {done.tail()}"
        out["transient"] = done.code == process.TEMPFAIL
    return out
