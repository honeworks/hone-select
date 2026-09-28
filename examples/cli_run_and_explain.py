"""CLI: run a selection from a config file, then explain it later from the span store.

What: the `hone-select` command (extra hone-select[cli]) on the files in examples/cli/: `run` prints the
      winner (or the full result with --json), `explain <run_id>` rebuilds the decision from the store,
      `show <run_id>` lists every span. Skipped, with a message, when the cli extra is not installed.
How:  1. put decorated functions in a module (examples/cli/shortest_line.py) and the settings in a TOML
         file (examples/cli/selection.toml); the task is a JSON file (examples/cli/task.json);
      2. from that folder: hone-select run selection.toml --task task.json --registry shortest_line
         [--seed 1] [--json];
      3. hone-select explain <run_id> [--db path/to/spans.db]; hone-select show <run_id> [--json].
      This script runs the same commands as `python -m hone_select.cli ...` so it uses this interpreter.
Why:  run selections from shell scripts, CI jobs or other languages, and inspect past runs without
      writing Python. Pitfall: --registry imports a module by name from the current folder, so run the
      command from the folder that holds it; set HONE_HOME (or --db) to find the right store.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if importlib.util.find_spec("typer") is None:
    print("skipped: the CLI needs the cli extra - pip install 'hone-select[cli]'")
    sys.exit(0)

FOLDER = Path(__file__).with_name("cli")
# Records go to $HONE_HOME/select/spans.db (default: .hone/ in the current folder); a temp folder here.
stores = tempfile.TemporaryDirectory()
ENV = {**os.environ, "HONE_HOME": stores.name, "PYTHONDONTWRITEBYTECODE": "1"}


def hone_select(*args):
    """Run `hone-select <args>` in examples/cli/ and return its standard output."""
    command = [sys.executable, "-m", "hone_select.cli", *args]
    done = subprocess.run(command, cwd=FOLDER, env=ENV, capture_output=True, text=True, check=True)
    return done.stdout


out = hone_select("run", "selection.toml", "--task", "task.json", "--registry", "shortest_line", "--json")
result = json.loads(out)
print("winner:", result["winner"]["candidate"]["data"], "run:", result["run_id"])
assert result["winner"]["candidate"]["data"] == "hello #0"
assert {"run_id", "trace_id", "winner", "ranked", "decision", "budget"} <= set(result)

explained = hone_select("explain", result["run_id"])
print(explained)
assert explained.startswith(f"run {result['run_id']}") and "selected: winner=" in explained

spans = hone_select("show", result["run_id"])
assert "hone.select.decision" in spans
stores.cleanup()
