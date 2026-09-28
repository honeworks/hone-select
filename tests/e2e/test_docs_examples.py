"""The README quickstart and every Python block in docs/ run as written (examples: test_ac21_examples)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
BLOCK = re.compile(r"^```python\n(.*?)^```", re.S | re.M)


def test_every_doc_has_python_blocks() -> None:
    assert all(BLOCK.search(doc.read_text()) for doc in DOCS)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_doc_blocks_run(doc: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HONE_HOME")  # the docs use the default .hone/ store, here inside tmp_path
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, object] = {"__name__": "__docs__"}
    for block in BLOCK.findall(doc.read_text()):
        exec(compile(block, str(doc), "exec"), namespace)  # noqa: S102 - running our own docs


def test_readme_quickstart_prints_the_shortest_line(tmp_path: Path) -> None:
    env = {**os.environ, "HONE_HOME": str(tmp_path)}
    script = ROOT / "examples" / "quickstart.py"
    out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.startswith("hello #0 0.92")
