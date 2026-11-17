"""复盘钩子、默认玩家工厂、取消、wire_check 能抓到问题、反思先于夜间请求。"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import Event, EventType, Request, Role, Seat, View
from werewolf.mind.types import MindSnapshot, Persona
from werewolf.protocol import EventMsg, GameStarted, RequestMsg, Review, ReviewThought
from werewolf.server.players import Player, RandomPlayer, ReviewData
from werewolf.server.session import Session
from werewolf.server.wirecheck import wire_check

from .helpers import ScriptedClient, assert_clean, gateway, make_session, play


class Tracking(RandomPlayer):
    """记录调用顺序，带 snapshot 与 review 钩子。"""

    log: list[tuple[str, Seat, int]] = []

    def __init__(self, seat: Seat, role: Role, persona: Persona, rng: Rng) -> None:
        super().__init__(seat, role, persona, rng)
        self.day = 0

    def observe(self, event: Event, view: View):
        if event.type == EventType.DAWN:
            self.log.append(('dawn', self.seat, event.day))
        self.day = event.day
        return super().observe(event, view)

    async def act(self, request: Request, view: View):
        if request.kind in ('wolf_propose', 'seer_check', 'witch_act'):
            self.log.append(('night_req', self.seat, request.day))
        return await super().act(request, view)

    async def reflect(self, view: View) -> None:
        await asyncio.sleep(0.01)
        self.log.append(('reflected', self.seat, view.day))

    def snapshot(self) -> MindSnapshot:
        return MindSnapshot(seat=self.seat, day=self.day, role=self.role, suspicion={1: 0.2, 2: 0.7}, mood=61.0,
                            state='steady')

    def review(self) -> ReviewData:
        return ReviewData(strategy='deep', thoughts=[ReviewThought(day=1, phase='night_wolf', kind='monologue',
                                                                   text='今晚好安静')])


async def test_review_hooks_and_reflection_order(cfg):
    Tracking.log = []
    _, ww = cfg
    c = ScriptedClient(2)
    s = Session(gateway(cfg), ww, human_seat=5, human_role=Role.VILLAGER, seed=2, speed='instant', send=c.send,
                run_dir=None, player_factory=lambda seat, role, p, rng: Tracking(seat, role, p, rng))
    c.session = s
    await s.run()
    await c.drain()
    assert_clean(s, c.msgs)
    review = next(m for m in c.msgs if isinstance(m, Review))
    ai = [x for x in review.seats if not x.is_human]
    assert all(x.strategy == 'deep' for x in ai)
    assert all(any(t.kind == 'monologue' for t in x.thoughts) for x in ai)
    assert all(x.days and x.days[0].mood == 61.0 and x.days[0].suspicion for x in ai)
    human = next(x for x in review.seats if x.is_human)
    assert human.thoughts == [] and human.days == []
    # 每个座位每夜：反思先于它的第一个夜间请求，也先于 dawn 被观察
    log = Tracking.log
    reflected = [(seat, day) for k, seat, day in log if k == 'reflected']
    assert reflected and len(reflected) == len(set(reflected)), '每座位每夜恰好一次'
    for i, (k, seat, day) in enumerate(log):
        if k == 'night_req':
            assert ('reflected', seat, day) in log[:i], (seat, day)
        if k == 'dawn' and (seat, day) in reflected:
            assert ('reflected', seat, day) in log[:i], (seat, day)


async def test_default_factory_falls_back_to_random(cfg):
    if importlib.util.find_spec('werewolf.server.npc') is not None:
        pytest.skip('npc 已接入')
    _, ww = cfg
    s = Session(gateway(cfg), ww, human_seat=1, human_role=None, seed=0, speed='instant', send=None, run_dir=None)
    assert all(isinstance(p, RandomPlayer) for p in s.players.values())
    assert set(s.players) == set(range(2, 10))
    assert all(isinstance(p, Player) for p in s.players.values())


async def test_role_and_seat_preferences(cfg):
    for role in Role:
        for seat in (1, 9):
            s = make_session(cfg, seed=seat * 7, role=role, seat=seat, headless=True)
            assert s.human_seat == seat and s.game.role_of(seat) == role
            assert sorted(r.value for r in (s.game.role_of(i) for i in range(1, 10))) == \
                sorted(r.value for r in s.game.config.roles)
            assert len({p.name for p in s.personas.values()}) == 8


async def test_cancellation_is_clean(cfg):
    c = ScriptedClient(1)
    s = make_session(cfg, seed=1, role=Role.VILLAGER, client=c, speed='normal', time_scale=1.0)
    task = asyncio.ensure_future(s.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert all(t.done() for t in s._tasks)
    assert all(t.done() for t in s._reflect.values())


async def test_wire_check_catches_problems(cfg):
    s, c = await play(cfg, seed=3, role=Role.VILLAGER)
    msgs = list(c.msgs)
    assert wire_check(msgs, s.game, s.human_seat) == []
    i = next(i for i, m in enumerate(msgs) if isinstance(m, EventMsg) and m.seq == 5)
    # 跳号
    bad = msgs[:i] + msgs[i + 1:]
    assert any('seq' in p for p in wire_check(bad, s.game, s.human_seat))
    # 不可见的事件
    hidden = next(e for e in s.game.events('judge') if not s.game.visible(e, s.human_seat))
    leak = EventMsg(seq=5, ev=hidden.model_copy(update={'id': 5}))
    assert wire_check(msgs[:i] + [leak] + msgs[i + 1:], s.game, s.human_seat)
    # 内核请求号
    req = RequestMsg(id='r12', kind='vote')
    assert any('内核' in p for p in wire_check([*msgs[:i], req], s.game, s.human_seat))
    # 别人的身份
    other = next(x for x in range(1, 10) if x != s.human_seat and s.game.role_of(x) != Role.VILLAGER)
    g = msgs[0]
    assert isinstance(g, GameStarted)
    bad_g = g.model_copy(update={'you': other, 'your_role': s.game.role_of(other)})
    assert any('身份' in p for p in wire_check([bad_g], s.game, s.human_seat))


class Listener(RandomPlayer):
    heard_log: list[tuple[int, str, int, list]] = []

    async def speak(self, request, view, pace, heard=()):
        self.heard_log.append((request.day, request.speech_kind, request.context.get('index', 0), list(heard)))
        return await super().speak(request, view, pace, heard)


async def test_human_quote_reaches_later_speakers_only(cfg):
    Listener.heard_log = []
    _, ww = cfg
    c = ScriptedClient(7, preview=False)
    c.speech_text = lambda: '我觉得3号很可疑，今天出3号'  # type: ignore[method-assign]
    s = Session(gateway(cfg), ww, human_seat=4, human_role=Role.VILLAGER, seed=7, speed='instant', send=c.send,
                run_dir=None, player_factory=lambda seat, role, p, rng: Listener(seat, role, p, rng))
    c.session = s
    await s.run()
    await c.drain()
    human_turns = [(e.day, e.data['kind']) for e in s.game.events('judge')
                   if e.type == EventType.SPEECH and e.actor == 4]
    assert human_turns
    with_quote = [r for r in Listener.heard_log if r[3]]
    assert with_quote
    for day, kind, _, heard in with_quote:
        assert (day, kind) in human_turns
        assert all(q.seat == 4 and len(q.text) <= 80 for q in heard)
        assert heard[0].text.startswith('我觉得3号')


async def test_cancel_stops_npc_monologues(cfg, monkeypatch):
    """new_game 取消旧局时，NpcAgent 另起的独白任务也被取消并等完，不漏任务。"""
    import werewolf.server.npc as npc_mod
    from werewolf.kernel.types import GameConfig
    from werewolf.llm.gateway import Gateway
    from werewolf.server.npc import NpcAgent, make_npc

    started: list[int] = []
    cancelled: list[int] = []

    async def endless(gw, state, persona):
        started.append(1)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
    monkeypatch.setattr(npc_mod.llm_reflect, 'monologue', endless)
    real_route = Gateway.route
    monkeypatch.setattr(Gateway, 'route', lambda self, cat: 'claude' if cat == 'monologue' else real_route(self, cat))

    settings, ww = cfg
    gw = gateway(cfg)
    npcs: list[NpcAgent] = []

    s_cfg = GameConfig(human_seat=5)

    def factory(seat, role, p, rng):
        npc = make_npc(seat, role, p, rng, gw, ww, s_cfg)
        npcs.append(npc)
        return npc

    c = ScriptedClient(3)
    s = Session(gw, ww, human_seat=5, human_role=Role.VILLAGER, seed=3, speed='instant', send=c.send, run_dir=None,
                player_factory=factory)
    c.session = s
    task = asyncio.ensure_future(s.run())
    for _ in range(2000):
        await asyncio.sleep(0.005)
        if started:
            break
    assert started, '独白应该已经起了'
    sides = [t for n in npcs for t in n._side]
    assert sides
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(t.done() for t in sides), '旧局的独白任务还在跑'
    assert cancelled
