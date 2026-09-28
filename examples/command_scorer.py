"""Command scorer: score candidates with an executable in any language, via JSON on stdin and stdout.

What: CommandScorer running examples/command/count_words.py on candidates whose content is in files;
      a script that fails gives Score(None, error=...), not a crash and not a 0.
How:  1. write the program: read {"candidate": {id, data, files, meta}} from stdin, print
         {"value": 0..1, "reason": ..., "confidence": ..., "details": {...}} on stdout, exit 0;
         exit non-zero (with a message on stderr) when it cannot score;
      2. put file outputs (audio, images, code) in Candidate.of(data, files={"name": path}); the file
         contents are part of the candidate id, and the program gets the paths;
      3. CommandScorer(name, [program, args...], cost=20, timeout_s=60) - or [scorers.<name>]
         kind = "command" in the config - and register it like any scorer.
Why:  reuse checkers you already have (test runners, audio analysers, linters) without porting them to
      Python. Pitfalls: pass the interpreter explicitly (sys.executable here) rather than relying on PATH;
      a timeout, bad JSON or a value outside 0..1 are all "could not score".
"""

import sys
import tempfile
from pathlib import Path

from hone_select import Candidate, CommandScorer, Engine, generator

SCRIPT = Path(__file__).with_name("command") / "count_words.py"
TEXTS = ["one two three four five", "one two three four five six seven eight nine ten", ""]

word_count = CommandScorer("word_count", [sys.executable, str(SCRIPT)], cost=20, timeout_s=30)

outputs = tempfile.TemporaryDirectory()  # where the generator writes its files


@generator()
def write_file(task, v):
    path = Path(outputs.name) / f"draft-{v['index']}.txt"  # e.g. a rendered song, an image, a build
    path.write_text(TEXTS[v["index"]], encoding="utf-8")
    return Candidate.of({"target_words": task["target_words"]}, files={"text": str(path)})


CONFIG = """
[generate]
n = 3
[score]
cascade = [{ scorers = ["word_count"] }]
"""
engine = Engine(CONFIG, registry=[write_file, word_count])
result = engine.run({"target_words": 5})

for item in result.ranked:
    score = item.scores["word_count"]
    print(Path(item.candidate.files["text"]).name, "->", score.value, score.reason or score.error)

best, _, empty = result.ranked
assert best.candidate.files["text"].endswith("draft-0.txt") and best.total == 1.0
assert best.scores["word_count"].details == {"words": 5}
assert empty.scores["word_count"].value is None  # the script exited 1: could not score, not 0
assert "empty file" in empty.scores["word_count"].error
assert empty.total is None  # ranked last

outputs.cleanup()
