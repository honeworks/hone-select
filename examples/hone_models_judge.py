"""hone-models judges: name a model in the config and let the hone-models extra build the client.

What: a [judges.local] section with client = "hone_models:decision", used by a prompt scorer defined in
      [scorers.*]; hone-select finds the client through the "hone.decision_clients" entry point and never
      imports hone-models itself. Offline here: hone-models' own FakeOllama server stands in for Ollama.
      Skipped, with a message, when hone-models is not installed.
How:  1. pip install "hone-select[models]";
      2. [judges.local] client = "hone_models:decision", model = "<model id>" - every key except
         `client` is passed to hone_models.decision(...);
      3. refer to the judge by name: [scorers.x] judge = "local", or PromptScorer(..., judge="local");
      4. or skip the config and pass the client: Engine(..., judges={"local": hone_models.decision(...)}).
Why:  hone-models handles local and hosted models (Ollama, GPU leases, retries, records); hone-select
      only needs the DecisionClient port, so either package works without the other. Pitfall: an
      unknown `client` name is a ConfigError listing the installed ones - install the package that
      provides it.
"""

import importlib.util
import sys

if importlib.util.find_spec("hone_models") is None:
    print("skipped: this example needs the models extra - pip install 'hone-select[models]'")
    sys.exit(0)

from hone_models.testing import FakeOllama  # a local fake Ollama server: no model, no GPU

from hone_select import Engine, generator

CONFIG = """
[judges.local]
client = "hone_models:decision"
model = "gemma4-12b"

[generate]
n = 2

[score]
cascade = [{ scorers = ["clear"] }]

[scorers.clear]
kind = "prompt"
judge = "local"
criteria = "The sentence is clear."
"""


@generator()
def write(task, v):
    return f"{task}, take {v['index']}"


with FakeOllama() as ollama:  # sets OLLAMA_HOST while it runs; real use: a running Ollama
    # cache=None: this example counts calls (with the default cache, a second run reuses the scores)
    engine = Engine(CONFIG, registry=[write], cache=None)
    result = engine.run("The meeting moved to Monday")

print("judge client:", type(engine.judges["local"]).__module__, engine.judges["local"].model_id)
print("winner:", result.winner.candidate.data, result.winner.total)
assert engine.judges["local"].model_id == "gemma4-12b"
assert all(s.scores["clear"].value is not None for s in result.ranked)  # both were judged
assert [r["path"] for r in ollama.requests].count("/api/chat") == 2  # one judge call per candidate
