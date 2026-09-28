"""A command scorer for examples/command_scorer.py; any language works the same way.

stdin:  {"candidate": {"id": ..., "data": ..., "files": {"text": "/path/to/file"}, "meta": {...}}}
stdout: {"value": 0..1, "reason": "...", "details": {...}}
A non-zero exit code means "could not score"; stderr becomes the error message.
"""

import json
import sys

candidate = json.load(sys.stdin)["candidate"]
with open(candidate["files"]["text"], encoding="utf-8") as f:
    words = f.read().split()
if not words:
    sys.exit("empty file: nothing to score")  # exit code 1, message on stderr
target = candidate["data"]["target_words"]
value = max(0.0, 1 - abs(len(words) - target) / target)
result = {"value": value, "reason": f"{len(words)} words, target {target}", "details": {"words": len(words)}}
print(json.dumps(result))
