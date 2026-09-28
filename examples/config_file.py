"""Config file: keep the selection settings in a TOML file and the code in a registry.

What: loading `config_file.toml`, which names code components (a gate, a scorer) and defines a prompt
      scorer without code; how a mistake in the config becomes a ConfigError that says what to change.
How:  1. write the functions and decorate them (@generator, @gate, @scorer);
      2. Engine(Path("config_file.toml"), registry=[...], judges={"reviewer": client}) - every name the
         config uses must be in the registry or a [scorers.<name>] section; a judge name must be in
         judges= or a [judges.<name>] section (see hone_models_judge.py);
      3. engine.config is the validated SelectionConfig if you want to read settings back;
      4. catch ConfigError at start-up: Engine(...) raises it before anything runs, for a bad value
         (a schema error) and for a name that nothing defines.
Why:  a config file lets you tune n, weights, cascades and policies without touching code, and it is
      recorded (its hash is on every run span). Pitfall: a str is read as a path only when it is one line
      ending in ".toml"; anything else is parsed as TOML text.
"""

from pathlib import Path

from hone_select import ConfigError, Engine, gate, generator, scorer
from hone_select.testing import FakeDecisionClient

HEADLINES = [
    "Council approves new bike lanes",
    "Bike lanes: a story of councils, votes and what it all might mean",
    "Council approves new bike lanes",  # a duplicate: removed by [dedup]
    "Lanes",
]


@generator()
def write_headline(task, v):
    return {"headline": HEADLINES[v["index"]], "temperature": v["params"]["temperature"]}


@gate()
def has_verb(c):
    return " " in c.data["headline"]  # a stand-in check: one-word headlines have no verb


@scorer(cost=1)
def brevity(c):
    return max(0.0, 1 - len(c.data["headline"].split()) / 15)


# The judge for [scorers.clarity]. In real use: an LLM client (see bring_your_own_client.py).
reviewer = FakeDecisionClient(answers={"clarity": 0.75}, model_id="reviewer-model")

config_path = Path(__file__).with_name("config_file.toml")
registry = [write_headline, has_verb, brevity]
# cache=None: this example counts calls (with the default cache, a second run reuses the scores)
engine = Engine(config_path, registry=registry, judges={"reviewer": reviewer}, cache=None)
print("weights from the file:", engine.config.score.weights)

result = engine.run({"topic": "bike lanes"})
print("winner:", result.winner.candidate.data["headline"], round(result.winner.total, 3))

assert result.winner.candidate.data["headline"] == "Council approves new bike lanes"
assert any(entry["event"] == "dedup" for entry in result.decision)  # the repeated headline
assert len(reviewer.calls) == 2  # keep_top = 2: the judge saw only the two finalists
assert reviewer.calls[0]["state"] == "Council approves new bike lanes"  # field = "headline"

# Config mistakes are caught when the Engine is built, with a message that says what to change.
BAD_VALUE = """
[select]
policy = "best"
"""
UNKNOWN_NAME = """
[score]
cascade = [{ scorers = ["brevty"] }]
"""
for broken, expected in [(BAD_VALUE, "select.policy"), (UNKNOWN_NAME, "unknown scorer 'brevty'")]:
    try:
        Engine(broken, registry=registry)
    except ConfigError as e:
        print("ConfigError:", e)
        assert expected in str(e)
    else:
        raise AssertionError("a broken config must raise ConfigError")
