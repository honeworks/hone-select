"""Shared pytest fixtures: per-test HONE_HOME, GPU lock and real-model availability checks."""

from __future__ import annotations

import fcntl
import json
import os
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

OLLAMA_URL = os.environ.get("HONE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")


@pytest.fixture(autouse=True)
def hone_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test's default stores (.hone/...) inside its own temp folder."""
    home = tmp_path / "hone-home"
    monkeypatch.setenv("HONE_HOME", str(home))
    monkeypatch.delenv("HONE_CAPTURE_CONTENT", raising=False)
    return home


@pytest.fixture(autouse=True)
def quiet_fake_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Run conditions (design change 0010 §14): no default test reads the real /proc, calls the real
    nvidia-smi or touches the real GPU lock. Tests that script the machine pass their own Sources."""
    if request.node.get_closest_marker("gpu"):
        return
    from hone_select.experiments import conditions

    proc = tmp_path / "fake-proc"
    proc.mkdir(exist_ok=True)
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n")
    quiet = conditions.Sources(proc=proc, nvidia_smi=(str(tmp_path / "no-nvidia-smi"),), sleep=lambda s: None)
    monkeypatch.setattr(conditions, "DEFAULT", quiet)
    monkeypatch.setenv("HONE_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.delenv("HONE_GPU_LOCK_HELD", raising=False)


@pytest.fixture(scope="session")
def gpu_lock() -> Iterator[None]:
    """Hold the machine-wide GPU lock for the session (no-op if scripts/gpu-lock.sh already holds it)."""
    if os.environ.get("HONE_GPU_LOCK_HELD") == "1":
        yield
        return
    path = Path(os.environ.get("HONE_GPU_LOCK", "/tmp/honeworks-gpu.lock"))  # noqa: S108 - shared machine-wide lock by design
    path.touch(exist_ok=True)
    with path.open("r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:  # noqa: S310
            return [m["name"] for m in json.load(r).get("models", [])]
    except OSError:
        return []


@pytest.fixture(scope="session")
def ollama_model(gpu_lock: None):
    """Factory: ollama_model("HONE_TEST_TEXT_MODEL", "gemma4-12b:latest") -> name, or skip with a reason."""
    available = ollama_models()

    loaded: list[str] = []

    def _use(env: str, default: str) -> str:
        name = os.environ.get(env, default)
        if not available:
            pytest.skip(f"Ollama not reachable at {OLLAMA_URL}")
        if name not in available:
            pytest.skip(f"Ollama model {name!r} ({env}) not installed")
        loaded.append(name)
        return name

    yield _use
    for name in set(loaded):  # free the shared GPU for the next user
        try:
            req = urllib.request.Request(  # noqa: S310
                f"{OLLAMA_URL}/api/generate",
                data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=30).read()  # noqa: S310
        except OSError:
            pass
