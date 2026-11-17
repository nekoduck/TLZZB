"""parser 测试的夹具：真 content/、Mock 模型、几个常用的场面。全部离线。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong import parser
from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import effects, resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Fact, WorldState
from tianlong.llm import LLM, make_llm

REPO = Path(__file__).resolve().parents[2]
KEY_ENVS = ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY")
_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def fresh() -> tuple[Defs, WorldState]:
    if "content" not in _CACHE:
        _CACHE["content"] = load(REPO / "content")
    defs, state = _CACHE["content"]
    return defs, state.model_copy(deep=True)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    s = load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")
    s.route.classify = "jev_first"  # 这些测试验 J1 → C1 的路；改路由的测试各自另设
    return s


@pytest.fixture
def llm(settings: Settings, tmp_path: Path) -> LLM:
    return make_llm(settings, mock=True, run_dir=tmp_path)


class Scene:
    """一个可以往里打字的场面。"""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.defs, self.state = fresh()

    @property
    def s(self) -> WorldState:
        return self.state

    def fx(self, effs: list[dict]) -> None:
        effects.apply_effects(self.state, self.defs, effs, source="rule")

    def move_player(self, loc: str) -> None:
        self.fx([{"move": ["player", loc]}])

    def add_fact(self, fid: str, text: str, knowers: list[str]) -> None:
        # 测试布景：加一条事实（运行时只有内核会加事实）
        self.state.facts[fid] = Fact(id=fid, text=text, tags=["test"])
        self.fx([{"learn": [k, fid, 1.0, "saw"]} for k in knowers])

    async def parse(self, raw: str, **kw: Any) -> parser.ParseResult:
        return await parser.classify(self.llm, self.state, self.defs, raw, **kw)

    async def bind(self, raw: str) -> tuple[parser.ParseResult, parser.Bound]:
        r = await self.parse(raw)
        return r, parser.bind(self.state, self.defs, r)

    async def play(self, raw: str) -> tuple[parser.Bound, list[Any]]:
        _, b = await self.bind(raw)
        evs: list[Any] = []
        if b.kind == "action" and b.action is not None:
            evs = resolve.apply(self.state, self.defs, b.action, b.speech, source="player")
        return b, evs


@pytest.fixture
def scene(llm: LLM) -> Scene:
    return Scene(llm)


@pytest.fixture
def camp(llm: LLM) -> Scene:
    """方案 4.5 的场面：神农帮营地，司空玄中了貂毒，段公子被扣着。"""
    sc = Scene(llm)
    sc.move_player("shennong_camp")
    sc.fx([
        {"move": ["duan_yu", "shennong_camp"]},
        {"add_status": ["duan_yu", "captive", {"source": "sikong_xuan"}]},
        {"add_status": ["sikong_xuan", "poisoned_diao", {"ticks": 672}]},
    ])
    sc.add_fact("F_test_paths", "⟦player⟧熟悉⟦wuliang_mount⟧一带的山路。", ["player", "duan_yu"])
    return sc
