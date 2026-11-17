"""game 的测试夹具：小世界（tests/fixtures/mini）或真实的 content/，Mock 的模型，日志与存档都写进临时目录。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings, load_settings
from tianlong.game import Game
from tianlong.kernel import resolve
from tianlong.kernel.state import Ctx
from tianlong.ui.events import Break, Ended, Header, Notice, Options, Text, UIEvent
from tianlong.ui.text import plain_text

REPO = Path(__file__).resolve().parents[2]
MINI = REPO / "tests" / "fixtures" / "mini"
CONTENT = REPO / "content"


def make_settings(tmp_path: Path, content: Path = MINI) -> Settings:
    s = load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")
    s.paths.content = str(content)
    s.paths.runs = str(tmp_path / "runs")
    s.paths.saves = str(tmp_path / "saves")
    return s


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """小世界：3 个地点、4 个人。"""
    return make_settings(tmp_path, MINI)


@pytest.fixture
def real_settings(tmp_path: Path) -> Settings:
    """真实的 content/。"""
    return make_settings(tmp_path, CONTENT)


class Session:
    """驱动一局游戏，记下每一步的界面事件。"""

    def __init__(self, game: Game) -> None:
        self.game = game
        self.steps: list[tuple[str, list[UIEvent]]] = []

    @property
    def state(self) -> Any:
        return self.game.state

    async def start(self) -> list[UIEvent]:
        evs = [ev async for ev in self.game.start()]
        self.steps.append(("", evs))
        return evs

    async def send(self, raw: str) -> list[UIEvent]:
        evs = [ev async for ev in self.game.turn(raw)]
        self.steps.append((raw, evs))
        return evs

    async def play(self, *inputs: str) -> list[UIEvent]:
        out: list[UIEvent] = []
        for raw in inputs:
            out = await self.send(raw)
        return out

    def fx(self, effs: list[dict], self_id: str | None = None) -> list[Any]:
        """测试布景：经内核的效果改世界。"""
        st = self.game.state
        return resolve.run_effects(st, self.game.defs, effs, Ctx(self_id=self_id or st.player), source="rule")

    async def close(self) -> None:
        await self.game.aclose()


async def new_session(settings: Settings, *, seed: int = 1, debug: bool = False, **kw: Any) -> Session:
    kw.setdefault("run_id", "run")
    kw.setdefault("player_name", "阿青")
    game = await Game.new(settings, seed=seed, mock=True, debug=debug, **kw)
    s = Session(game)
    await s.start()
    return s


def kinds(evs: list[UIEvent]) -> list[str]:
    return [type(e).__name__ for e in evs]


def text_of(evs: list[UIEvent]) -> str:
    return "".join(e.piece for e in evs if isinstance(e, Text))


def notices(evs: list[UIEvent]) -> list[str]:
    return [e.text for e in evs if isinstance(e, Notice)]


def options_of(evs: list[UIEvent]) -> list[str] | None:
    for e in evs:
        if isinstance(e, Options):
            return list(e.items)
    return None


def header_of(evs: list[UIEvent]) -> Header | None:
    return next((e for e in evs if isinstance(e, Header)), None)


def ended_of(evs: list[UIEvent]) -> Ended | None:
    return next((e for e in evs if isinstance(e, Ended)), None)


def assert_full_turn(evs: list[UIEvent], n_options: int = 3) -> None:
    """一回合的样子（方案 2.1）：抬头、叙述、一段结束、选项。"""
    ks = kinds(evs)
    assert ks[0] == "Header", ks
    assert "Text" in ks and "Break" in ks and ks.index("Text") < ks.index("Break"), ks
    opts = options_of(evs)
    assert opts is not None and len(opts) == n_options, (ks, opts)
    assert all(o.strip() for o in opts) and len(set(opts)) == len(opts), opts
    assert text_of(evs).strip()


__all__ = [
    "Break",
    "CONTENT",
    "MINI",
    "REPO",
    "Session",
    "assert_full_turn",
    "ended_of",
    "header_of",
    "kinds",
    "make_settings",
    "new_session",
    "notices",
    "options_of",
    "plain_text",
    "text_of",
]
