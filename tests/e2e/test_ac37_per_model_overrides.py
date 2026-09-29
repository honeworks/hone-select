"""AC-37: per-model overrides (`[generate.per_model]`, a case's `per_model`, `prompts/<model>/guided.md` next
to `prompts/guided.md`): each model receives its own prompt and inputs, the others the shared ones;
`plan.json` shows the resolved prompt and inputs per setup and marks setups "asked differently"; judges
never see an override; an override that names no factor or case field is a `ConfigError` at plan time."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.experiments import start

from . import fake_media
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

IMAGES = """
title = "Angles"
question = "Which image model follows a camera angle?"
registry = ["tests.e2e.fake_media"]
[generate]
kind = "generate"
client = "tests.e2e.fake_media:music"
prompt = "{prompt}"
output = "shot.png"
inputs = { size = "1024x1024" }
[generate.per_model."qwen-image-edit-2511"]
inputs = { camera_angle = "{case.angle}" }
[generate.per_model."z-image-turbo"]
prompt = "{scene}, {angle} view, photo"
[factors]
model = ["gpt-image-1.5", "qwen-image-edit-2511", "z-image-turbo", "flux.2-klein-4b"]
prompt = ["guided.md"]
[criteria]
scorers = ["sees_case"]
"""
CASES = """
[[case]]
id = "c01-street"
scene = "a street at noon"
angle = "top_down"
[[case]]
id = "c07-rooftop"
scene = "a girl on a rooftop at night"
angle = "low_angle"
judge_view = ["scene"]
[case.per_model."gpt-image-1.5"]
scene = "a girl on a rooftop at night. Camera: low angle, looking up at her against the sky."
"""


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    fake_media.reset()
    yield
    fake_media.reset()


def _images(tmp: Path, experiment: str = IMAGES, cases: str = CASES) -> tuple[Any, Path]:
    p, folder = project(tmp, experiment, cases)
    (folder / "prompts" / "guided.md").write_text("Draw {scene}.")
    (folder / "prompts" / "gpt-image-1.5").mkdir()
    (folder / "prompts" / "gpt-image-1.5" / "guided.md").write_text("Please draw: {scene} Keep it realistic.")
    return p, folder


def _calls(model: str) -> dict[str, dict[str, Any]]:
    return {c["out"].parts[-5]: c for c in fake_media.CALLS if c["model"] == model}


def test_ac37_each_model_is_asked_its_own_way(tmp_path: Path) -> None:
    p, _ = _images(tmp_path)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    gpt = _calls("gpt-image-1.5")
    assert gpt["c01-street"]["prompt"] == "Please draw: a street at noon Keep it realistic."
    assert gpt["c07-rooftop"]["prompt"] == (
        "Please draw: a girl on a rooftop at night. Camera: low angle, looking up at her against the sky. "
        "Keep it realistic."
    )
    qwen = _calls("qwen-image-edit-2511")
    assert qwen["c07-rooftop"]["prompt"] == "Draw a girl on a rooftop at night."
    assert qwen["c07-rooftop"]["camera_angle"] == "low_angle"
    assert qwen["c07-rooftop"]["size"] == "1024x1024"
    z = _calls("z-image-turbo")
    assert z["c01-street"]["prompt"] == "a street at noon, top_down view, photo"
    assert "camera_angle" not in z["c01-street"]
    flux = _calls("flux.2-klein-4b")
    assert flux["c07-rooftop"]["prompt"] == "Draw a girl on a rooftop at night."
    assert set(flux["c07-rooftop"]) >= {"size"}
    assert "camera_angle" not in flux["c07-rooftop"]


def test_ac37_the_plan_and_results_mark_setups_asked_differently(tmp_path: Path) -> None:
    p, folder = _images(tmp_path)
    plan = p.plan("E0001")
    by_model = {a["model"]: a for a in plan["asked"].values()}
    gpt = by_model["gpt-image-1.5"]
    assert gpt["prompt"] == "prompts/gpt-image-1.5/guided.md"
    assert gpt["asked_differently"] is True
    assert gpt["overrides"]["prompt_files"] == ["prompts/gpt-image-1.5/guided.md"]
    assert list(gpt["case_overrides"]) == ["c07-rooftop"]
    qwen = by_model["qwen-image-edit-2511"]
    assert qwen["inputs"] == {"size": "1024x1024", "camera_angle": "{case.angle}"}
    assert qwen["overrides"]["per_model"] == {"inputs": {"camera_angle": "{case.angle}"}}
    assert by_model["z-image-turbo"]["prompt"] == "{scene}, {angle} view, photo"
    flux = by_model["flux.2-klein-4b"]
    assert (flux["asked_differently"], flux["prompt"], flux["inputs"]) == (
        False,
        "prompts/guided.md",
        {"size": "1024x1024"},
    )
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    results = json.loads((folder / "results" / "results.json").read_text())
    marks = {s["params"]["model"]: s["asked_differently"] for s in results["setups"].values()}
    assert marks == {
        "gpt-image-1.5": True,
        "qwen-image-edit-2511": True,
        "z-image-turbo": True,
        "flux.2-klein-4b": False,
    }
    summary = (folder / "results" / "summary.md").read_text()
    assert "asked differently" in summary
    sample = json.loads(next(folder.glob("outputs/c07-rooftop/gptimage1.5-*/s0/result.json")).read_text())
    assert sample["asked_differently"]["case"] == {"scene": gpt["case_overrides"]["c07-rooftop"]["scene"]}


def test_ac37_judges_never_see_an_override(tmp_path: Path) -> None:
    p, _ = _images(tmp_path)
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    start(p, "E0001")
    seen = [s for s in fake_media.SEEN if "__gptimage1.5" in s["sample"]]
    rooftop = next(s for s in seen if s["sample"].startswith("c07-rooftop"))
    assert rooftop["case"] == {"scene": "a girl on a rooftop at night"}  # judge_view, the shared field
    assert rooftop["data"]["case"] == {"scene": "a girl on a rooftop at night"}
    assert "Camera" not in json.dumps(rooftop)
    street = next(s for s in seen if s["sample"].startswith("c01-street"))
    assert street["case"] == {"scene": "a street at noon", "angle": "top_down"}


@pytest.mark.parametrize(
    ("experiment", "cases", "message"),
    [
        (IMAGES.replace('"{case.angle}"', '"{case.lens}"'), CASES, "name no factor or case field"),
        (IMAGES.replace("{scene}, {angle}", "{scene}, {mood}"), CASES, "name no factor or case field"),
        (IMAGES.replace('per_model."z-image-turbo"', 'per_model."sdxl"'), CASES, "'sdxl' is not a model"),
        (
            IMAGES,
            CASES.replace('scene = "a girl on a rooftop at night. Camera', 'lens = "a girl. Camera'),
            "not fields of the case",
        ),
        (
            IMAGES,
            CASES.replace('case.per_model."gpt-image-1.5"', 'case.per_model."dalle"'),
            "'dalle' is not a model",
        ),
        (IMAGES, CASES.replace('judge_view = ["scene"]', 'judge_view = "scene"'), "list of strings"),
    ],
)
def test_ac37_an_override_that_names_nothing_is_refused_at_plan_time(
    tmp_path: Path, experiment: str, cases: str, message: str
) -> None:
    p, _ = _images(tmp_path, experiment, cases)
    with pytest.raises(ConfigError, match=message):
        p.plan("E0001")
