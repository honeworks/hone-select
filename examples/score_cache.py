"""Score cache: never pay twice for the same score; bump a scorer's version to recompute it.

What: the SQLite score cache - a second identical run calls no scorers; changing one scorer's `version`
      recomputes only that scorer; a failed score is not cached; a ScoreCache of your own.
How:  1. by default Engine uses $HONE_HOME/select/cache.db (next to the span store); pass
         cache=SqliteScoreCache(path) to choose the file, or cache=None to turn caching off;
      2. the key is (candidate id, scorer name, scorer version, judge model id): same data + same scorer
         version + same judge = the cached Score is reused and a "cache_hit" entry is recorded;
      3. change @scorer(version="2") (or PromptScorer(..., version="2")) whenever you change its logic;
      4. any object with get(key) -> Score | None and put(key, score) is a ScoreCache (hone_select.ports).
Why:  judges and command scorers are slow and paid, and re-running a selection with a new scorer or a
      new policy should not re-score everything. Pitfalls: forgetting to bump `version` after changing a
      scorer silently reuses the old scores; [record] sink = "none" turns off the span store, not the
      cache - only Engine(cache=None) does.
"""

import tempfile
from pathlib import Path

from hone_select import Candidate, Engine, scorer
from hone_select.cache import SqliteScoreCache

calls = {"brevity": 0, "rhyme": 0}


@scorer(version="1")
def brevity(c):
    calls["brevity"] += 1
    return 1 - len(c.data) / 50


@scorer(version="1")
def rhyme(c):
    calls["rhyme"] += 1
    if "moon" in c.data:
        raise RuntimeError("rhyme service timed out")  # failures are retried next time, never cached
    return 0.5


@scorer(name="rhyme", version="2")  # the same scorer after a logic change
def rhyme_v2(c):
    calls["rhyme"] += 1
    return 0.8


CONFIG = """
[score]
cascade = [{ scorers = ["brevity", "rhyme"] }]
"""
candidates = [Candidate.of(t) for t in ["june moon", "rain again", "summer"]]
folder = tempfile.TemporaryDirectory()
cache = SqliteScoreCache(Path(folder.name) / "cache.db")

# 1. The first run calls every scorer once per candidate.
Engine(CONFIG, registry=[brevity, rhyme], cache=cache).score(candidates)
print("first run calls:", calls)
assert calls == {"brevity": 3, "rhyme": 3}

# 2. The same run again: everything that succeeded comes from the cache; only the failure is retried.
Engine(CONFIG, registry=[brevity, rhyme], cache=cache).score(candidates)
print("second run calls:", calls)
assert calls == {"brevity": 3, "rhyme": 4}

# 3. A new rhyme version: rhyme runs again for all three; brevity still comes from the cache.
Engine(CONFIG, registry=[brevity, rhyme_v2], cache=cache).score(candidates)
print("after rhyme v2:", calls)
assert calls == {"brevity": 3, "rhyme": 7}
cache.close()
folder.cleanup()


# 4. Your own cache: anything with get and put. Keys are (candidate id, scorer, version, judge model).
class DictCache:
    def __init__(self):
        self.scores = {}

    def get(self, key):
        return self.scores.get(key)

    def put(self, key, score):
        self.scores[key] = score


mine = DictCache()
Engine(CONFIG, registry=[brevity, rhyme_v2], cache=mine).score(candidates)
print("keys:", sorted(mine.scores)[:2])
assert len(mine.scores) == 6 and all(len(key) == 4 for key in mine.scores)
