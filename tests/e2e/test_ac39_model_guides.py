"""AC-39: guides: `plan.json` stores each model's guide and the dashboard shows it; `{model_guide}` and
`ctx.model_guide` get the stored guide; license and `commercial_use` appear in the results and a
non-commercial model is marked, not removed; a model reported not installed is listed with its install
command and `start` refuses until it is installed; without hone-select[models] a declared `guides` is a
`ConfigError` naming it and needs are `need_unknown`; `FakeModelGuides` passes `check_model_guides`."""

import json
import sys
import textwrap
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from hone_select import ConfigError, HoneSelectError
from hone_select.dashboard import make_server
from hone_select.experiments import start
from hone_select.testing import FakeModelGuides, check_model_guides

from . import fake_media
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

GUIDES = {
    "ace": {
        "kind": "music",
        "summary": "Full songs with vocals; follows genre tags well.",
        "prompt": "Comma-separated tags: genre, mood, instruments.",
        "features": [{"name": "lyrics", "how": "sections in brackets", "examples": ["[verse]"]}],
        "max_duration_s": 600,
        "license": "Apache-2.0",
        "commercial_use": True,
        "installed": "yes",
        "source": "https://github.com/ace-step/ACE-Step-1.5",
        "checked": "2026-09-29",
    },
    "yue": {
        "kind": "music",
        "summary": "Long songs in many languages.",
        "license": "CC-BY-NC-4.0",
        "commercial_use": False,
        "installed": "no",
        "install": "hone-models models install yue",
    },
}
SONGS = """
title = "Songs"
question = "Which music model?"
registry = ["tests.e2e.fake_media"]
[generate]
kind = "generate"
client = "tests.e2e.fake_media:music"
guides = "tests.e2e.fake_media:guides"
prompt = "{mood} song"
output = "take.wav"
[factors]
model = ["ace", "yue"]
[criteria]
scorers = ["commercial"]
"""
CASES = '[[case]]\nid = "calm"\nmood = "calm"\n'


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    fake_media.reset()
    fake_media.GUIDES.update(json.loads(json.dumps(GUIDES)))
    yield
    fake_media.reset()


@contextmanager
def serving(root: Path) -> Iterator[str]:
    srv = make_server(root / "no-store.db", port=0, project=root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - a local test server
        return json.loads(r.read())


def test_ac39_the_plan_stores_the_guides_and_the_dashboard_shows_them(tmp_path: Path) -> None:
    p, _ = project(tmp_path, SONGS, CASES)
    plan = p.plan("E0001")
    ace, yue = plan["models"]["ace"], plan["models"]["yue"]
    assert ace["guide"]["summary"] == GUIDES["ace"]["summary"]
    assert ace["guide"]["features"][0]["examples"] == ["[verse]"]
    assert (ace["installed"], ace["license"], ace["commercial_use"]) == ("yes", "Apache-2.0", True)
    assert (yue["installed"], yue["install"]) == ("no", "hone-models models install yue")
    with serving(tmp_path) as base:
        detail = get(base + "/api/experiments/E0001")
        page = urllib.request.urlopen(base + "/", timeout=10).read().decode()  # noqa: S310
    assert detail["plan"]["models"]["ace"]["guide"]["prompt"] == GUIDES["ace"]["prompt"]
    assert detail["plan"]["models"]["yue"]["installed"] == "no"
    for text in ("Model guides", "modelsCard(x)", "not installed", "Not applicable", "asked differently"):
        assert text in page


def test_ac39_a_model_not_installed_refuses_start_until_it_is(tmp_path: Path) -> None:
    p, _ = project(tmp_path, SONGS, CASES)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    with pytest.raises(HoneSelectError, match=r"not installed: yue.*hone-models models install yue"):
        start(p, "E0001")
    assert fake_media.CALLS == []
    fake_media.GUIDES["yue"]["installed"] = "yes"  # the owner installed it
    assert start(p, "E0001")["status"] == "completed"


def test_ac39_license_in_the_results_and_a_non_commercial_model_is_marked(tmp_path: Path) -> None:
    fake_media.GUIDES["yue"]["installed"] = "yes"
    p, folder = project(tmp_path, SONGS, CASES)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    res = json.loads((folder / "results" / "results.json").read_text())
    assert res["models"]["yue"] == {"license": "CC-BY-NC-4.0", "commercial_use": False}
    assert res["models"]["ace"] == {"license": "Apache-2.0", "commercial_use": True}
    assert len(res["ranking"]) == 2  # marked, never removed
    summary = (folder / "results" / "summary.md").read_text()
    assert "`yue`: non-commercial (CC-BY-NC-4.0)" in summary
    assert "`ace`: non-commercial" not in summary


WRITER = """
title = "Prompt writers"
question = "Which chat model writes the best image prompt for each image model?"
[generate]
kind = "prompt"
client = "tests.e2e.fake_media:writer"
guides = "tests.e2e.fake_media:guides"
prompt = "Write a prompt for {target_model}.\\n{model_guide}\\nScene: {mood}"
[factors]
model = ["writer-a"]
target_model = ["ace", "yue"]
"""


def test_ac39_the_prompt_subject_gets_the_target_models_guide(tmp_path: Path) -> None:
    fake_media.GUIDES["yue"]["installed"] = "yes"
    p, _ = project(tmp_path, WRITER, CASES)
    p.plan("E0001")
    fake_media.GUIDES["ace"]["summary"] = "changed after the plan"  # the run uses the stored guide
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    prompts = {t["target_model"]: t["messages"][-1]["content"] for t in fake_media.TEXTS}
    assert "Write a prompt for ace." in prompts["ace"]
    assert GUIDES["ace"]["summary"] in prompts["ace"]
    assert "Prompt: Comma-separated tags" in prompts["ace"]
    assert "- feature lyrics: sections in brackets (e.g. [verse])" in prompts["ace"]
    assert "changed after the plan" not in prompts["ace"]
    assert GUIDES["yue"]["summary"] in prompts["yue"]


GUIDE_SUBJECT = """
def summary(case, setup, ctx):
    return (ctx.model_guide or {}).get("summary", "no guide")
"""


def test_ac39_a_python_subject_gets_ctx_model_guide(tmp_path: Path) -> None:
    experiment = """
    title = "Guides"
    question = "Does the subject see the guide?"
    [generate]
    kind = "python"
    function = "guide_subject:summary"
    guides = "tests.e2e.fake_media:guides"
    [factors]
    model = ["ace", "nobody"]
    """
    p, folder = project(tmp_path, textwrap.dedent(experiment), CASES)
    (tmp_path / "guide_subject.py").write_text(GUIDE_SUBJECT)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    data = {
        json.loads(r.read_text())["params"]["model"]: json.loads(r.read_text())["data"]
        for r in folder.glob("outputs/*/*/s0/result.json")
    }
    assert data == {"ace": GUIDES["ace"]["summary"], "nobody": "no guide"}


def test_ac39_without_the_extra_a_declared_guides_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "hone_models", None)  # hone-select without hone-select[models]
    experiment = SONGS.replace('"tests.e2e.fake_media:guides"', '"hone_models:guides"')
    p, _ = project(tmp_path, experiment, CASES)
    with pytest.raises(ConfigError, match=r"install hone-select\[models\]"):
        p.plan("E0001")
    (tmp_path / "bad").mkdir()
    p, _ = project(tmp_path / "bad", SONGS.replace("tests.e2e.fake_media:guides", "nope"), CASES)
    with pytest.raises(
        ConfigError, match=r"guides 'nope' is neither an installed guide source \(\[.*'hone_models:guides'"
    ):
        p.plan("E0001")
    (tmp_path / "none").mkdir()
    cases = '[[case]]\nid = "calm"\nmood = "calm"\nneeds = ["lyrics"]\n'
    p, _ = project(tmp_path / "none", SONGS.replace('guides = "tests.e2e.fake_media:guides"\n', ""), cases)
    plan = p.plan("E0001")
    assert [c["needs"] for c in plan["applicability"]["need_unknown"]] == [["lyrics"], ["lyrics"]]
    assert plan["outputs"] == 2


def test_ac39_fake_model_guides_passes_the_contract() -> None:
    check_model_guides(FakeModelGuides(GUIDES), known="ace")
    check_model_guides(FakeModelGuides())
    with pytest.raises(AssertionError, match="installed"):
        check_model_guides(FakeModelGuides({"x": {"installed": "maybe"}}), known="x")
