"""NpcAgent（server/npc.py）：Jev 只在该问的时候问、只拿状态文本；写台词只拿简报；
整步超时交「过」；反思每夜恰好一次、狼人用公开状态文本；独白只进复盘；会话层默认用它。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings
from tianlong.llm.errors import LLMError
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import ActKind, GameConfig, RequestKind, Role
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.llm.render import decision_state
from werewolf.llm.speak import render_brief
from werewolf.mind.types import Pace, Quote
from werewolf.server import npc as npc_mod
from werewolf.server.npc import NpcAgent, make_npc, option_text
from werewolf.server.session import Session
from werewolf.testing import duel_scenario
from werewolf.voice.digest import public_digest
from werewolf.voice.personas import load_personas

from ..acceptance.fakes import Call, offline_cfg, recording_gateway

MONO = '独白里的金丝雀'


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    return offline_cfg(monkeypatch, tmp_path, fake_keys=True)


def _agent(gw: Gateway, ww: WerewolfSettings, seat: int, role: Role, persona_i: int = 0) -> NpcAgent:
    return NpcAgent(seat, role, load_personas()[persona_i], Rng(11, f'npc:{seat}'), gw, ww, GameConfig(human_seat=1))


def _feed(agent: NpcAgent, sc: Any) -> None:
    view = sc.view(agent.seat)
    for ev in sc.events:
        if sc.visible(ev, agent.seat):
            agent.observe(ev, view)


def test_option_text() -> None:
    assert option_text('投 3 号', ['他踩了金水', '别的']) == '投 3 号：他踩了金水'
    assert option_text('过', []) == '过'


async def test_offline_act_uses_rules_only(cfg: tuple[Settings, WerewolfSettings]) -> None:
    settings, ww = cfg
    gw = Gateway(settings, ww, offline=True)
    sc = duel_scenario()
    a = _agent(gw, ww, 7, Role.VILLAGER)
    _feed(a, sc)
    req = sc.request_vote(7)
    action, dec = await a.act(req, sc.view(7))
    assert dec.source == 'rules' and dec.jev_probs is None
    assert action.option_id == dec.option_id and action.seat == 7 and action.request_id == req.id
    assert a.decisions == [dec]


async def test_jev_asked_with_state_text_and_option_descriptions(cfg: tuple[Settings, WerewolfSettings]) -> None:
    gw, log = recording_gateway(cfg)
    _, ww = cfg
    sc = duel_scenario()
    a = _agent(gw, ww, 7, Role.VILLAGER)
    _feed(a, sc)
    req = sc.request_vote(7)
    view = sc.view(7)
    cands = a.mind.options(req, view)
    assert len(cands) >= 2 and cands[0].utility - cands[1].utility < ww.mind.decisive_gap
    state = decision_state(view, a.mind.snapshot(req), a.persona, public_digest(view.public()))
    _, dec = await a.act(req, view)
    jev = [c for c in log if c.provider == 'jev']
    assert len(jev) == 1 and jev[0].kind == 'J2_act'
    assert jev[0].text.startswith(state)
    for o in cands:
        assert option_text(o.label, o.reasons) in jev[0].text
    assert dec.source == 'jev' and dec.jev_probs is not None
    assert set(dec.jev_probs) == {o.option_id for o in cands}


async def test_decisive_leader_skips_jev(cfg: tuple[Settings, WerewolfSettings]) -> None:
    """3 号（真预言家）已验 2 号是狼：投 2 号建立在硬知识上，领先 ≥ decisive_gap，不问 Jev。"""
    gw, log = recording_gateway(cfg)
    _, ww = cfg
    sc = duel_scenario()
    a = _agent(gw, ww, 3, Role.SEER)
    _feed(a, sc)
    req = sc.request_vote(3)
    cands = a.mind.options(req, sc.view(3))
    assert cands[0].option_id == 'vote:2'
    assert len(cands) == 1 or cands[0].utility - cands[1].utility >= ww.mind.decisive_gap
    _, dec = await a.act(req, sc.view(3))
    assert dec.option_id == 'vote:2' and dec.source == 'rules'
    assert not [c for c in log if c.provider == 'jev']


async def test_wolf_proposal_carries_note_and_first_night_stance(cfg: tuple[Settings, WerewolfSettings]) -> None:
    settings, ww = cfg
    gw = Gateway(settings, ww, offline=True)
    from werewolf.testing import Scenario
    sc = Scenario()
    sc.day = 1
    a = _agent(gw, ww, 2, Role.WEREWOLF)
    _feed(a, sc)
    req = sc.request_kill(2)
    action, dec = await a.act(req, sc.view(2))
    assert action.stance is not None and action.stance == dec.stance
    assert action.note == (dec.note[:80] if dec.note else None)
    assert action.option_id not in ('kill:5', 'kill:8')  # 不刀队友


async def test_speech_prompt_is_exactly_the_rendered_brief(cfg: tuple[Settings, WerewolfSettings]) -> None:
    """写台词的提示 = render_brief(简报)；简报来自窄化之后的公开族类型。acts 来自计划，不来自文字。"""
    def claude(kind: str, system: list[str], user: str) -> str:
        return '我先过一下，听听后面的。'

    gw, log = recording_gateway(cfg, claude_fn=claude)
    _, ww = cfg
    sc = duel_scenario()
    a = _agent(gw, ww, 2, Role.WEREWOLF)
    _feed(a, sc)
    req = sc.request_speak(2, index=3, total=6)
    heard = [Quote(seat=1, text='我觉得2号很可疑')]
    action, plan, dec, result = await a.speak(req, sc.view(2), Pace(), heard)
    assert action.speech is not None
    assert action.speech.acts == plan.public_acts()
    assert action.speech.text == result.text and action.speech.source == 'ai'
    assert a.last_brief is not None and a.last_brief.heard == heard
    prompts = [c for c in log if c.provider == 'claude' and c.kind == 'W_speech']
    assert prompts and prompts[0].user == render_brief(a.last_brief)
    assert dec.option_id == plan.id
    # 私有的东西不在提示里
    for c in prompts:
        assert plan.thought not in c.text if plan.thought else True
        assert '队友' not in c.user and '狼队' not in c.user


async def test_speak_timeout_submits_pass(cfg: tuple[Settings, WerewolfSettings], monkeypatch: pytest.MonkeyPatch) -> None:
    settings, ww = cfg
    ww = ww.model_copy(update={'limits': ww.limits.model_copy(update={'speech_timeout_s': 0.05})})
    gw = Gateway(settings, ww, offline=True)

    async def slow(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(npc_mod, 'write_speech', slow)
    sc = duel_scenario()
    a = _agent(gw, ww, 7, Role.VILLAGER)
    _feed(a, sc)
    action, plan, dec, result = await a.speak(sc.request_speak(7), sc.view(7), Pace())
    assert action.speech is not None and [x.kind for x in action.speech.acts] == [ActKind.PASS]
    assert plan.variant == 'pass' and dec.option_id == plan.id
    assert result.tier == 'template' and result.text and a.timeouts == 1


async def test_reflection_once_per_night_and_wolf_uses_public_state(cfg: tuple[Settings, WerewolfSettings]) -> None:
    gw, log = recording_gateway(cfg, claude_fn=lambda k, s, u: MONO if k == 'W_monologue' else '')
    _, ww = cfg
    sc = duel_scenario()
    wolf = _agent(gw, ww, 2, Role.WEREWOLF)
    good = _agent(gw, ww, 7, Role.VILLAGER, 1)
    _feed(wolf, sc)
    _feed(good, sc)
    calls: list[str] = []
    for ag in (wolf, good):
        orig = ag.mind.apply_reflection

        def counted(r: Any, orig: Any = orig, seat: int = ag.seat) -> None:
            calls.append(f'{seat}:{r.basis}:{r.source}')
            orig(r)

        ag.mind.apply_reflection = counted  # type: ignore[method-assign]
        await ag.reflect(sc.view(ag.seat))
        await ag.reflect(sc.view(ag.seat))  # 同一夜第二次：不再落地
        await ag.aclose()
    assert calls == ['2:public:jev', '7:private:jev']
    refl = [c for c in log if c.kind == 'J3_reflect']
    assert len(refl) == 2
    wolf_state = refl[0].text.split('\n{')[0]
    assert '狼人' not in wolf_state and '队友' not in wolf_state and '5号和8号' not in wolf_state
    assert '【我是谁】' in refl[1].text
    # 独白只进复盘，不进之后的任何提示
    assert any(MONO in t.text for t in wolf.review().thoughts if t.kind == 'monologue')
    n0 = len(log)
    await good.act(sc.request_vote(7), sc.view(7))
    await good.speak(sc.request_speak(7), sc.view(7), Pace())
    assert all(MONO not in c.text for c in log[n0:])
    assert all(MONO not in (b.model_dump_json()) for b in good.briefs)


async def test_reflection_falls_back_to_rules_when_jev_fails(cfg: tuple[Settings, WerewolfSettings]) -> None:
    def boom(*a: Any) -> dict[str, Any]:
        raise LLMError('down')

    gw, _ = recording_gateway(cfg, jev_fn=boom)
    _, ww = cfg
    sc = duel_scenario()
    a = _agent(gw, ww, 7, Role.VILLAGER)
    _feed(a, sc)
    await a.reflect(sc.view(7))
    assert [r.source for _, r in a.reflections] == ['rules']
    _, dec = await a.act(sc.request_vote(7), sc.view(7))
    assert dec.source == 'rules'


async def test_session_default_factory_is_make_npc(cfg: tuple[Settings, WerewolfSettings]) -> None:
    settings, ww = cfg
    gw = Gateway(settings, ww, offline=True)
    s = Session(gw, ww, human_seat=3, human_role=None, seed=5, speed='instant', send=None, run_dir=None,
                autopilot=True)
    assert all(isinstance(p, NpcAgent) for p in s.players.values())
    res = await asyncio.wait_for(s.run(), 30)
    assert res.winner is not None or res.reason == 'max_days'
    assert s.review is not None and all(r.strategy for r in s.review.seats)
    days = [d for r in s.review.seats for d in r.days]
    assert days, 'per-day snapshots reach the review'
    assert callable(make_npc)


def test_calls_dataclass_is_exported() -> None:
    assert Call('jev', 'J2_act', 'x').provider == 'jev'
    assert RequestKind.VOTE in npc_mod.QUESTIONS
