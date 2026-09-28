"""AC-20 [real]: PromptScorer and PromptPairwise through OpenAIDecisionClient against Ollama's
OpenAI-compatible endpoint. Run via: scripts/gpu-lock.sh uv run pytest -m gpu"""

import os
from collections.abc import Callable
from typing import Any

import pytest

from hone_select import Candidate, Engine, PromptPairwise, PromptScorer
from hone_select.testing import contracts

openai = pytest.importorskip("openai")
OpenAIDecisionClient = pytest.importorskip("hone_select.adapters.openai").OpenAIDecisionClient

pytestmark = [pytest.mark.gpu, pytest.mark.ollama, pytest.mark.slow]
OLLAMA_URL = os.environ.get("HONE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")

CRITERIA = "The line is a vivid, singable pop chorus about summer, in correct English."
GOOD = "Sun on our shoulders, we dance till the night turns gold"
MEDIOCRE = "summer is a season that is warm and it happens every year"
BAD = "asdf qwer summer zxcv ;;; 42"


@pytest.fixture(scope="module")
def judge(ollama_model: Callable[[str, str], str]) -> Any:
    model = ollama_model("HONE_TEST_TEXT_MODEL", "gemma4-12b:latest")
    client = openai.OpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama", timeout=300)
    return OpenAIDecisionClient(client, model=model, seed=1, max_tokens=800)


def test_ac20_contract_on_real_model(judge: Any) -> None:
    contracts.check_decision_client(judge)


def test_ac20_clearly_better_candidate_wins(judge: Any) -> None:
    quality = PromptScorer("quality", CRITERIA, judge)
    better = PromptPairwise("better", CRITERIA, judge)
    config = """
[score]
cascade = [{ scorers = ["quality"] }]
[select]
tie_margin = 1.0
escalate = "pairwise"
pairwise = "better"
"""
    ranked = Engine(config, registry=[quality, better], cache=None).score(
        [Candidate.of(BAD), Candidate.of(MEDIOCRE), Candidate.of(GOOD)]
    )
    assert all(s.scores["quality"].value is not None for s in ranked)
    assert ranked[0].candidate.data == GOOD
    value = {s.candidate.data: s.scores["quality"].value or 0.0 for s in ranked}
    assert value[GOOD] > value[BAD]
