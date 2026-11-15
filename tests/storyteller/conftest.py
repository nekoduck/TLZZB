"""讲述者测试的夹具：真内容（content/），Mock 的模型层。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, Event, WorldState
from tianlong.llm import LLM, make_llm

REPO = Path(__file__).resolve().parents[2]
KEY_ENVS = ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY")

_CACHE: dict[str, tuple[Defs, WorldState]] = {}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


@pytest.fixture
def llm(settings: Settings, tmp_path: Path) -> LLM:
    return make_llm(settings, mock=True, run_dir=tmp_path)


def calls(llm: LLM, kind: str | None = None) -> list[dict]:
    """读 Mock 录下的调用（calls.jsonl）。"""
    path = llm.recorder.path
    if path is None or not path.exists():
        rows = list(llm.recorder.records)
    else:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if kind is None or r.get("kind") == kind]


class World:
    def __init__(self) -> None:
        if "content" not in _CACHE:
            _CACHE["content"] = load(REPO / "content")
        defs, state = _CACHE["content"]
        self.defs = defs
        self.s: WorldState = state.model_copy(deep=True)

    @property
    def p(self):
        return self.s.chars[self.s.player]

    def fx(self, effs: list[dict], ctx: Ctx | None = None, source: str = "rule") -> list[Event]:
        before = list(self.s.violations)
        evs = resolve.run_effects(self.s, self.defs, effs, ctx, source=source)
        assert self.s.violations == before, self.s.violations[len(before):]
        return evs

    def open_bout(self) -> None:
        self.fx([{"open": ["bout_laugh"]}])
        assert any(s.kind == "bout_laugh" for s in self.s.situations.values())


@pytest.fixture
def w() -> World:
    return World()
