"""叙述模块测试的夹具：真内容（content/）、Mock 的 llm、造好的玩家视图与假的 C3 流。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import actions, perception, resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.perception import PlayerView
from tianlong.kernel.state import Event, Speech, WorldState
from tianlong.llm import LLM, CallMeta, make_llm

REPO = Path(__file__).resolve().parents[2]
KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY')

_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def fresh_world() -> tuple[Defs, WorldState]:
    if 'content' not in _CACHE:
        _CACHE['content'] = load(REPO / 'content')
    defs, state = _CACHE['content']
    return defs, state.model_copy(deep=True)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / 'config.toml', env_file=tmp_path / 'none.env')


@pytest.fixture
def llm(settings: Settings, tmp_path: Path) -> LLM:
    return make_llm(settings, mock=True, run_dir=tmp_path)


@pytest.fixture
def world() -> tuple[Defs, WorldState]:
    return fresh_world()


def act(state: WorldState, defs: Defs, actor: str, verb: str, speech: Speech | None = None,
        **params: Any) -> list[Event]:
    a = actions.build(state, defs, actor, verb, params)
    assert a is not None, (actor, verb, params)
    return resolve.apply(state, defs, a, speech, source='player' if actor == state.player else 'mind')


def opening_scene(state: WorldState, defs: Defs) -> tuple[PlayerView, list[Event]]:
    """玩家四下打量；东家冷冷地叮嘱一句；玩家打量青衫公子。"""
    p = state.player
    evs: list[Event] = []
    evs += act(state, defs, p, 'look')
    evs += act(state, defs, 'ma_wude', 'wait',
               Speech(act='warn', to=[p], stance='cold', gist='叫你别多嘴'), ticks=1)
    evs += act(state, defs, p, 'examine', target='duan_yu')
    return perception.player_view(state, defs, evs), evs


@pytest.fixture
def scene(world: tuple[Defs, WorldState]) -> tuple[Defs, WorldState, PlayerView]:
    defs, state = world
    view, _ = opening_scene(state, defs)
    return defs, state, view


def fake_stream(pieces: Sequence[str], *, stop_reason: str = 'end_turn',
                log: dict[str, Any] | None = None, error: Exception | None = None) -> Callable[..., Any]:
    """代替 `llm.claude.stream_text` 的假流：按片吐出 pieces，记下是否被提前关掉。"""
    log = log if log is not None else {}
    log.setdefault('closed_early', False)
    log.setdefault('yielded', 0)

    async def stream_text(kind: str, system_blocks: Any, user_text: str, *, mock_text: Any = None,
                          escalated: bool = False, ttl: str | None = None,
                          meta_out: list[CallMeta] | None = None) -> Any:
        log['system'] = system_blocks
        log['user'] = user_text
        log['mock_text'] = mock_text
        meta = CallMeta(kind=kind, provider='fake', model='fake', stop_reason=stop_reason)
        try:
            if error is not None:
                raise error
            for p in pieces:
                log['yielded'] += 1
                yield p
        except GeneratorExit:
            meta.stopped_early = True
            log['closed_early'] = True
            raise
        finally:
            if meta_out is not None:
                meta_out.append(meta)

    return stream_text


def option(action: Any, tag: str) -> SimpleNamespace:
    return SimpleNamespace(action=action, tag=tag)
