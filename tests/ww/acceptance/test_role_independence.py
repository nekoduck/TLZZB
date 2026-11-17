"""身份无关（端到端）：同一座位、同一人设、同一串公开事件、同一份 PublicPlan，不论真实身份是狼人、
预言家还是猎人，NpcAgent 窄化出来的简报与写台词的提示逐字相同（私有标注、念头、动机都不同也一样）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import ActKind, GameConfig, Role, Tone
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.llm.speak import render_brief
from werewolf.mind.types import Pace, PlannedAct, Quote, ReasonCode, SpeechPlan
from werewolf.server.npc import NpcAgent
from werewolf.testing import DEFAULT_ROLES, Scenario, act
from werewolf.voice.personas import load_personas

from .fakes import offline_cfg

SEAT = 2


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    return offline_cfg(monkeypatch, tmp_path)


def _scenario(swap_with: int | None) -> Scenario:
    """duel_scenario 的前半段（公开动作相同）；2 号的身份按 swap_with 与别人对调。"""
    roles = dict(DEFAULT_ROLES)
    if swap_with is not None:
        roles[SEAT], roles[swap_with] = roles[swap_with], roles[SEAT]
    sc = Scenario(roles=roles)
    seer = next(s for s, r in roles.items() if r == Role.SEER)
    sc.night(1, kill=4, check=(seer, 7, 'good'))
    sc.dawn([4])
    sc.start_speeches([5, 6, 7, 8, 9, 1, 2, 3])
    sc.speech(5, '我是平民，先听听。', [act('claim_role', role=Role.VILLAGER, weight=1)])
    sc.speech(6, '4号夜里倒了，先不多说。', [])
    sc.speech(7, '过。', [act('pass')])
    sc.speech(8, '7号太划水了，我觉得7号有问题。', [act('accuse', 7), act('vote_intent', 7, weight=1)],
              tone=Tone.FIRM)
    sc.speech(9, '8号踩7号踩得有点急。', [act('question', 8, weight=1)])
    sc.speech(1, '2号你怎么看？', [act('question', 2, weight=1)])
    return sc


def _plan(role: Role) -> SpeechPlan:
    """同一份公开内容；私有标注（motive、truthful、thought、reasons、utility）随身份不同。"""
    wolf = role == Role.WEREWOLF
    return SpeechPlan(
        id='push:8', seat=SEAT, day=1, kind='day', variant='push', label='踩 8 号',
        reasons=['队友在 8 号'] if wolf else ['8 号踩得太急'], utility=1.3 if wolf else 0.4,
        acts=[PlannedAct(kind=ActKind.ACCUSE, target=8, weight=1, reason=ReasonCode.TOO_EAGER,
                         motive=ReasonCode.TEAM if wolf else None, truthful=not wolf),
              PlannedAct(kind=ActKind.VOTE_INTENT, target=8, weight=1, reason=ReasonCode.TOO_EAGER,
                         motive=ReasonCode.TEAM if wolf else None, truthful=not wolf)],
        tone=Tone.FIRM, length='medium', addressed=[1],
        thought='倒钩一下 8 号，队友会理解的' if wolf else '8 号像狼')


@pytest.mark.parametrize('variant', [(None, Role.WEREWOLF), (9, Role.HUNTER), (3, Role.SEER), (1, Role.VILLAGER)])
def test_brief_and_prompt_identical_across_roles(cfg: tuple[Settings, WerewolfSettings],
                                                 variant: tuple[int | None, Role]) -> None:
    settings, ww = cfg
    gw = Gateway(settings, ww, offline=True)
    persona = load_personas()[3]
    out: list[tuple[str, str]] = []
    for swap, role in [(None, Role.WEREWOLF), variant]:
        sc = _scenario(swap)
        assert sc.roles[SEAT] == role
        agent = NpcAgent(SEAT, role, persona, Rng(5, 'npc'), gw, ww, GameConfig(human_seat=1))
        view = sc.view(SEAT)
        for ev in sc.events:
            if sc.visible(ev, SEAT):
                agent.observe(ev, view)
        req = sc.request_speak(SEAT, index=6, total=8)
        brief = agent._brief(_plan(role), req, view, Pace(), [Quote(seat=1, text='2号你怎么看？')])
        out.append((brief.model_dump_json(), render_brief(brief)))
    assert out[0] == out[1]
