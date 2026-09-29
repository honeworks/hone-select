"""Contract checkers for the ports hone-select owns (design/current.md §7.9). Providers run these against
their implementations; each raises ``AssertionError`` on a mismatch."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

from hone_select.ports import get

__all__ = [
    "check_decision_client",
    "check_embedder",
    "check_machine_probe",
    "check_record_sink",
    "check_text_client",
    "example_span",
    "get",
]


def check_text_client(client: Any) -> None:
    r = client.complete([{"role": "user", "content": "Say OK."}])
    assert isinstance(get(r, "text"), str)
    assert get(r, "error") is None or isinstance(get(r, "error"), str)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    r = client.complete(
        [{"role": "user", "content": 'Return {"ok": true}.'}],
        schema=schema,
        trace={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
    )
    assert get(r, "parsed") is not None or get(r, "error")
    client.complete([{"role": "user", "content": "x"}], unknown_param_is_ignored=1)


def check_decision_client(client: Any) -> None:
    qs = {
        "q1": {"type": "yes_no", "instructions": "Is the sky described as blue?"},
        "q2": {"type": "choice", "instructions": "Colour?", "options": ["blue", "red"]},
        "q3": {"type": "score", "instructions": "How vivid?", "scale": [1, 5]},
    }
    a = client.decide("The sky is blue.", qs)
    assert set(a) <= set(qs)
    for name, ans in a.items():
        v, err = get(ans, "value"), get(ans, "error")
        assert (v is None) == bool(err) or err is None
        if v is not None:
            assert 0.0 <= v <= 1.0
        assert get(ans, "type") == qs[name]["type"]
        assert isinstance(get(ans, "calibrated", False), bool)
    if "q2" in a and get(a["q2"], "value") is not None:
        assert get(a["q2"], "choice") in ("blue", "red")


def check_embedder(e: Any) -> None:
    v = e.embed(["a", "b"])
    assert len(v) == 2
    assert all(len(x) == e.dimensions for x in v)
    assert all(abs(sum(t * t for t in x) - 1.0) < 1e-3 for x in v)
    assert e.embed([]) == []


def check_record_sink(sink: Any, read_back: Callable[[], Iterable[Mapping[str, Any]]]) -> None:
    span = example_span()
    sink.emit(span)
    sink.flush()
    got = [s for s in read_back() if s["span_id"] == span["span_id"]]
    assert got
    assert got[0]["trace_id"] == span["trace_id"]


def example_span() -> dict[str, Any]:
    """An example span in the shape of design/current.md §8.1."""
    return {
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": None,
        "name": "hone.test.example",
        "kind": "internal",
        "start_time": "2026-09-27T14:03:11.120Z",
        "end_time": "2026-09-27T14:03:11.220Z",
        "status": {"code": "ok", "message": ""},
        "attributes": {"hone.schema_version": "1"},
        "events": [],
        "resource": {},
        "links": [],
    }


def _list_of_mappings(value: Any, key: str) -> None:
    assert isinstance(value, list), f"{key} must be a list"
    assert all(isinstance(item, Mapping) for item in value), f"every item of {key} must be a mapping"  # pyright: ignore[reportUnknownVariableType]


def check_machine_probe(probe: Any) -> None:
    """``snapshot()`` returns a mapping of the documented shape; ``prepare([])`` returns a mapping
    (design change 0010 §6). Every key is optional; ``load`` is not called (it would load a model)."""
    answer = probe.snapshot()
    assert isinstance(answer, Mapping)
    snap = cast(Mapping[str, Any], answer)
    if snap.get("gpus") is not None:
        _list_of_mappings(snap["gpus"], "gpus")
    for key in ("servers", "loaded_models", "leases"):
        if key in snap:
            _list_of_mappings(snap[key], key)
    servers: list[Mapping[str, Any]] = snap.get("servers", [])
    models: list[Mapping[str, Any]] = snap.get("loaded_models", [])
    for server in servers:
        assert server.get("running") in (True, False, None), "servers[].running is True, False or None"
    for model in models:
        assert isinstance(model.get("name"), str), "loaded_models[].name is a string"
    if snap.get("gpu_lock") is not None:
        assert isinstance(snap["gpu_lock"], Mapping)
    reply = probe.prepare([])
    assert isinstance(reply, Mapping)
    prepared = cast(Mapping[str, Any], reply)
    for key in ("unloaded", "errors", "missing", "loaded_models"):
        if key in prepared:
            assert isinstance(prepared[key], list), f"prepare()[{key!r}] must be a list"
