"""AC-28: test cases come from `cases.toml` fields, from one folder of input files per case, or from an
earlier experiment's outputs (its winners or every sample), so one experiment can test another's outputs."""

import json
from pathlib import Path

import pytest

from hone_select import ConfigError
from hone_select.experiments import Project, start
from hone_select.experiments import cases as cases_mod

from .experiment_helpers import approved

pytestmark = pytest.mark.e2e

READER = """
def read(case, setup, ctx):
    return {"source": case["fields"]["source_setup"], "text": (case["fields"]["input"] or "")[:12],
            "files": sorted(case["files"])}
"""


def test_ac28_case_folders_bring_their_files(tmp_path: Path) -> None:
    folder = Project(tmp_path).new("Files")
    (folder / "cases" / "scene-a").mkdir()
    (folder / "cases" / "scene-a" / "scene.blend").write_bytes(b"blend")
    (folder / "cases" / "cases.toml").write_text(
        '[[case]]\nid = "scene-a"\nframes = 24\n[[case]]\nid = "b"\n'
    )
    loaded = cases_mod.load(folder, "cases/", tmp_path / "experiments")
    by_id = {c["id"]: c for c in loaded}
    assert by_id["scene-a"]["fields"] == {"frames": 24}
    assert Path(by_id["scene-a"]["files"]["scene.blend"]).read_bytes() == b"blend"
    assert by_id["b"]["files"] == {}


def test_ac28_duplicate_or_missing_cases_are_errors(tmp_path: Path) -> None:
    folder = Project(tmp_path).new("Bad")
    (folder / "cases" / "cases.toml").write_text('[[case]]\nid = "a"\n[[case]]\nid = "a"\n')
    with pytest.raises(ConfigError, match="two test cases have the id 'a'"):
        cases_mod.load(folder, "cases/", tmp_path)
    (folder / "cases" / "cases.toml").write_text('[[case]]\ntopic = "no id"\n')
    with pytest.raises(ConfigError, match="needs an id"):
        cases_mod.load(folder, "cases/", tmp_path)
    with pytest.raises(ConfigError, match="does not exist"):
        cases_mod.load(folder, "missing/", tmp_path)


@pytest.mark.parametrize(("keep", "count"), [("winners", 2), ("all", 24)])
def test_ac28_cases_from_an_earlier_experiment(tmp_path: Path, keep: str, count: int) -> None:
    p, _ = approved(tmp_path)
    start(p, "E0001")
    (tmp_path / "reader.py").write_text(READER)
    second = p.new("Read the winners")
    (second / "experiment.toml").write_text(
        f'title = "Read"\ncases = {{ from = "E0001", keep = "{keep}" }}\n'
        '[generate]\nkind = "python"\nfunction = "reader:read"\n[factors]\nreader = ["v1"]\n'
    )
    p.plan("E0002")
    p.review("E0002", "approved")
    start(p, "E0002")
    rows = [json.loads(q.read_text()) for q in second.glob("outputs/*/*/*/result.json")]
    assert len(rows) == count
    assert all(r["error"] is None for r in rows)
    assert all(r["data"]["files"] == ["story.txt"] for r in rows)  # the earlier outputs' files are inputs
    if keep == "winners":
        winners = json.loads((p.path("E0001") / "results" / "results.json").read_text())["winners"]
        assert {r["case"] for r in rows} == set(winners.values())


def test_ac28_cases_from_an_experiment_without_results(tmp_path: Path) -> None:
    p = Project(tmp_path)
    p.new("Empty")
    with pytest.raises(ConfigError, match="no results yet"):
        cases_mod.from_experiment(p.folder, "E0001", "winners")
    with pytest.raises(ConfigError, match="no such experiment"):
        cases_mod.from_experiment(p.folder, "E0042", "all")
