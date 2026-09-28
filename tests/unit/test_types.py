import os
import subprocess
import sys
from pathlib import Path

import pytest

from hone_select import Candidate


def test_candidate_id_is_stable_and_content_based() -> None:
    a = Candidate.of({"x": 1, "y": [1, 2]})
    b = Candidate.of({"y": [1, 2], "x": 1}, meta={"seed": 3})
    assert a.id == b.id
    assert len(a.id) == 16
    assert Candidate.of({"x": 2}).id != a.id
    assert b.meta == {"seed": 3}


def test_candidate_id_includes_file_contents(tmp_path: Path) -> None:
    f = tmp_path / "out.wav"
    f.write_bytes(b"one")
    first = Candidate.of("song", files={"audio": str(f)})
    f.write_bytes(b"two")
    second = Candidate.of("song", files={"audio": str(f)})
    assert first.id != second.id
    assert first.id != Candidate.of("song").id


def test_candidate_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Candidate.of("x", files={"a": str(tmp_path / "missing")})


def test_candidate_id_is_stable_across_processes_for_sets_and_dataclasses() -> None:
    code = (
        "from dataclasses import dataclass\n"
        "from hone_select import Candidate\n"
        "@dataclass\nclass P:\n    x: int\n"
        "print(Candidate.of({'tags': {'alpha', 'beta', 'gamma', 'delta'}, 'p': P(1)}).id)"
    )
    ids = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(ids) == 1
