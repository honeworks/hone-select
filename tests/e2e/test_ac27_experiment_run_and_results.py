"""AC-27: a run resumes where it stopped without redoing samples, stops at its budget, scores each case as a
recorded selection (trace context: the experiment id and case), and reports per setup, per factor level
and against the baseline, with the known best setup first; designs and keep_files behave as declared."""

import json
from pathlib import Path

import pytest

from hone_select._records import read_spans
from hone_select.experiments import Project, runner, start
from hone_select.experiments import definition as d

from .experiment_helpers import PYTHON_EXPERIMENT, approved

pytestmark = pytest.mark.e2e


def test_ac27_results_find_the_known_best_setup(tmp_path: Path) -> None:
    p, folder = approved(tmp_path)
    start(p, "E0001")
    res = json.loads((folder / "results" / "results.json").read_text())
    best = d.setup_id({"model": "large", "temperature": 1.0})
    assert res["best"] == best  # longest stories: the subject makes large at 1.0 the longest
    assert res["ranking"][0] == best
    assert res["setups"][best]["wins"] == 2  # it wins both cases
    assert res["setups"][best]["pass_rate"] == 1.0
    total = res["setups"][best]["total"]
    assert total["low"] <= total["mean"] <= total["high"]
    models = res["factors"]["model"]
    assert (
        models["large"]["total"]["mean"]
        > models["medium"]["total"]["mean"]
        > models["small"]["total"]["mean"]
    )
    assert set(res["setups"][best]["criteria"]) == {"length", "words"}  # ran_ok is a gate, not a criterion
    today = res["baselines"]["today"]
    assert today[best]["diff"] > 0
    assert today[best]["clear"] is True
    assert set(res["winners"]) == {"keeper", "ferry"}
    summary = (folder / "results" / "summary.md").read_text()
    assert f"**Best setup:** `{best}`" in summary
    assert "## model" in summary
    assert "## Against baseline `today`" in summary


def test_ac27_each_case_is_a_recorded_selection(tmp_path: Path) -> None:
    p, folder = approved(tmp_path)
    start(p, "E0001")
    selection = json.loads((folder / "outputs" / "keeper" / "selection.json").read_text())
    assert len(selection["samples"]) == 12
    runs = [
        s for s in read_spans(tmp_path / ".hone" / "select" / "spans.db") if s["name"] == "hone.select.run"
    ]
    assert {r["attributes"]["hone.item"] for r in runs} == {"keeper", "ferry"}
    assert {r["attributes"]["hone.run_id"] for r in runs} == {"E0001"}
    assert selection["run_id"] in {r["span_id"] for r in runs}


def test_ac27_stop_then_start_resumes_without_redoing(tmp_path: Path) -> None:
    p, folder = approved(tmp_path)
    seen: list[str] = []

    def stop_after_five(result: dict) -> None:
        seen.append(result["sample_id"])
        if len(seen) == 5:
            runner.stop(p, "E0001")

    status = start(p, "E0001", on_sample=stop_after_five)
    assert status["status"] == "stopped"
    assert status["done"] == 5
    stamps = {q: q.stat().st_mtime_ns for q in folder.glob("outputs/*/*/*/result.json")}
    status = start(p, "E0001", on_sample=lambda result: seen.append(result["sample_id"]))
    assert status["status"] == "completed"
    assert len(seen) == 24  # 5 before the stop + 19 after, none twice
    assert len(set(seen)) == 24
    assert all(q.stat().st_mtime_ns == t for q, t in stamps.items())  # done samples were not redone


def test_ac27_budget_stops_the_run(tmp_path: Path) -> None:
    p, _ = approved(tmp_path, PYTHON_EXPERIMENT + "[budget]\nseconds = 0.000001\n")
    status = start(p, "E0001")
    assert status["status"] == "stopped"
    assert status["done"] == 1  # the first sample used the whole budget


def test_ac27_one_at_a_time_and_list_designs(tmp_path: Path) -> None:
    base = PYTHON_EXPERIMENT.replace(
        '[factors]\nmodel = ["small", "medium", "large"]\ntemperature = [0.0, 1.0]',
        '[factors]\nmodel = ["small", "medium", "large"]\ntemperature = [0.0, 0.5, 1.0]',
    )
    oat = base + '[design]\nkind = "one_at_a_time"\n'
    (tmp_path / "a").mkdir()
    p, folder = approved(tmp_path / "a", oat)
    setups = d.setups(d.load(folder))
    # the baseline, then each factor varied alone around it: 1 + (3 - 1) + (3 - 1) distinct setups
    assert len(setups) == 5
    assert setups[0] == {"model": "small", "temperature": 0.0}
    listed = base + '[design]\nkind = "list"\n[[setup]]\nmodel = "large"\ntemperature = 1.0\n'
    (tmp_path / "b").mkdir()
    listed_folder = approved(tmp_path / "b", listed)[1]
    assert d.setups(d.load(listed_folder)) == [
        {"model": "small", "temperature": 0.0},
        {"model": "large", "temperature": 1.0},
    ]
    assert isinstance(p, Project)


@pytest.mark.parametrize(("keep", "kept"), [("all", True), ("none", False)])
def test_ac27_keep_files(tmp_path: Path, keep: str, kept: bool) -> None:
    exp = PYTHON_EXPERIMENT.replace(
        'function = "subjects:write"', f'function = "subjects:write"\nkeep_files = "{keep}"'
    )
    p, folder = approved(tmp_path, exp)
    start(p, "E0001")
    result = next(folder.glob("outputs/*/*/*/result.json"))
    files = result.parent / "files" / "story.txt"
    assert files.is_file() is kept
    assert "story.txt" in json.loads(result.read_text())["files"]  # its size and hash stay recorded


def test_ac27_judge_agreement_is_reported(tmp_path: Path) -> None:
    exp = PYTHON_EXPERIMENT.replace(
        'scorers = ["length"]', 'scorers = ["length", "length_again"]\ncompare = [["length", "length_again"]]'
    )
    (tmp_path / "again.py").write_text(
        "from hone_select import scorer\n\n\n@scorer('length_again')\ndef again(c):\n    return 0.5\n"
    )
    exp = exp.replace('registry = ["subjects"]', 'registry = ["subjects", "again"]')
    p, folder = approved(tmp_path, exp)
    start(p, "E0001")
    res = json.loads((folder / "results" / "results.json").read_text())
    assert res["agreement"]["length vs length_again"] > 0
