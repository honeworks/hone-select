"""The installed hone-models decision client through `client = "hone_models:decision"`, offline.

Uses hone-models' own FakeOllama (a local HTTP fake: no model, no GPU). Skipped without hone-models.
"""

import pytest

from hone_select import Engine, generator
from hone_select.testing import contracts

hone_models = pytest.importorskip("hone_models")
FakeOllama = pytest.importorskip("hone_models.testing").FakeOllama

CONFIG = """
[judges.local]
client = "hone_models:decision"
model = "gemma4-12b"
[generate]
n = 3
[score]
cascade = [{ scorers = ["clear"] }]
[scorers.clear]
kind = "prompt"
judge = "local"
criteria = "The sentence is clear."
"""


@generator()
def write(task, v):
    return f"{task} {v['index']}"


def test_hone_models_decision_client_passes_our_contract() -> None:
    with FakeOllama():
        contracts.check_decision_client(hone_models.decision("gemma4-12b"))


def test_config_judge_resolves_to_hone_models_and_scores_every_candidate() -> None:
    with FakeOllama() as ollama:
        engine = Engine(CONFIG, registry=[write], cache=None)
        result = engine.run("A clear sentence")
    assert getattr(engine.judges["local"], "model_id", None) == "gemma4-12b"  # not part of the port
    assert all(s.scores["clear"].value is not None for s in result.ranked)
    assert [r["path"] for r in ollama.requests].count("/api/chat") == 3
