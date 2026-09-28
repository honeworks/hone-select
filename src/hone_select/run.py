"""The mutable state of one selection run, passed to the scoring and selection steps."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from hone_select.budget import Budget
from hone_select.config import SelectionConfig
from hone_select.ports import RecordSink, ScoreCache
from hone_select.registry import Component
from hone_select.types import as_text

log = logging.getLogger("hone_select")
FIXED_REASONS = ("pairwise", "escalation_skipped")  # their `reason` is a fixed token, not content


@dataclass(slots=True)
class Run:
    config: SelectionConfig
    components: Mapping[str, Component]
    sink: RecordSink
    cache: ScoreCache | None
    budget: Budget
    capture_content: bool = True
    decision: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    # one entry per pairwise comparison: "first" / "second" when both orders chose that position, else None
    pairwise_bias: list[str | None] = field(default_factory=list[str | None])

    def note(self, event: str, **fields: Any) -> None:
        """Append one entry to the ordered decision trace."""
        self.decision.append({"event": event, **fields})

    def warn(self, message: str) -> None:
        """A warning is logged and also kept in the decision trace."""
        log.warning(message)
        self.note("warning", message=message)

    def content(self, value: Any) -> Any:
        """``value`` itself, or only its hash and length when content capture is off."""
        if self.capture_content:
            return value
        text = as_text(value)
        return {"sha256": hashlib.sha256(text.encode()).hexdigest(), "len": len(text)}

    def content_trace(self) -> list[dict[str, Any]]:
        """The decision trace for records: free-text ``reason`` / ``error`` fields go through ``content``."""
        return [
            e if e["event"] in FIXED_REASONS else {k: self._private(k, v) for k, v in e.items()}
            for e in self.decision
        ]

    def _private(self, key: str, value: Any) -> Any:
        if key in ("reason", "error"):
            return self.content(value)
        if key == "rejected":  # the "gated" entry: candidate id -> reason; keep the ids readable
            return {cid: self.content(reason) for cid, reason in value.items()}
        return value

    def mark_error(self, record: dict[str, Any], error: Exception) -> str:
        """Set a span's error status and return ``"Type: message"`` for results and the decision trace.

        With content capture off the span status holds only the exception type (messages can quote data).
        """
        message = f"{type(error).__name__}: {error}"
        status = message if self.capture_content else type(error).__name__
        record["status"] = {"code": "error", "message": status}
        return message
