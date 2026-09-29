"""AC-38: needs: a case needing "camera angle", one needing `duration_s >= 6`, a factor value over a model's
`max_duration_s`, a model with an undeclared limit: cells whose model lacks the feature or the limit are not
applicable, listed with the unmet need, not run, not failures; the undeclared limit is run and marked
`need_unknown`; per-setup numbers are over applicable cases with the count; baseline deltas and wins use only
cases both sides can do and say how many."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hone_select.cli import app
from hone_select.experiments import start

from . import fake_media
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

CLIPS = """
title = "Clips"
question = "Which video model?"
registry = ["tests.e2e.fake_media"]
[generate]
kind = "generate"
client = "tests.e2e.fake_media:music"
guides = "tests.e2e.fake_media:guides"
prompt = "{scene}"
output = "clip.mp4"
inputs = { duration_s = "{setup.duration_s}" }
[factors]
model = ["wan", "sora", "mystery"]
duration_s = [4, 8]
[[baseline]]
name = "today"
model = "wan"
duration_s = 4
[criteria]
scorers = ["by_model"]
"""
CASES = """
[[case]]
id = "angle"
scene = "a lighthouse from below"
needs = ["camera angle"]
[[case]]
id = "long"
scene = "a slow sunrise"
needs = ["duration_s >= 6"]
[[case]]
id = "plain"
scene = "a harbour"
"""
GUIDES = {
    "wan": {"features": [{"name": "camera angle", "how": "a LoRA phrase"}], "max_duration_s": 5},
    "sora": {"features": [], "durations_s": [4, 8, 12]},
    "mystery": {"features": []},  # no limits declared
}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    fake_media.reset()
    fake_media.GUIDES.update(GUIDES)
    yield
    fake_media.reset()


def _run(tmp: Path) -> tuple[dict[str, Any], Path]:
    p, folder = project(tmp, CLIPS, CASES)
    plan = p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    return plan, folder


def _cells(plan: dict[str, Any], key: str) -> dict[tuple[str, str, int], list[str]]:
    setups = plan["setups"]
    return {
        (c["case"], c["model"], setups[c["setup"]]["duration_s"]): c.get("unmet", c["needs"])
        for c in plan["applicability"][key]
    }


def test_ac38_cells_a_model_cannot_do_are_listed_and_not_run(tmp_path: Path) -> None:
    plan, folder = _run(tmp_path)
    na = _cells(plan, "not_applicable")
    assert na[("angle", "sora", 4)] == ["sora: no feature 'camera angle'"]
    assert na[("angle", "mystery", 8)] == ["mystery: no feature 'camera angle'"]
    assert na[("long", "wan", 4)] == ["wan: duration_s >= 6 (max_duration_s 5)"]
    assert na[("plain", "wan", 8)] == ["wan: duration_s = 8 (max_duration_s 5)"]  # the factor value
    assert ("long", "sora", 8) not in na  # 12 s is in its durations
    assert ("plain", "sora", 4) not in na
    assert len(na) == 2 + 3 + 1 + 2  # sora angle x2, wan at 8 s x3, wan long at 4 s, mystery angle x2
    assert plan["outputs"] == 3 * 6 - len(na)
    ran = {(c["prompt"], c["model"], c["duration_s"]) for c in fake_media.CALLS}
    assert ("a lighthouse from below", "sora", 4) not in ran
    assert ("a harbour", "wan", 8) not in ran
    assert len(fake_media.CALLS) == plan["outputs"]
    assert not list(folder.glob("outputs/angle/sora*"))


def test_ac38_an_undeclared_limit_is_run_and_marked_need_unknown(tmp_path: Path) -> None:
    plan, folder = _run(tmp_path)
    unknown = _cells(plan, "need_unknown")
    assert unknown[("long", "mystery", 4)] == ["duration_s >= 6", "duration_s = 4"]
    assert unknown[("plain", "mystery", 8)] == ["duration_s = 8"]
    sample = json.loads(next(folder.glob("outputs/long/mystery-4-*/s0/result.json")).read_text())
    assert sample["need_unknown"] == ["duration_s >= 6", "duration_s = 4"]
    assert sample["error"] is None


def test_ac38_results_count_applicable_cases_and_compare_on_shared_ones(tmp_path: Path) -> None:
    _, folder = _run(tmp_path)
    res = json.loads((folder / "results" / "results.json").read_text())
    ids = {(s["params"]["model"], s["params"]["duration_s"]): sid for sid, s in res["setups"].items()}
    by = {key: res["setups"][sid] for key, sid in ids.items()}
    wan4 = by[("wan", 4)]
    assert wan4["applicable"] == {
        "cases": 2,
        "of": 3,
        "not_applicable": 1,
        "needs": {"duration_s >= 6": 1},
        "need_unknown": 0,
    }
    assert (wan4["samples"], wan4["errors"], wan4["pass_rate"]) == (2, 0, 1.0)  # not failures
    assert wan4["total"]["mean"] == 0.9
    assert by[("wan", 8)]["samples"] == 0
    assert by[("sora", 4)]["applicable"]["needs"] == {"camera angle": 1}
    assert res["best"] == ids[("wan", 4)]
    base = res["baselines"]["today"]
    sora4 = ids[("sora", 4)]
    assert base[sora4]["shared_cases"] == 1  # only "plain": wan cannot do "long", sora cannot do "angle"
    assert (base[sora4]["diff"], base[sora4]["wins"], base[sora4]["losses"]) == (-0.3, 0, 1)
    model = res["factors"]["model"]
    assert {level: s["shared_cases"] for level, s in model.items()} == {"wan": 1, "sora": 1, "mystery": 1}
    assert model["wan"]["samples"] == 1  # the one case every model can do, at the durations it can do
    summary = (folder / "results" / "summary.md").read_text()
    assert "2 of 3; 1 not applicable: duration_s >= 6" in summary
    assert "What each model could not do" in summary
    assert "`sora` could not do: camera angle (1 of 3 cases)" in summary
    assert "shared cases" in summary


def test_ac38_without_a_guide_source_every_need_is_unknown_and_nothing_is_skipped(tmp_path: Path) -> None:
    p, _ = project(tmp_path, CLIPS.replace('guides = "tests.e2e.fake_media:guides"\n', ""), CASES)
    plan = p.plan("E0001")
    assert "models" not in plan
    assert plan["applicability"]["not_applicable"] == []
    assert len(plan["applicability"]["need_unknown"]) == 18
    assert plan["outputs"] == 18


def test_ac38_the_cli_plan_lists_the_cells_not_run(tmp_path: Path) -> None:
    project(tmp_path, CLIPS, CASES)
    fake_media.GUIDES["mystery"]["installed"] = "no"
    out = CliRunner().invoke(app, ["experiments", "plan", "E0001", "--project", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert "not applicable: angle x sora-4-" in out.output
    assert "sora: no feature 'camera angle'" in out.output
    assert (
        "not installed: mystery (start refuses until it is): hone-models models install mystery" in out.output
    )
