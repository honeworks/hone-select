"""CommandScorer: score with an executable in any language (JSON on stdin, JSON on stdout)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar

from hone_select.types import Candidate, Score, to_score


@dataclass(frozen=True)
class CommandScorer:
    """Run ``command`` once per candidate.

    stdin: ``{"candidate": {"id", "data", "files", "meta"}}``;
    stdout: ``{"value": 0.9, "reason": "...", "confidence": 0.8, "details": {}}``.
    A non-zero exit, bad JSON or a timeout gives ``Score(None, error=<stderr tail>)``.
    """

    name: str
    command: Sequence[str]
    _: KW_ONLY
    cost: float = 20.0
    timeout_s: float = 60.0
    version: str = "1"
    kind: ClassVar[str] = "scorer"

    def __call__(self, candidate: Candidate) -> Score:
        payload = {
            "candidate": {
                "id": candidate.id,
                "data": candidate.data,
                "files": dict(candidate.files),
                "meta": dict(candidate.meta),
            }
        }
        try:
            done = subprocess.run(  # noqa: S603 - the user configures the command
                list(self.command),
                input=json.dumps(payload, default=str),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            return Score(None, error=f"timed out after {self.timeout_s:g}s: {stderr[-500:].strip()}")
        except OSError as e:
            return Score(None, error=f"could not run {self.command[0]!r}: {e}")
        if done.returncode != 0:
            return Score(None, error=f"exit code {done.returncode}: {done.stderr[-500:].strip()}")
        try:
            output = json.loads(done.stdout)
            if not isinstance(output, dict) or "value" not in output:
                raise ValueError('expected an object like {"value": 0.9}')
            score = to_score(output)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            return Score(None, error=f"bad JSON on stdout ({e}): {done.stdout[-200:]!r} {done.stderr[-300:]}")
        if score.value is None and not score.error:
            return Score(None, score.confidence, score.reason, score.details, "command returned no value")
        return score
