"""Variations and seeds: give each candidate its own seed and sampling params, and get the same run twice.

What: the variation schedule in [generate] vary - seeds that increment or come from a list, and param
      grids (temperature, style, ...) that cycle - and a seeded run that is repeatable.
How:  1. [generate] vary = { seed = "increment" | [s1, s2, ...], <param> = [v1, v2, ...] };
      2. your generator reads v["seed"] and v["params"] and passes them to the model (or random.Random);
      3. engine.run(task, seed=base): with "increment", candidate i gets seed base + i; each candidate's
         meta records its index, seed and params.
Why:  diversity is what gives selection something to choose from, and seeds make a good result
      reproducible and debuggable. Pitfall: use random.Random(v["seed"]), never the global random
      module or built-in hash(), or two identical runs will differ.
"""

import random

from hone_select import Engine, generator, scorer

WORDS = ["rain", "light", "golden", "summer", "river", "night", "home", "fire"]


@generator()
def write_line(task, v):
    rng = random.Random(v["seed"])  # a private generator, seeded per candidate
    count = 3 if v["params"]["style"] == "short" else 6
    words = rng.sample(WORDS, count)
    return f"{task}: " + " ".join(words) + f" (t={v['params']['temperature']})"


@scorer()
def mentions_golden(c):
    return 1.0 if "golden" in c.data else 0.2


CONFIG = """
[generate]
n = 6
vary = { seed = "increment", temperature = [0.7, 1.0], style = ["short", "short", "long"] }
[score]
cascade = [{ scorers = ["mentions_golden"] }]
"""
engine = Engine(CONFIG, registry=[write_line, mentions_golden])

first = engine.run("chorus", seed=42)
for item in first.ranked:
    meta = item.candidate.meta
    print(meta["index"], meta["seed"], meta["params"], "->", item.candidate.data)

metas = sorted((s.candidate.meta for s in first.ranked), key=lambda m: m["index"])
assert [m["seed"] for m in metas] == [42, 43, 44, 45, 46, 47]
assert [m["params"]["temperature"] for m in metas] == [0.7, 1.0] * 3  # each param cycles on its own
assert [m["params"]["style"] for m in metas] == ["short", "short", "long"] * 2

# Same config + same seed -> the same candidates, ranking and totals (run ids and timings differ).
second = engine.run("chorus", seed=42)
assert [(s.candidate.id, s.total) for s in second.ranked] == [(s.candidate.id, s.total) for s in first.ranked]

# A different seed gives different candidates.
other = engine.run("chorus", seed=7)
assert {s.candidate.id for s in other.ranked} != {s.candidate.id for s in first.ranked}

# A fixed list of seeds instead of "increment" (it cycles when n is larger).
LISTED_SEEDS = """
[generate]
n = 6
vary = { seed = [1, 2, 3], temperature = [0.7, 1.0], style = ["short", "short", "long"] }
[score]
cascade = [{ scorers = ["mentions_golden"] }]
"""
listed = Engine(LISTED_SEEDS, registry=[write_line, mentions_golden]).run("chorus")
seeds = sorted((s.candidate.meta["index"], s.candidate.meta["seed"]) for s in listed.ranked)
print("listed seeds:", seeds)
assert [seed for _, seed in seeds] == [1, 2, 3, 1, 2, 3]
