"""AC-42: the results after 20 picks (14-5-1): wins, losses and ties of each side, the win rate 73.7 % with
its Wilson interval, `clear`, `complete`, the summary line and the winner per case; an unfinished A/B shows
`complete: false`; `experiments report` recomputes."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hone_select.cli import app
from hone_select.experiments import ab, report, start
from hone_select.experiments import definition as d

from .ab_helpers import AB_EXPERIMENT, FAILING, LARGE, MEDIUM, ab_plan, result_of
from .experiment_helpers import approved

pytestmark = pytest.mark.e2e

EIGHT_CASES = "".join(f'[[case]]\nid = "c{i}"\ntopic = "topic {i}"\n' for i in range(1, 9))


def run(tmp: Path) -> Path:
    (tmp / "ab_subjects.py").write_text(FAILING)
    p, folder = approved(tmp, AB_EXPERIMENT.format(ab="pairs = 20"), EIGHT_CASES)
    start(p, "E0001")
    return folder


def wanted(folder: Path) -> list[str]:
    """The winner of each planned pair: medium wins every pair of case c1, the first other pair is a tie,
    medium wins the next ones until it has 5 wins, and large wins the rest (14-5-1 over 20)."""
    plan = ab_plan(folder)["criteria"]["owner_pick"]["pairs"]
    c1 = sum(p["case"] == "c1" for p in plan)
    others = ["tie"] + [MEDIUM] * (5 - c1) + [LARGE] * 20
    out: list[str] = []
    for p in plan:
        out.append(MEDIUM if p["case"] == "c1" else others.pop(0))
    return out


def pick(folder: Path, n: int) -> None:
    """The first n picks, through the next pair the dashboard would show."""
    spec = d.load(folder)
    plan = ab_plan(folder)["criteria"]["owner_pick"]["pairs"]
    winners = wanted(folder)
    for _ in range(n):
        index = ab.next_pair(folder, spec, "owner_pick")["index"]
        want, left = winners[index], result_of(folder, plan[index]["left"])["setup"]
        choice = "tie" if want == "tie" else "left" if want == left else "right"
        ab.add(folder, spec, "owner_pick", index, choice)


def results(folder: Path) -> dict[str, Any]:
    res = report(folder, d.load(folder), json.loads((folder / "plan.json").read_text()))
    return res["ab"]["owner_pick"]


def test_ac42_results_after_twenty_picks(tmp_path: Path) -> None:
    folder = run(tmp_path)
    assert len(ab_plan(folder)["criteria"]["owner_pick"]["pairs"]) == 20
    pick(folder, 20)
    [pair] = results(folder)["pairs"]
    assert pair["setups"] == [LARGE, MEDIUM]  # the setup with more wins first
    assert pair["sides"] == {
        LARGE: {"wins": 14, "losses": 5, "ties": 1},
        MEDIUM: {"wins": 5, "losses": 14, "ties": 1},
    }
    assert pair["win_rate"] == pytest.approx(0.7368, abs=1e-4)  # 14 of the 19 picks that are not ties
    assert pair["low"] == pytest.approx(0.5121, abs=1e-4)  # Wilson, 95 %
    assert pair["high"] == pytest.approx(0.8819, abs=1e-4)
    assert pair["clear"] is True
    assert (pair["judged"], pair["planned"], pair["requested"], pair["complete"]) == (20, 20, 20, True)
    assert pair["cases"]["c1"] == MEDIUM  # a setup that wins only on one kind of input shows
    assert LARGE in pair["cases"].values()
    summary = (folder / "results" / "summary.md").read_text()
    line = f"A/B (owner_pick): {LARGE} beats {MEDIUM}, 14-5 with 1 tie, win rate 74 % (51-88 %), clear."
    assert line in summary


def test_ac42_an_unfinished_ab_is_not_complete(tmp_path: Path) -> None:
    folder = run(tmp_path)
    res = results(folder)
    assert res["ready"] is True
    [pair] = res["pairs"]
    assert (pair["judged"], pair["complete"], pair["win_rate"], pair["clear"]) == (0, False, None, False)
    pick(folder, 7)
    [pair] = results(folder)["pairs"]
    assert (pair["judged"], pair["complete"]) == (7, False)
    assert "7 of 20 picks so far" in (folder / "results" / "summary.md").read_text()


def test_ac42_experiments_report_recomputes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = run(tmp_path)
    pick(folder, 20)
    monkeypatch.chdir(tmp_path)
    out = CliRunner().invoke(app, ["experiments", "report", "E0001"])
    assert out.exit_code == 0, out.output
    saved = json.loads((folder / "results" / "results.json").read_text())
    assert saved["ab"]["owner_pick"]["pairs"][0]["complete"] is True
    assert "beats" in (folder / "results" / "summary.md").read_text()
