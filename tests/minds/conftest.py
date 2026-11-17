"""minds 的测试夹具：真内容（content/）与 Mock 的模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.llm import LLM, make_llm

REPO = Path(__file__).resolve().parents[2]
KEY_ENVS = ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY")

_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def fresh() -> tuple[Defs, WorldState]:
    if "content" not in _CACHE:
        _CACHE["content"] = load(REPO / "content")
    defs, state = _CACHE["content"]
    return defs, state.model_copy(deep=True)


def open_bout(defs: Defs, state: WorldState) -> str:
    resolve.run_effects(state, defs, [{"open": ["bout_laugh"]}], source="situation")
    sid = next(s.id for s in state.situations.values() if s.kind == "bout_laugh")
    return sid


@pytest.fixture
def world() -> tuple[Defs, WorldState]:
    return fresh()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


@pytest.fixture
def llm(settings: Settings) -> LLM:
    return make_llm(settings, mock=True, run_dir=None)


def with_route(settings: Settings, **route: str) -> Settings:
    return settings.model_copy(update={"route": settings.route.model_copy(update=route)})
