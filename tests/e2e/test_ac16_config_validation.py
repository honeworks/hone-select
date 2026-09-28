"""AC-16: bad configs fail early with a ConfigError that says what to fix."""

import pytest

from hone_select import ConfigError, Engine, generator, scorer

pytestmark = pytest.mark.e2e


@generator()
def gen(task, v):
    return v["index"]


@scorer()
def quality(c):
    return 0.5


def make(config: str) -> Engine:
    return Engine(config, registry=[gen, quality])


def test_ac16_unknown_scorer_in_cascade() -> None:
    with pytest.raises(ConfigError, match=r"unknown scorer 'nope'.*quality"):
        make('[score]\ncascade = [{ scorers = ["nope"] }]')


def test_ac16_weight_for_unknown_scorer() -> None:
    with pytest.raises(ConfigError, match=r"weight.*'ghost'"):
        make('[score]\ncascade = [{ scorers = ["quality"] }]\nweights = { ghost = 1.0 }')


def test_ac16_bad_policy() -> None:
    with pytest.raises(ConfigError, match=r"select\.policy.*argmax"):
        make('[score]\ncascade = [{ scorers = ["quality"] }]\n[select]\npolicy = "best_guess"')


def test_ac16_unknown_key_and_bad_toml() -> None:
    with pytest.raises(ConfigError, match="colour"):
        make('[generate]\ncolour = "red"')
    with pytest.raises(ConfigError, match="TOML"):
        make("[score\n")


def test_ac16_unknown_gate() -> None:
    with pytest.raises(ConfigError, match=r"unknown gate 'g'"):
        make('[score]\ngates = ["g"]\ncascade = [{ scorers = ["quality"] }]')


def test_ac16_valid_config_is_accepted() -> None:
    engine = make('[score]\ncascade = [{ scorers = ["quality"] }]')
    assert engine.config.score.cascade[0].scorers == ["quality"]
