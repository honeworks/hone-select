"""Units of design change 0011: needs against guides, guide text, client resolution, the generate result,
overrides and the definition checks."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.experiments import applicable, generation, guides, overrides, summary
from hone_select.experiments import definition as d


@pytest.mark.parametrize(
    ("need", "guide", "state"),
    [
        ("camera angle", {"features": [{"name": "Camera Angle"}]}, "met"),
        ("lyrics", {"inputs": {"lyrics": "sections"}}, "met"),
        ("references", {"inputs": ["prompt", "references"]}, "met"),
        ("camera angle", {}, "unmet"),
        ("duration_s >= 60", {"max_duration_s": 47}, "unmet"),
        ("duration_s >= 60", {"max_duration_s": 600}, "met"),
        ("duration_s >= 60", {"durations_s": [4, 8, 12]}, "unmet"),
        ("duration_s = 8", {"durations_s": [4, 8, 12]}, "met"),
        ("duration_s = 6", {"durations_s": [4, 8, 12]}, "unmet"),
        ("duration_s <= 4", {"max_duration_s": 5}, "met"),
        ("duration_s >= 60", {}, "unknown"),
        ("references >= 3", {"max_references": 3}, "met"),
        ("references >= 3", {"max_references": 1}, "unmet"),
        ("size = 1024x1024", {"sizes": ["1024x1024"]}, "met"),
        ("size = 512x512", {"sizes": ["1024x1024"]}, "unmet"),
        ("size = 512x512", {}, "unknown"),
        ("duration_s >= long", {"max_duration_s": 5}, "unknown"),
        ("camera angle", None, "unknown"),
    ],
)
def test_needs_against_a_guide(need: str, guide: dict[str, Any] | None, state: str) -> None:
    assert applicable.judge(need, guide)[0] == state


def test_unmet_reasons_name_the_limit() -> None:
    assert (
        applicable.judge("duration_s >= 60", {"durations_s": [4, 8]})[1]
        == "duration_s >= 60 (durations_s [4, 8])"
    )
    assert (
        applicable.judge("references >= 3", {"max_references": 1})[1] == "references >= 3 (max_references 1)"
    )


def test_a_guide_as_text() -> None:
    assert guides.text(None) == ""
    assert guides.text({"text": "the source's own text"}) == "the source's own text"
    g = {
        "id": "qwen",
        "kind": "image",
        "summary": "Edits references.",
        "inputs": ["references", "camera_angle"],
        "features": [{"name": "camera angle"}],
        "max_references": 3,
        "commercial_use": True,
    }
    text = guides.text(g)
    assert text.splitlines()[:2] == ["qwen (image)", "Edits references."]
    assert "- input references" in text
    assert "- feature camera angle: " in text
    assert "max_references: 3" in text
    assert "- input key: e.g. 'C minor'" in guides.text({"inputs": {"key": "e.g. 'C minor'", "bpm": ""}})


def test_the_guide_source_defaults_to_hone_models_for_its_clients() -> None:
    assert (
        guides.source_name(d.GenerateSpec(kind="generate", client="hone_models:image"))
        == "hone_models:guides"
    )
    assert guides.source_name(d.GenerateSpec(kind="generate", client="mine:image")) is None
    assert guides.source_name(d.GenerateSpec(kind="python", function="a:b", guides="mine:g")) == "mine:g"


class EntryPoint:
    def __init__(self, value: Any) -> None:
        self.value = value

    def load(self) -> Any:
        return self.value


def test_a_client_resolves_through_its_entry_point_group(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def entry_points(*, group: str, name: str) -> list[EntryPoint]:
        seen.append((group, name))
        return [EntryPoint(lambda model: f"client for {model}")] if group == "hone.music_clients" else []

    monkeypatch.setattr(generation, "entry_points", entry_points)
    assert generation.factory("hone_models:music")("ace") == "client for ace"
    assert seen == [("hone.music_clients", "hone_models")]
    assert generation.factory("json:dumps") is json.dumps  # not an entry point: "module:factory"
    with pytest.raises(ConfigError, match="cannot import"):
        generation.factory("no_such_module:video")  # neither an entry point nor importable
    with pytest.raises(ConfigError, match="hone_models"):
        generation.factory("hone_models:no_such_factory")  # a hone_models: name that does not resolve


def test_a_client_that_cannot_be_built_is_a_config_error() -> None:
    g = d.GenerateSpec(kind="generate", client="json:loads", client_args={"nope": 1})
    with pytest.raises(ConfigError, match="could not be built for 'x'"):
        generation.client(g, "x")


@dataclass
class Media:
    path: Path
    sha256: str = "abc"
    bytes: int = 3


def test_a_media_result_as_a_sample(tmp_path: Path) -> None:
    @dataclass
    class Failed:
        error: str = "boom"
        files: tuple[()] = ()

    out = generation.described(Failed(), tmp_path, {})
    assert (out["error"], out["error_kind"], out["cost_usd"], out["measurements"]) == (
        "boom",
        "failed",
        None,
        {},
    )

    @dataclass
    class Done:
        files: tuple[Media, ...]
        elapsed_s: float = 1.5
        cost_usd: float = float("nan")

    out = generation.described(
        Done((Media(tmp_path / "a" / "b.png"), Media(Path("/elsewhere/c.png")))), tmp_path, {}
    )
    assert [f["name"] for f in out["data"]["files"]] == ["a/b.png", "c.png"]
    assert out["cost_usd"] is None  # NaN is not a cost
    assert out["measurements"] == {"elapsed_s": 1.5}


def test_the_generate_call_that_raises_is_a_failed_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Raising:
        def generate(self, prompt: str, **kw: Any) -> Any:
            raise RuntimeError("server gone")

    spec = d.ExperimentSpec.model_validate(
        {
            "title": "t",
            "generate": {"kind": "generate", "client": "x:y", "prompt": "hi"},
            "factors": {"model": ["m"]},
        }
    )
    monkeypatch.setitem(generation._clients, ("x:y", "m"), Raising())
    folder = tmp_path / "E0001-t"
    where = generation.subjects.Where(tmp_path, folder, folder / "w")
    out = generation.run(spec, {"id": "c", "fields": {}, "files": {}}, {"model": "m"}, 0, where)
    assert out["error"] == "RuntimeError: server gone"


def test_case_overrides_and_judge_view() -> None:
    case = {
        "id": "c",
        "fields": {"scene": "shared", "angle": "low"},
        "files": {},
        "per_model": {"m": {"scene": "own"}},
    }
    asked = overrides.case_for(case, "m")
    assert asked["fields"] == {"scene": "own", "angle": "low"}
    assert overrides.judge_view(asked) == {"scene": "shared", "angle": "low"}
    assert overrides.case_for(case, "other") is case
    assert overrides.judge_view({**case, "judge_view": ["angle"]}) == {"angle": "low"}


@pytest.mark.parametrize(
    ("generate", "message"),
    [
        (
            {"kind": "prompt", "client": "a:b", "output": "take.wav"},
            'output of a prompt subject is "text" or "json"',
        ),
        ({"kind": "generate", "client": "a:b", "output": "../escape.png"}, "inside the sample's folder"),
        ({"kind": "generate"}, "needs `client`"),
        (
            {"kind": "python", "function": "a:b", "per_model": {"m": {"prompt": "x"}}},
            "applies to prompt and generate",
        ),
    ],
)
def test_definition_mistakes(tmp_path: Path, generate: dict[str, Any], message: str) -> None:
    lines = [
        'title = "t"',
        "[generate]",
        *(f"{k} = {json.dumps(v)}" for k, v in generate.items() if k != "per_model"),
    ]
    for model, table in generate.get("per_model", {}).items():
        lines += [f'[generate.per_model."{model}"]', *(f"{k} = {json.dumps(v)}" for k, v in table.items())]
    (tmp_path / "experiment.toml").write_text("\n".join(lines) + "\n")
    with pytest.raises(ConfigError, match=message):
        d.load(tmp_path)


def test_a_case_whose_per_model_is_not_a_table_is_refused() -> None:
    spec = d.ExperimentSpec.model_validate({"title": "t", "generate": {"kind": "python", "function": "a:b"}})
    case = {"id": "c", "fields": {}, "files": {}, "per_model": "m"}
    with pytest.raises(ConfigError, match="is a table of models"):
        overrides.check(spec, [case], [{}])


def test_the_summary_without_applicability_or_models() -> None:
    res = {
        "eid": "E0001",
        "title": "t",
        "question": "q",
        "best": None,
        "ranking": [],
        "setups": {},
        "factors": {},
        "baselines": {},
    }
    text = summary.summary(res)
    assert "could not do" not in text
    assert "Licenses" not in text
