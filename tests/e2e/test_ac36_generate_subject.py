"""AC-36: a generate subject with a fake music client: each sample calls `generate` with its seed, `out` in
the workdir and the filled inputs (a case file as a `Path`); the candidate has the files and measurements;
`refused` is a failed sample with its kind; `out_of_memory` with conditions is `outside` and runs once more;
one session per model group."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hone_select.experiments import start

from . import fake_media
from .experiment_helpers import project

pytestmark = pytest.mark.e2e

SONGS = """
title = "Songs"
question = "Which music model?"
registry = ["tests.e2e.fake_media"]
[generate]
kind = "generate"
client = "tests.e2e.fake_media:music"
prompt = "{prompt}"
output = "take.wav"
inputs = { lyrics = "{case.lyrics}", duration_s = "{setup.duration_s}", reference = "{case.files[ref.wav]}", behaviour = "{case.behaviour}", label = "take {seed} of {case.id}" }
[factors]
model = ["ace", "levo"]
prompt = ["short.md"]
duration_s = [30]
[criteria]
scorers = ["commercial"]
measure = { elapsed_s = "lower" }
%s
"""
CONDITIONS = "[conditions]\nmin_free_ram_gb = 1"
CASES = """
[[case]]
id = "calm"
lyrics = "[verse]\\nslow waves"
mood = "calm"
behaviour = "ok"
[[case]]
id = "loud"
lyrics = "[chorus]\\nshout"
mood = "loud"
behaviour = "refused"
[[case]]
id = "heavy"
lyrics = "[verse]\\nweight"
mood = "heavy"
behaviour = "oom_once"
"""


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    fake_media.reset()
    yield
    fake_media.reset()


def _songs(tmp: Path, conditions: str = CONDITIONS) -> tuple[Any, Path]:
    p, folder = project(tmp, SONGS % conditions, CASES)
    (folder / "prompts" / "short.md").write_text("{mood} song, {duration_s} seconds")
    for case in ("calm", "loud", "heavy"):
        (folder / "cases" / case).mkdir()
        (folder / "cases" / case / "ref.wav").write_bytes(b"RIFF" + case.encode())
    p.plan("E0001")
    p.review("E0001", "approved", "go", by="tester")
    return p, folder


def _result(folder: Path, case: str, model: str) -> dict[str, Any]:
    found = [
        json.loads(r.read_text())
        for r in folder.glob(f"outputs/{case}/*/s0/result.json")
        if json.loads(r.read_text())["params"]["model"] == model
    ]
    return found[0]


def test_ac36_each_sample_calls_generate_with_its_seed_out_and_inputs(tmp_path: Path) -> None:
    p, folder = _songs(tmp_path)
    assert start(p, "E0001")["status"] == "completed"
    calm = [c for c in fake_media.CALLS if c["behaviour"] == "ok"]
    assert [c["model"] for c in calm] == ["ace", "levo"]
    call = calm[0]
    assert call["prompt"] == "calm song, 30 seconds"
    assert call["seed"] == 0
    assert call["duration_s"] == 30  # one placeholder keeps the factor's type
    assert call["lyrics"] == "[verse]\nslow waves"
    assert call["label"] == "take 0 of calm"
    ref = call["reference"]
    assert isinstance(ref, Path)
    assert ref == (folder / "cases" / "calm" / "ref.wav").resolve()
    assert call["out"].name == "take.wav"
    assert call["out"].parent.parent.name == "s0"
    assert call["out"].is_relative_to(folder / "outputs" / "calm")
    assert call["trace"]["hone.run_id"] == "E0001"
    r = _result(folder, "calm", "ace")
    assert r["error"] is None
    assert r["data"]["files"][0]["name"] == "take.wav"
    assert r["data"]["files"][0]["sha256"] == r["files"]["take.wav"]["sha256"]
    assert r["data"]["files"][0]["duration_s"] == 30.0
    assert r["measurements"]["elapsed_s"] == 2.5
    assert r["measurements"]["cost_usd"] == 0.04
    assert r["cost_usd"] == 0.04
    assert r["cost_estimated"] is True
    assert (r["license"], r["commercial_use"]) == ("Apache-2.0", True)
    selection = json.loads((folder / "outputs" / "calm" / "selection.json").read_text())
    assert selection["samples"][r["sample_id"]]["scores"]["commercial"]["value"] == 1.0  # meta has it


def test_ac36_refused_is_a_failed_sample_with_its_kind(tmp_path: Path) -> None:
    p, folder = _songs(tmp_path)
    start(p, "E0001")
    r = _result(folder, "loud", "ace")
    assert r["error"] == "the prompt was refused by moderation"
    assert r["error_kind"] == "refused"
    assert r["environment"]["status"] == "ok"  # a refusal says nothing about the machine
    selection = json.loads((folder / "outputs" / "loud" / "selection.json").read_text())
    assert selection["samples"][r["sample_id"]]["rejected"] is True
    results = json.loads((folder / "results" / "results.json").read_text())
    assert all(s["errors"] == 1 for s in results["setups"].values())


def test_ac36_out_of_memory_with_conditions_is_outside_and_runs_again_once(tmp_path: Path) -> None:
    p, folder = _songs(tmp_path)
    start(p, "E0001")
    first = json.loads(next((folder / "outputs" / "heavy").glob("*/s0/outside-1.json")).read_text())
    assert first["error_kind"] == "out_of_memory"
    assert first["environment"]["status"] == "outside"
    assert "out_of_memory" in first["environment"]["checks"]["out_of_memory"]["reason"]
    again = _result(folder, "heavy", first["params"]["model"])
    assert again["error"] is None
    assert again["environment"]["attempt"] == 2
    assert [c["model"] for c in fake_media.CALLS if c["behaviour"] == "oom_once"] == [
        "ace",
        "ace",
        "levo",
        "levo",
    ]


def test_ac36_out_of_memory_without_conditions_is_a_failure(tmp_path: Path) -> None:
    p, folder = _songs(tmp_path, conditions="")
    start(p, "E0001")
    r = _result(folder, "heavy", "ace")
    assert (r["error_kind"], r["environment"]["status"]) == ("out_of_memory", "not_checked")
    assert not list(folder.glob("outputs/heavy/*/s0/outside-1.json"))


def test_ac36_one_session_per_model_group(tmp_path: Path) -> None:
    p, _ = _songs(tmp_path, conditions="")  # no out-of-memory rerun after the group's last sample
    start(p, "E0001")
    events = fake_media.EVENTS
    assert events[0] == ("enter", "ace")
    assert events.count(("enter", "ace")) == 1
    assert events.count(("enter", "levo")) == 1
    switch = events.index(("exit", "ace"))
    assert events[switch + 1] == ("enter", "levo")
    assert all(model == "ace" for kind, model in events[:switch] if kind == "generate")
    assert all(model == "levo" for kind, model in events[switch + 1 :] if kind == "generate")
    assert events[-1] == ("exit", "levo")


def test_ac36_the_plan_shows_the_call_and_the_resolved_request(tmp_path: Path) -> None:
    p, folder = _songs(tmp_path)
    plan = json.loads((folder / "plan.json").read_text())
    assert plan["subject"] == "generate"
    assert "tests.e2e.fake_media:music(<model>).generate(" in plan["commands"][0]
    asked = next(iter(plan["asked"].values()))
    assert asked["prompt"] == "prompts/short.md"
    assert asked["inputs"]["duration_s"] == "{setup.duration_s}"
    assert asked["asked_differently"] is False
    assert p.status("E0001")["status"] == "approved"


def test_ac36_a_pilot_runs_one_generate_sample_and_ends_its_session(tmp_path: Path) -> None:
    p, folder = project(tmp_path, SONGS % "", CASES)
    (folder / "prompts" / "short.md").write_text("{mood} song")
    for case in ("calm", "loud", "heavy"):
        (folder / "cases" / case).mkdir()
        (folder / "cases" / case / "ref.wav").write_bytes(b"RIFF")
    plan = p.plan("E0001", pilot=True)
    assert plan["pilot"]["error"] is None
    assert plan["pilot"]["measurements"]["elapsed_s"] == 2.5
    assert plan["estimate"]["money_usd"] == round(0.04 * plan["outputs"], 4)
    assert fake_media.EVENTS == [("enter", "ace"), ("generate", "ace"), ("exit", "ace")]
