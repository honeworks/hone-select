"""Typed exceptions raised at the public API."""


class HoneSelectError(Exception):
    """Base class for every error raised by hone-select."""


class ConfigError(HoneSelectError):
    """The configuration or registry is invalid; the message says what to change."""


class BudgetExceeded(HoneSelectError):  # noqa: N818 - name fixed by the design
    """A cost, time or money budget ran out. The engine catches it and selects among what exists."""


class PortError(HoneSelectError):
    """An injected adapter (judge, embedder, sink, cache) returned something unusable."""
