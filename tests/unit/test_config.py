from pathlib import Path

import pytest

from hone_select import ConfigError, SelectionConfig
from hone_select.config import check_config, load_config

EXAMPLE = Path(__file__).parents[1] / "fixtures" / "selection.toml"


def test_full_spec_example_parses() -> None:
    config = load_config(EXAMPLE)
    assert config.generate.n == 6
    assert config.score.cascade[1].keep_top == 2
    assert config.scorers["singability"].kind == "prompt"
    assert config.scorers["tests_pass"].kind == "command"
    assert config.judges["local"].client == "hone_models:decision"
    assert config.judges["local"].model_extra == {"model": "qwen2.5vl-7b"}
    assert config.select.escalate == "pairwise"
    assert config.budget.max_money_usd == 2.0


def test_path_string_and_config_object() -> None:
    assert load_config(str(EXAMPLE)).generate.n == 6
    obj = SelectionConfig()
    assert load_config(obj) is obj
    with pytest.raises(ConfigError, match="not found"):
        load_config("missing.toml")


def test_defaults() -> None:
    config = load_config("")
    assert config.select.policy == "argmax"
    assert config.select.fallback == "none"
    assert config.dedup.method == "exact"
    assert config.record.sink == "sqlite"


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[generate]\nn = 0", "generate.n"),
        ("[generate]\nvary = { temperature = [] }", "vary.temperature must be a non-empty list"),
        ('[generate]\nvary = { seed = "random" }', "or 'increment'"),
        ('[generate]\nvary = { seed = ["a"] }', "list of integers"),
        ('[generate]\nvary = { top_p = "high" }', "vary.top_p"),
        ("[score]\nweights = { a = -1 }", "weights.a"),
        ('[score]\naggregate = "median"', "score.aggregate"),
        ('[scorers.x]\nkind = "magic"', "scorers.x"),
        ('[scorers.x]\nkind = "command"', "command"),
    ],
)
def test_invalid_values(toml: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(toml)


def test_check_config_policy_requirements() -> None:
    names = {"scorer": {"s"}, "gate": set[str](), "pairwise": {"p"}, "generator": set[str]()}
    base = '[score]\ncascade = [{ scorers = ["s"] }]\n[select]\n'
    with pytest.raises(ConfigError, match="needs select.threshold"):
        check_config(load_config(base + 'policy = "first_above"'), names)
    with pytest.raises(ConfigError, match="select.pairwise must name"):
        check_config(load_config(base + 'escalate = "pairwise"'), names)
    with pytest.raises(ConfigError, match="unknown pairwise judge 'q'"):
        check_config(load_config(base + 'policy = "pairwise_tournament"\npairwise = "q"'), names)
    check_config(load_config(base + 'policy = "pairwise_tournament"\npairwise = "p"'), names)
