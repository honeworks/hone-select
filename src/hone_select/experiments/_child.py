"""Runs one python subject in its own process (design change 0009):
`python -m hone_select.experiments._child`.

Reads a JSON payload on stdin ({function, case, setup, seed, workdir, paths, result}), calls
`function(case, setup, ctx)` and writes {data, measurements, cost_usd, error, transient} to `result`.
A separate process gives the subject a timeout that can kill it, its own peak memory, and isolation from
crashes; the result file keeps the subject's own prints out of the way.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from hone_select.experiments.subjects import Ctx, TransientError, import_object
from hone_select.types import Candidate


def main() -> int:
    payload = json.loads(sys.stdin.read())
    sys.path[:0] = payload["paths"]  # the project root, then the experiment's scripts/
    os.chdir(payload["paths"][0])
    ctx = Ctx(Path(payload["workdir"]), payload["seed"], payload["case"]["files"])
    out: dict[str, Any]
    try:
        value = import_object(payload["function"])(payload["case"], payload["setup"], ctx)
        meta = dict(value.meta) if isinstance(value, Candidate) else {}
        data = value.data if isinstance(value, Candidate) else value
        out = {
            "data": data,
            "measurements": dict(meta.get("measurements", {})),
            "cost_usd": float(meta.get("cost_usd", 0.0)),
            "error": None,
        }
    except TransientError as e:
        out = {"error": f"TransientError: {e}", "transient": True}
    except Exception as e:  # the subject's failure is a result
        out = {"error": f"{type(e).__name__}: {e}"}
    try:
        text = json.dumps(out)
    except (TypeError, ValueError) as e:
        reason = f"the subject returned data that is not JSON ({e}); return str, numbers, lists or dicts"
        text = json.dumps({"error": reason})
    Path(payload["result"]).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
