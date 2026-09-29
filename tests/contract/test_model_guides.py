"""The ModelGuides port (design change 0011 §5): the fake passes the contract checker, the checker rejects
broken shapes, and the hone_models:guides adapter over a stand-in hone_models module."""

import dataclasses
import sys
import types
from typing import Any

import pytest

from hone_select import ConfigError
from hone_select.adapters import hone_models as resolver
from hone_select.ports import ModelGuides
from hone_select.testing import FakeModelGuides, check_model_guides

WAN = {"kind": "video", "features": [{"name": "camera angle"}], "max_duration_s": 5, "installed": "yes"}


def test_the_fake_passes_the_contract_and_keeps_its_calls() -> None:
    fake = FakeModelGuides({"wan": WAN})
    check_model_guides(fake, known="wan")
    source: ModelGuides = fake
    assert source.guide("wan")["id"] == "wan"  # type: ignore[index]
    assert source.guide("other") is None
    assert fake.calls[-2:] == ["wan", "other"]


@pytest.mark.parametrize(
    "guide",
    [
        {"features": "camera angle"},
        {"features": [{"name": 3}]},
        {"installed": True},
        {"commercial_use": "yes"},
        {"max_duration_s": "600"},
        {"max_references": True},
        {"sizes": "1024x1024"},
        {"id": "another"},
    ],
)
def test_the_checker_rejects_broken_guides(guide: dict[str, Any]) -> None:
    class Broken:
        def guide(self, model_id: str) -> Any:
            return None if model_id != "x" else guide

    with pytest.raises(AssertionError):
        check_model_guides(Broken(), known="x")


def test_the_checker_wants_none_for_an_unknown_model() -> None:
    class Everything:
        def guide(self, model_id: str) -> Any:
            return {"id": model_id}

    with pytest.raises(AssertionError, match="unknown model must be None"):
        check_model_guides(Everything())


@dataclasses.dataclass
class Guide:
    id: str
    summary: str

    def as_text(self) -> str:
        return f"{self.id}: {self.summary}"


class Dumped:
    def model_dump(self, mode: str) -> dict[str, Any]:
        return {"id": "dumped", "mode": mode}


class Jsoned:
    def to_json(self) -> dict[str, Any]:
        return {"id": "jsoned", "install": "custom install"}


def _hone_models(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("hone_models")

    class UnknownModelError(Exception):
        pass

    answers: dict[str, Any] = {
        "ace": Guide("ace", "songs"),
        "mapped": {"id": "mapped", "installed": "no"},
        "dumped": Dumped(),
        "jsoned": Jsoned(),
    }

    def guide(model_id: str) -> Any:
        if model_id not in answers:
            raise UnknownModelError(f"unknown model {model_id!r}")
        return answers[model_id]

    module.ConfigError = UnknownModelError  # type: ignore[attr-defined]
    module.guide = guide  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hone_models", module)
    return module


def test_the_adapter_returns_the_json_form_with_text_and_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _hone_models(monkeypatch)
    source = resolver.guides()
    check_model_guides(source, known="ace")
    assert source.guide("ace") == {
        "id": "ace",
        "summary": "songs",
        "text": "ace: songs",
        "install": "hone-models models install ace",
    }
    assert source.guide("mapped") == {
        "id": "mapped",
        "installed": "no",
        "install": "hone-models models install mapped",
    }
    assert source.guide("dumped")["mode"] == "json"  # type: ignore[index]
    assert source.guide("jsoned")["install"] == "custom install"  # type: ignore[index]
    assert source.guide("nothing") is None


def test_the_adapter_needs_hone_models_with_guides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_models", None)
    with pytest.raises(ConfigError, match=r"install hone-select\[models\]"):
        resolver.guides()
    monkeypatch.setitem(sys.modules, "hone_models", types.ModuleType("hone_models"))
    with pytest.raises(ConfigError, match="update hone-models"):
        resolver.guides()
