"""Aggregation and missing scores: how several scores become one total, and what None does to it.

What: normalizers that turn raw measurements into 0..1; weights; the four aggregators; the three
      `missing` policies for a scorer that could not score (returned None or raised).
How:  1. inside a scorer, map a raw number with linear(lo, hi), inverse(lo, hi), sigmoid(mid, k) or
         from_1_5 - each returns a plain function raw -> 0..1;
      2. [score] weights = {name = w} (unlisted scorers weigh 1); aggregate = "weighted_mean" | "min" |
         "geometric" | "weighted_mean_with_floor" (with floor = ...);
      3. [score] missing = "renormalize" (ignore None; default) | "zero" (None counts as 0) | "reject".
Why:  None means "could not score", never "scored 0": a crashed judge must not sink a good candidate.
      Choose "renormalize" when a missing score is noise, "reject" when every score is required, and
      "zero" only when a missing score really is evidence of failure. Use "min" or a floor when one weak
      aspect should cap the whole ("a great melody does not save broken lyrics").
"""

from hone_select import Candidate, Engine, Score, from_1_5, inverse, linear, scorer, sigmoid

# Raw measurements -> 0..1, higher is better.
assert linear(0, 10)(5) == 0.5  # 0 -> 0, 10 -> 1, clamped
assert inverse(0, 10)(2) == 0.8  # lower raw is better: 0 -> 1, 10 -> 0
assert sigmoid(mid=120, k=0.1)(120) == 0.5  # a smooth step centred on 120
assert from_1_5(4) == 0.75  # a 1..5 rating

SONGS = [
    {"title": "steady", "bpm": 120, "errors": 1, "rating": 4},
    {"title": "fast", "bpm": 170, "errors": 0, "rating": 5},
    {"title": "no rating", "bpm": 118, "errors": 2, "rating": None},
    {"title": "judge crashed", "bpm": 121, "errors": 0, "rating": "crash"},
]


@scorer()
def tempo(c):
    return sigmoid(mid=150, k=-0.1)(c.data["bpm"])  # negative k: slower is better, around 150 bpm


@scorer()
def accuracy(c):
    return Score(inverse(0, 5)(c.data["errors"]), reason=f"{c.data['errors']} errors")


@scorer()
def rating(c):
    if c.data["rating"] == "crash":
        raise TimeoutError("judge did not answer")  # becomes Score(None, error="TimeoutError: ...")
    if c.data["rating"] is None:
        return None  # could not score: Score(None)
    return from_1_5(c.data["rating"])


candidates = [Candidate.of(song) for song in SONGS]
CONFIG = """
[score]
cascade = [{ scorers = ["tempo", "accuracy", "rating"] }]
"""
SETTINGS = {  # extra [score] keys for each variant
    "renormalize": "",  # the default
    "zero": 'missing = "zero"',
    "reject": 'missing = "reject"',
    "weights": "weights = { rating = 3 }",
    "min": 'aggregate = "min"',
    "geometric": 'aggregate = "geometric"',
    "floor": 'aggregate = "weighted_mean_with_floor"\nfloor = 0.5',
}

results = {}  # variant -> title -> Scored
for variant, extra in SETTINGS.items():
    engine = Engine(CONFIG + extra, registry=[tempo, accuracy, rating])
    results[variant] = {s.candidate.data["title"]: s for s in engine.score(candidates)}
    print(f"{variant:12}", {title: round(s.total, 3) for title, s in results[variant].items()})

# The crashed judge's candidate keeps a total from its other two scores ...
assert results["renormalize"]["judge crashed"].total > 0.8
# ... falls when None counts as 0 ...
assert results["zero"]["judge crashed"].total < results["renormalize"]["judge crashed"].total
# ... and is rejected (ranked last) when every score is required.
assert results["reject"]["judge crashed"].rejected
assert results["reject"]["no rating"].rejected
assert not results["reject"]["steady"].rejected
# With a floor, any score under 0.5 caps the total at 0.5 ("fast": its tempo score is low).
assert results["floor"]["fast"].total == 0.5

# The failure is kept with the score, for you to inspect.
crashed = results["renormalize"]["judge crashed"].scores["rating"]
print("rating of 'judge crashed':", crashed)
assert crashed.value is None
assert crashed.error == "TimeoutError: judge did not answer"
