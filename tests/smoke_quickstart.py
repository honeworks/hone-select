"""Run by scripts/check.sh in a fresh venv with only the wheel installed: the README quickstart works."""

import os
import runpy
import tempfile
from pathlib import Path

os.environ["HONE_HOME"] = tempfile.mkdtemp()  # keep the smoke run's span store out of the repo
quickstart = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "quickstart.py"))
result = quickstart["engine"].run("hello")
assert result.winner is not None
assert result.winner.candidate.data == "hello #0", result.winner
print("quickstart ok:", result.winner.candidate.data, result.winner.total)
