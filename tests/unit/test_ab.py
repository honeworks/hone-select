"""A/B judgement (design change 0012): the Wilson interval, pairing edge cases and the summary lines."""

import random
from collections import Counter
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.experiments import ab, ab_results
from hone_select.experiments import definition as d


def spec(**ab_keys: Any) -> d.ExperimentSpec:
    return d.ExperimentSpec.model_validate(
        {
            "title": "t",
            "generate": {"kind": "python", "function": "m:f"},
            "factors": {"model": ["a", "b", "c"]},
            "baseline": [{"name": "today", "model": "a"}],
            "scorers": {"pick": {"kind": "ab", "question": "Which?", **ab_keys}},
        }
    )


@pytest.mark.parametrize(
    ("wins", "n", "low", "high"),
    [
        (14, 19, 0.5121, 0.8819),
        (5, 10, 0.2366, 0.7634),
        (8, 10, 0.4902, 0.9433),
        (0, 10, 0.0, 0.2775),
        (1, 1, 0.2065, 1.0),
    ],
)
def test_wilson_matches_known_values(wins: int, n: int, low: float, high: float) -> None:
    got = ab_results.wilson(wins, n)
    assert got[0] == pytest.approx(low, abs=1e-4)
    assert got[1] == pytest.approx(high, abs=1e-4)


def test_wilson_without_trials_is_unknown() -> None:
    assert ab_results.wilson(0, 0) == (None, None)


def test_the_defaults_are_twenty_pairs_with_ties() -> None:
    c = spec().ab_scorers()["pick"]
    assert (c.between, c.top, c.pairs, c.allow_tie) == ("top", 2, 20, True)
    assert spec().person_scorers() == {"pick"}


def test_top_three_gives_three_pairs_of_setups() -> None:
    s = spec(top=3)
    ranking = [d.setup_id({"model": m}) for m in ("c", "b", "a")]
    pairs = ab.setup_pairs(s, s.ab_scorers()["pick"], ranking)
    assert pairs == [[ranking[0], ranking[1]], [ranking[0], ranking[2]], [ranking[1], ranking[2]]]


def test_between_baseline_and_a_list() -> None:
    a, b, c = (d.setup_id({"model": m}) for m in ("a", "b", "c"))
    s = spec(between="baseline")
    assert ab.setup_pairs(s, s.ab_scorers()["pick"], [c, a, b]) == [[c, a], [b, a]]
    s = spec(between=["today", c])
    assert ab.setup_pairs(s, s.ab_scorers()["pick"], [c, b, a]) == [[a, c]]


def test_between_is_checked() -> None:
    with pytest.raises(ConfigError, match="not setups"):
        ab.check_definition(spec(between=["today", "ghost"]))
    with pytest.raises(ConfigError, match="two different setups"):
        ab.check_definition(spec(between=["today", d.setup_id({"model": "a"})]))
    no_baseline = spec(between="baseline").model_copy(update={"baseline": []})
    with pytest.raises(ConfigError, match="needs a"):
        ab.check_definition(no_baseline)
    ab.check_definition(spec(between="top", top=3))


def test_a_bad_ab_section_fails_at_load(tmp_path: Any) -> None:
    (tmp_path / "experiment.toml").write_text(
        'title = "t"\n[generate]\nkind = "python"\nfunction = "m:f"\n'
        '[scorers.pick]\nkind = "ab"\nquestion = "?"\npairs = 0\n'
    )
    with pytest.raises(ConfigError, match=r"\[scorers.pick\] pairs"):
        d.load(tmp_path)


def test_a_case_where_one_setup_failed_pairs_what_is_left() -> None:
    a = {0: "a0", 2: "a2"}  # a's sample 1 failed
    b = {0: "b0", 1: "b1", 2: "b2"}
    same, rest = ab.matches(a, b)
    assert same == [("a0", "b0"), ("a2", "b2")]
    assert rest == []  # b1 has no partner
    same, rest = ab.matches({1: "a1"}, {0: "b0"})
    assert (same, rest) == ([], [("a1", "b0")])  # different indices only when nothing matches
    assert ab.matches({}, b) == ([], [])  # a setup that failed the whole case gives no pairs


def found(cases: list[str], samples: int, fail: dict[str, int] | None = None) -> dict:
    out: dict = {}
    for c in cases:
        for s in ("A", "B"):
            n = (fail or {}).get(f"{c}/{s}", samples)
            out[(c, s)] = {k: f"{c}__{s}__s{k}" for k in range(n)}
    return out


def test_pairs_spread_evenly_over_cases() -> None:
    cases = [f"c{i}" for i in range(8)]
    drawn = ab.draw(["A", "B"], cases, found(cases, 3), 20, random.Random(1))
    per_case = Counter(p["case"] for p in drawn)
    assert len(drawn) == 20
    assert sorted(set(per_case.values())) == [2, 3]
    assert len({(p["left"], p["right"]) for p in drawn}) == 20  # different samples
    for p in drawn:
        assert p["left"].split("__")[2] == p["right"].split("__")[2]  # the same sample index


def test_fewer_available_pairs_than_asked() -> None:
    cases = ["c1", "c2"]
    drawn = ab.draw(["A", "B"], cases, found(cases, 2, {"c2/B": 0}), 20, random.Random(0))
    assert len(drawn) == 2  # only c1 has pairs, two of them
    assert {p["case"] for p in drawn} == {"c1"}


def test_left_and_right_are_seeded() -> None:
    cases = [f"c{i}" for i in range(5)]
    first = ab.draw(["A", "B"], cases, found(cases, 2), 10, random.Random("0:ab:pick"))
    again = ab.draw(["A", "B"], cases, found(cases, 2), 10, random.Random("0:ab:pick"))
    assert first == again
    assert {p["left"].split("__")[1] for p in first} == {"A", "B"}


def pair_result(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "setups": ["x", "y"],
        "sides": {"x": {"wins": 5, "losses": 5, "ties": 0}, "y": {"wins": 5, "losses": 5, "ties": 0}},
        "win_rate": 0.5,
        "low": 0.2366,
        "high": 0.7634,
        "clear": False,
        "judged": 10,
        "planned": 10,
        "complete": True,
    }
    return base | changes


def test_summary_lines() -> None:
    res = {
        "ab": {
            "even": {"ready": True, "pairs": [pair_result()]},
            "fresh": {"ready": True, "pairs": [pair_result(judged=0, complete=False, win_rate=None)]},
            "later": {"ready": False, "pairs": []},
        }
    }
    text = "\n".join(ab_results.lines(res))
    assert "A/B (even): x is even with y, 5-5 with no ties, win rate 50 % (24-76 %), not clear." in text
    assert "A/B (fresh): x vs y, no picks yet." in text
    assert "A/B (later): waiting for the run." in text
    assert ab_results.lines({"ab": {}}) == []
