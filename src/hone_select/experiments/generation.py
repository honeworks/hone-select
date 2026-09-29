"""The generate subject (design change 0011 §1): one call of an image, music or video client per sample.

`client = "hone_models:music"` resolves through the entry-point group `hone.music_clients` (`image`,
`video` alike; the name before the colon is the entry point's), else as a "module:factory". Each sample
calls `client(model).generate(prompt, out=<workdir>/<output>, seed=, timeout_s=, trace=, **inputs)`; the
result's files are the candidate, its `elapsed_s` and `cost_usd` the measurements, its `error` a failed
sample with `error_kind`. A client with `session()` keeps one session open for consecutive samples of one
model; the runner ends it when the model changes and at the end of the run.
"""

from __future__ import annotations

import re
import string
from collections.abc import Callable
from contextlib import ExitStack
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from hone_select.errors import ConfigError
from hone_select.experiments import overrides, process, subjects
from hone_select.experiments.definition import ExperimentSpec, GenerateSpec

KINDS = ("image", "music", "video")
WHOLE = re.compile(r"^\{([^{}]+)\}$")  # an input that is one placeholder keeps the value's type
FILE_KEYS = ("sha256", "bytes", "mime", "width", "height", "duration_s")


def factory(name: str) -> Callable[..., Any]:
    """`<entry point>:<kind>` from `hone.<kind>_clients`, else `module:factory`."""
    prefix, _, kind = name.partition(":")
    if kind in KINDS:
        found = entry_points(group=f"hone.{kind}_clients", name=prefix)
        if found:
            return next(iter(found)).load()
    return subjects.import_object(name)


_clients: dict[tuple[str, str], Any] = {}


def client(g: GenerateSpec, model: str | None) -> Any:
    """The client for `model`, built once; one that cannot be built is a `ConfigError` (stops the run)."""
    key = (str(g.client), str(model))
    if key not in _clients:
        make = factory(str(g.client))
        try:
            _clients[key] = make(model, **g.client_args) if model is not None else make(**g.client_args)
        except Exception as e:  # a factory that rejects its arguments is a configuration mistake
            raise ConfigError(f"[generate] client {g.client!r} could not be built for {model!r}: {e}") from e
    return _clients[key]


class _Session:
    """The one open client session: kept while samples of the same model follow each other."""

    def __init__(self) -> None:
        self.key: tuple[str, str] | None = None
        self.stack = ExitStack()

    def use(self, key: tuple[str, str], c: Any) -> None:
        if key == self.key:
            return
        self.end()
        if hasattr(c, "session"):
            self.stack.enter_context(c.session())
            self.key = key

    def end(self) -> None:
        self.key = None
        stack, self.stack = self.stack, ExitStack()
        stack.close()


SESSION = _Session()


def end_session() -> None:
    """Close the open session (the model is freed); the runner calls it when the model changes."""
    SESSION.end()


def _value(value: Any, fill: dict[str, Any], files: set[str]) -> Any:
    """An input with placeholders filled; a value that names a case file becomes a `Path`."""
    if isinstance(value, list):
        return [_value(v, fill, files) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(value, str):
        return value
    whole = WHOLE.match(value)
    out = string.Formatter().get_field(whole[1], (), fill)[0] if whole else value.format_map(fill)
    return Path(out) if isinstance(out, str) and out in files else out


def request(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: subjects.Where
) -> tuple[str, dict[str, Any]]:
    """The prompt and the inputs of one sample, after the model's overrides. Raises KeyError,
    AttributeError or ConfigError when a placeholder names nothing."""
    model = overrides.model_of(spec, setup)
    template, inputs = overrides.generate_for(spec.generate, model)
    prompt = subjects.render(template, subjects.prompt_values(spec, case, setup, seed, where), where, model)
    fill = subjects.placeholders(case, setup, where, seed)
    files = {str(f) for f in case["files"].values()}
    return prompt, {k: _value(v, fill, files) for k, v in inputs.items()}


def run(
    spec: ExperimentSpec, case: dict[str, Any], setup: dict[str, Any], seed: int, where: subjects.Where
) -> dict[str, Any]:
    g, model = spec.generate, overrides.model_of(spec, setup)
    prompt, inputs = request(spec, case, setup, seed, where)
    c = client(g, model)
    trace = {
        "hone.run_id": where.folder.name.split("-", 1)[0],
        "hone.item": case["id"],
        "hone.step": "experiment",
    }
    try:
        SESSION.use((str(g.client), str(model)), c)
        result = c.generate(
            prompt, out=where.workdir / g.output, seed=seed, timeout_s=g.timeout, trace=trace, **inputs
        )
    except Exception as e:  # a call that fails is a result
        return {"error": f"{type(e).__name__}: {e}", "error_kind": None, "measurements": {}}
    return described(result, where.workdir, overrides.judge_view(case))


def _file(f: Any, workdir: Path) -> dict[str, Any]:
    path = Path(getattr(f, "path", f))
    name = str(path.relative_to(workdir)) if path.is_relative_to(workdir) else path.name
    return {"name": name, **{k: getattr(f, k, None) for k in FILE_KEYS}}


def described(result: Any, workdir: Path, case_view: dict[str, Any]) -> dict[str, Any]:
    """A `MediaResult` as a sample: the files (the data judges see, with the case's shared fields), the
    measurements, the error and its kind, the cost and whether it is estimated, the license."""
    error = getattr(result, "error", None)
    cost = process.cost(getattr(result, "cost_usd", None))
    elapsed = getattr(result, "elapsed_s", None)
    measurements = {
        k: v for k, v in (("elapsed_s", elapsed), ("cost_usd", cost)) if isinstance(v, int | float)
    }
    return {
        "data": {
            "case": case_view,
            "files": [_file(f, workdir) for f in cast(list[Any], getattr(result, "files", None) or [])],
        },
        "error": str(error) if error else None,
        "error_kind": (getattr(result, "error_kind", None) or "failed") if error else None,
        "log": "",
        "cost_usd": cost,
        "cost_estimated": bool(getattr(result, "cost_estimated", False)),
        "license": getattr(result, "license", None),
        "commercial_use": getattr(result, "commercial_use", None),
        "job_id": getattr(result, "job_id", None),
        "measurements": measurements,
    }


def check(
    spec: ExperimentSpec, cases: list[dict[str, Any]], setups: list[dict[str, Any]], folder: Path
) -> None:
    """Plan time: every prompt and input of a prompt or generate subject, after the overrides, names only
    factors and case fields."""
    g = spec.generate
    if g.kind not in ("prompt", "generate"):
        return
    for setup in setups:
        model = overrides.model_of(spec, setup)
        for case in cases:
            asked = overrides.case_for(case, model)
            where = subjects.Where(folder.parent.parent, folder, folder / "outputs" / case["id"])
            try:
                if g.kind == "generate":
                    request(spec, asked, setup, spec.seed, where)
                else:
                    values = subjects.prompt_values(spec, asked, setup, spec.seed, where)
                    subjects.render(overrides.generate_for(g, model)[0], values, where, model)
            except (KeyError, AttributeError, IndexError, ValueError, ConfigError) as e:
                raise ConfigError(
                    f"[generate] the prompt or inputs for model {model!r} and case {case['id']!r} name no "
                    f"factor or case field ({type(e).__name__}: {e}); fix the placeholder or the override"
                ) from e
