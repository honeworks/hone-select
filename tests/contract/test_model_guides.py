"""The ModelGuides port (design change 0011 §5): the fake passes the contract checker, the checker rejects
broken shapes, and the hone_models:guides adapter over a stand-in hone_models module."""

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


class Guide:
    """Shaped like hone-models' ModelGuide: `as_dict()` and `as_text()`."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def as_dict(self) -> dict[str, Any]:
        return dict(self.data)

    def as_text(self) -> str:
        return f"{self.data['id']}: {self.data.get('summary')}"


def _hone_models(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("hone_models")
    errors = types.ModuleType("hone_models.errors")

    class ConfigError(Exception):
        pass

    answers: dict[str, Any] = {
        "ace": Guide({"id": "ace", "summary": "songs", "installed": "yes", "install": []}),
        "qwen": Guide({"id": "qwen", "installed": "no", "install": ["hf download a b", "mv x y"]}),
        "mapped": {"id": "mapped", "installed": "unknown"},
    }

    def guide(model_id: str) -> Any:
        if model_id not in answers:
            raise ConfigError(f"unknown model {model_id!r}")
        return answers[model_id]

    errors.ConfigError = ConfigError  # type: ignore[attr-defined]
    module.errors = errors  # type: ignore[attr-defined]
    module.guide = guide  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hone_models", module)
    monkeypatch.setitem(sys.modules, "hone_models.errors", errors)
    return module


def test_the_adapter_returns_the_json_form_with_text_and_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _hone_models(monkeypatch)
    source = resolver.guides()
    check_model_guides(source, known="ace")
    assert source.guide("ace") == {
        "id": "ace",
        "summary": "songs",
        "installed": "yes",
        "install": "hone-models models install ace",
        "install_commands": [],
        "text": "ace: songs",
    }
    qwen = source.guide("qwen")
    assert qwen is not None
    assert qwen["install"] == "hone-models models install qwen"
    assert qwen["install_commands"] == ["hf download a b", "mv x y"]
    assert source.guide("mapped") == {
        "id": "mapped",
        "installed": "unknown",
        "install": "hone-models models install mapped",
    }
    assert source.guide("nothing") is None


def test_the_adapter_needs_hone_models_with_guides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hone_models", None)
    with pytest.raises(ConfigError, match=r"install hone-select\[models\]"):
        resolver.guides()
    monkeypatch.setitem(sys.modules, "hone_models", types.ModuleType("hone_models"))
    with pytest.raises(ConfigError, match="update hone-models"):
        resolver.guides()
    with_guide = types.ModuleType("hone_models")
    with_guide.guide = lambda model_id: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hone_models", with_guide)
    monkeypatch.setitem(sys.modules, "hone_models.errors", None)  # a hone-models without errors
    with pytest.raises(ConfigError, match="update hone-models"):
        resolver.guides()
    monkeypatch.setitem(sys.modules, "hone_models.errors", types.ModuleType("hone_models.errors"))
    with pytest.raises(ConfigError, match="update hone-models"):  # errors without ConfigError
        resolver.guides()
