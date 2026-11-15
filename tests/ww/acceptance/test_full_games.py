"""整局（离线）：会话层 + NpcAgent，真人用脚本客户端（发言先预览、去掉一条再确认），五种身份各打几局：
都能打完，线上流体检（wire_check）零违例、seq 连续、协议合法；AI 的台词都过了核对、言语行为与计划一致；
托管（autopilot）的局也能打完，复盘里每个 AI 都有打法与按天的心境。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings
from werewolf.kernel import invariants as kinv
from werewolf.kernel.types import EventType, Role
from werewolf.llm.check import check_rules
from werewolf.llm.config import WerewolfSettings
from werewolf.server.npc import NpcAgent

from ..server.helpers import ScriptedClient, assert_clean
from .fakes import offline_cfg
from .harness import run_game


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    return offline_cfg(monkeypatch, tmp_path)


@pytest.mark.parametrize('seed', [1, 2])
@pytest.mark.parametrize('role', [Role.WEREWOLF, Role.SEER, Role.WITCH, Role.HUNTER, Role.VILLAGER])
async def test_human_role_full_game(cfg: tuple[Settings, WerewolfSettings], role: Role, seed: int) -> None:
    client = ScriptedClient(seed)
    s, _ = await run_game(cfg, seed=seed, role=role, client=client)  # type: ignore[arg-type]
    assert s.game.over and s.result is not None
    assert s.human_role == role
    assert_clean(s, client.msgs)
    assert kinv.check(s.game) == []
    agents = [p for p in s.players.values() if isinstance(p, NpcAgent)]
    assert len(agents) == 8
    for p in agents:
        for _plan, brief, res in zip(p.plans, p.briefs, p.results, strict=True):
            assert check_rules(res.text, brief) == []
            assert res.tier == 'template'
    by_seat = {p.seat: p for p in agents}
    for ev in s.game.events('judge'):
        if ev.type == EventType.SPEECH and ev.actor in by_seat:
            assert ev.data['speech']['source'] == 'ai'
    assert s.review is not None
    ai_rows = [r for r in s.review.seats if not r.is_human]
    assert all(r.strategy for r in ai_rows) and any(r.days for r in ai_rows)


async def test_autopilot_games_with_mind_checks(cfg: tuple[Settings, WerewolfSettings]) -> None:
    """托管局：每个 AI 每一步都跑心智不变量（NpcAgent.check_mind），零违例。"""
    from werewolf.llm.gateway import Gateway
    from werewolf.server.session import Session

    from .harness import probe_factory

    settings, ww = cfg
    for seed in range(3):
        gw = Gateway(settings, ww, offline=True)
        s = Session(gw, ww, human_seat=None, human_role=None, seed=seed, speed='instant', send=None, run_dir=None,
                    autopilot=True, player_factory=probe_factory(gw, ww, None, check_mind=True))
        await s.run()
        assert s.game.over
        viol = [v for p in s.players.values() if isinstance(p, NpcAgent) for v in p.violations]
        assert viol == []


@pytest.mark.parametrize('error', [RuntimeError, ConnectionError, KeyError])
async def test_model_clients_raising_never_stop_the_game(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                         error: type[Exception]) -> None:
    """模型客户端每次都抛意外的错（像被 aclose 之后）：决定走规则、台词退档或交「过」、解析只留原话，一局照样打完。"""
    from .fakes import recording_gateway

    cfg = offline_cfg(monkeypatch, tmp_path, fake_keys=True)

    def jev(*a: object) -> dict:
        raise error('jev client closed')

    def claude(*a: object) -> str:
        raise error('claude client closed')

    gw, log = recording_gateway(cfg, jev_fn=jev, claude_fn=claude)
    client = ScriptedClient(3)
    s, _ = await run_game(cfg, seed=3, seat=4, role=Role.VILLAGER, client=client, gateway=gw)  # type: ignore[arg-type]
    assert s.game.over and s.review is not None
    assert log, '确实调过模型'
    assert_clean(s, client.msgs)
