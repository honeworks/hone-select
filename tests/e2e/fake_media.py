"""A fake generation client and guide source for design change 0011 tests, reached as "module:factory"
(`tests.e2e.fake_media:music`, `tests.e2e.fake_media:guides`), so no hone-models version is needed.

`CALLS` keeps every `generate` call and `EVENTS` every session enter / exit and call, in order. A case's
`behaviour` input scripts the result: "refused", "oom_once" (out of memory on the first call for that
case and model only), otherwise a file is written. `reset()` clears everything between tests.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hone_select import Candidate, scorer
from hone_select.testing import FakeModelGuides

CALLS: list[dict[str, Any]] = []
EVENTS: list[tuple[str, str]] = []
GUIDES: dict[str, dict[str, Any]] = {}
TEXTS: list[dict[str, Any]] = []
LICENSES = {"yue": ("CC-BY-NC-4.0", False)}


@dataclass
class File:
    path: Path
    sha256: str
    bytes: int
    mime: str = "audio/wav"
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


@dataclass
class Result:
    files: list[File] = field(default_factory=list[File])
    error: str | None = None
    error_kind: str | None = None
    elapsed_s: float = 2.5
    cost_usd: float | None = 0.04
    cost_estimated: bool = True
    license: str | None = "Apache-2.0"
    commercial_use: bool | None = True
    job_id: str = "job-1"


def reset() -> None:
    CALLS.clear()
    EVENTS.clear()
    GUIDES.clear()
    TEXTS.clear()
    SEEN.clear()


class FakeMedia:
    def __init__(self, model: str, **options: Any) -> None:
        self.model, self.options = model, options

    @contextmanager
    def session(self) -> Iterator[None]:
        EVENTS.append(("enter", self.model))
        try:
            yield
        finally:
            EVENTS.append(("exit", self.model))

    def generate(
        self,
        prompt: str,
        *,
        out: Path,
        seed: int | None = None,
        timeout_s: Any = None,
        trace: Any = None,
        **inputs: Any,
    ) -> Result:
        CALLS.append(
            {"model": self.model, "prompt": prompt, "out": out, "seed": seed, "trace": trace, **inputs}
        )
        EVENTS.append(("generate", self.model))
        behaviour = inputs.get("behaviour", "ok")
        license_, commercial = LICENSES.get(self.model, ("Apache-2.0", True))
        if behaviour == "refused":
            return Result(
                error="the prompt was refused by moderation", error_kind="refused", license=license_
            )
        tries = sum(c["model"] == self.model and c.get("behaviour") == behaviour for c in CALLS)
        if behaviour == "oom_once" and tries == 1:
            return Result(error="CUDA out of memory", error_kind="out_of_memory", license=license_)
        out.parent.mkdir(parents=True, exist_ok=True)
        body = f"{self.model} {prompt} {seed}".encode()
        out.write_bytes(body)
        info = File(
            out, hashlib.sha256(body).hexdigest(), len(body), duration_s=float(inputs.get("duration_s") or 0)
        )
        return Result(files=[info], license=license_, commercial_use=commercial)


def music(model: str, **options: Any) -> FakeMedia:
    return FakeMedia(model, **options)


def guides() -> FakeModelGuides:
    """A guide source over `GUIDES` as it is now (tests change it between plan and start)."""
    return FakeModelGuides(GUIDES)


class Writer:
    """A text client that records its messages (the prompt subject's `{model_guide}`)."""

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, messages: Any, **params: Any) -> Any:
        TEXTS.append({"model": self.model, "messages": messages, **params})
        return type("R", (), {"text": f"a prompt by {self.model}", "error": None, "usage": {}})()


def writer(model: str, **options: Any) -> Writer:
    return Writer(model)


SCORES = {"wan": 0.9, "sora": 0.6, "mystery": 0.3}


@scorer("by_model")
def by_model(c: Candidate) -> float:
    """A known best model: the score depends only on the model."""
    return SCORES.get(str(c.meta["params"].get("model")), 0.5)


@scorer("commercial")
def commercial(c: Candidate) -> float:
    """1 when the candidate's meta says the model may be used commercially (license in the meta)."""
    return 1.0 if c.meta.get("commercial_use") else 0.0


SEEN: list[dict[str, Any]] = []


@scorer("sees_case")
def sees_case(c: Candidate) -> float:
    """Records what a code scorer sees of the case (never an override)."""
    SEEN.append({"sample": c.id, "case": dict(c.meta.get("case", {})), "data": c.data})
    return 0.5
