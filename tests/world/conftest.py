"""world/ 的测试夹具：真实的 content/，Mock 的模型（不联网）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, WorldState
from tianlong.llm import LLM, make_llm
from tianlong.world import clock, situations

REPO = Path(__file__).resolve().parents[2]
_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def fresh(seed: int = 0) -> tuple[Defs, WorldState]:
    if "content" not in _CACHE:
        _CACHE["content"] = load(REPO / "content")
    defs, state = _CACHE["content"]
    st = state.model_copy(deep=True)
    st.seed = seed
    return defs, st


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


@pytest.fixture
def llm(settings: Settings) -> LLM:
    return make_llm(settings, mock=True, run_dir=None)  # 调用只录在内存里


@pytest.fixture
def world() -> tuple[Defs, WorldState]:
    return fresh()


def fx(state: WorldState, defs: Defs, effs: list[dict], self_id: str | None = None) -> list[Any]:
    return resolve.run_effects(state, defs, effs, Ctx(self_id=self_id), source="rule")


def player_off(state: WorldState, defs: Defs) -> None:
    fx(state, defs, [{"leave": state.player}])


async def hours(llm: Any, state: WorldState, defs: Defs, n: int, **kw: Any) -> list[Any]:
    """推进 n 个时辰：advance_time 8 刻，再 world.tick，再 end_of_turn。"""
    out: list[Any] = []
    for _ in range(n):
        out += timeflow.advance_time(state, defs, 8)
        out += await clock.tick(llm, state, defs, **kw)
        situations.end_of_turn(state, defs)
    return out
