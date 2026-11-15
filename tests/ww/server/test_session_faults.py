"""出错与并发：玩家出错走兜底、夜间环节时长不随 AI 快慢变、取消干净、暂停时来的消息不卡、终局等独白。"""

from __future__ import annotations

import asyncio

import pytest

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    NIGHT_PHASES,
    ActKind,
    EventType,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    View,
)
from werewolf.mind.types import Persona
from werewolf.protocol import (
    Narration,
    RequestMsg,
    Review,
    ReviewThought,
    ServerMsg,
    SessionState,
    SetOptions,
)
from werewolf.server.players import RandomPlayer, ReviewData
from werewolf.server.session import GATED_TYPES, Session

from .helpers import ScriptedClient, assert_clean, gateway

WAKE = ('wolves_wake', 'seer_wake', 'witch_wake', 'skill')


def _session(cfg, factory, *, seed: int, client: ScriptedClient | None = None, role: Role = Role.VILLAGER,
             speed: str = 'instant', time_scale: float = 1.0) -> Session:
    _, ww = cfg
    s = Session(gateway(cfg), ww, human_seat=5, human_role=role, seed=seed, speed=speed,  # type: ignore[arg-type]
                send=client.send if client is not None else None, run_dir=None, player_factory=factory,
                time_scale=time_scale)
    if client is not None:
        client.session = s
    return s


class Faulty(RandomPlayer):
    """在指定的请求种类上抛错。"""

    kinds: frozenset[str] = frozenset()

    async def act(self, request: Request, view: View):
        if request.kind.value in self.kinds:
            raise RuntimeError('mind bug')
        return await super().act(request, view)

    async def speak(self, request, view, pace, heard=()):
        if 'speak' in self.kinds:
            raise RuntimeError('model client exploded')
        return await super().speak(request, view, pace, heard)

    async def reflect(self, view: View) -> None:
        if 'reflect' in self.kinds:
            raise RuntimeError('reflect bug')

    def observe(self, event, view):
        if 'observe' in self.kinds:
            raise RuntimeError('observe bug')
        return super().observe(event, view)


@pytest.mark.parametrize('kinds', [
    {'speak'}, {'vote'}, {'wolf_propose', 'wolf_kill'}, {'seer_check', 'witch_act', 'hunter_shoot'},
    {'reflect', 'observe'},
], ids=lambda k: '+'.join(sorted(k)))
async def test_player_faults_never_stop_the_game(cfg, kinds):
    cls = type('F', (Faulty,), {'kinds': frozenset(kinds)})
    for seed in (1, 4):
        c = ScriptedClient(seed)
        s = _session(cfg, lambda seat, role, p, rng: cls(seat, role, p, rng), seed=seed, client=c)
        await asyncio.wait_for(s.run(), 60)
        await c.drain()
        assert_clean(s, c.msgs)


async def test_failed_ai_speech_falls_back_to_pass(cfg):
    """写台词出错：交「过」（acts 是 [PASS]），不交带随机言语行为的占位发言。"""
    cls = type('F', (Faulty,), {'kinds': frozenset({'speak'})})
    c = ScriptedClient(2)
    s = _session(cfg, lambda seat, role, p, rng: cls(seat, role, p, rng), seed=2, client=c)
    await asyncio.wait_for(s.run(), 60)
    speeches = [e for e in s.game.events('judge') if e.type == EventType.SPEECH and e.actor != s.human_seat]
    assert speeches
    for e in speeches:
        assert [a['kind'] for a in e.data['speech']['acts']] == [ActKind.PASS.value], e.data
        assert '占位' not in e.data['speech']['text']


class Slow(RandomPlayer):
    """夜里与技能环节慢吞吞（模拟慢的模型调用）。"""

    delay = 0.4
    calls: list[str] = []

    async def act(self, request: Request, view: View):
        if request.phase in NIGHT_PHASES or request.phase == Phase.HUNTER:
            await asyncio.sleep(self.delay)
        return await super().act(request, view)


async def test_night_step_duration_does_not_depend_on_ai_speed(cfg):
    """规定 9：夜间环节与技能环节对外的时长只取决于导演抽的 hold；AI 到点没答就用兜底。"""
    scale = 0.01
    seen = 0
    for seed in (0, 3):
        stamps: list[tuple[float, ServerMsg]] = []
        c = ScriptedClient(seed)
        orig = c.send

        async def send(msg: ServerMsg, orig=orig, stamps=stamps) -> None:
            stamps.append((asyncio.get_running_loop().time(), msg))
            await orig(msg)
        c.send = send  # type: ignore[method-assign]
        s = _session(cfg, lambda seat, role, p, rng: Slow(seat, role, p, rng), seed=seed, client=c,
                     speed='normal', time_scale=scale)
        await asyncio.wait_for(s.run(), 60)
        await c.drain()
        assert_clean(s, [m for _, m in stamps])
        for i, (t0, m) in enumerate(stamps):
            if not (isinstance(m, Narration) and m.key in WAKE):
                continue
            t1 = next(t for t, n in stamps[i + 1:] if n.t in GATED_TYPES)
            seen += 1
            assert t1 - t0 < m.hold_ms / 1000 * scale + 0.2 < Slow.delay, (m.key, t1 - t0)
    assert seen >= 6


class SlowReflect(RandomPlayer):
    log: list[str] = []

    async def reflect(self, view: View) -> None:
        await asyncio.sleep(0.3)

    async def act(self, request: Request, view: View):
        self.log.append(request.kind.value)
        return await super().act(request, view)


async def test_cancel_while_ai_waits_for_reflection(cfg):
    """取消一局时，正等着反思的 AI 作答任务也被取消，不会在终局之后接着去 act。"""
    SlowReflect.log = []
    c = ScriptedClient(1)
    s = _session(cfg, lambda seat, role, p, rng: SlowReflect(seat, role, p, rng), seed=1, client=c)
    task = asyncio.ensure_future(s.run())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if s._reflect and s._tasks:
            break
    assert s._tasks, '要在 AI 等反思的时候取消'
    pending = set(s._tasks)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert all(t.cancelled() for t in pending)
    assert SlowReflect.log == []


async def test_messages_while_paused_do_not_block(cfg):
    """没有连接时浏览器来的消息（新连接在 resume 之前发的 set 等）不卡住接收循环，重连后状态正确。"""
    holder: dict[str, RequestMsg] = {}

    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if isinstance(msg, RequestMsg) and not holder:
            holder['req'] = msg
            return True
        return False

    c = ScriptedClient(3, hook=hook)
    s = _session(cfg, lambda seat, role, p, rng: RandomPlayer(seat, role, p, rng), seed=3, client=c,
                 speed='normal', time_scale=0.0)
    run = asyncio.ensure_future(s.run())
    for _ in range(500):
        await asyncio.sleep(0)
        if holder:
            break
    s.detach()
    await asyncio.wait_for(s.on_client(SetOptions(speed='fast')), 1)
    await asyncio.wait_for(s.on_client(SetOptions(god_view=True)), 1)
    c2 = ScriptedClient(4)
    c2.session = s
    await s.attach(c2.send)
    state = next(m for m in c2.msgs if isinstance(m, SessionState))
    assert state.speed == 'fast' and state.open_request == holder['req']
    await asyncio.wait_for(run, 60)
    await c2.drain()
    assert_clean(s, list(s.outbox))


class LateMonologue(RandomPlayer):
    """反思之后另起任务写独白，写得慢；终局时还没写完。"""

    def __init__(self, seat: Seat, role: Role, persona: Persona, rng: Rng) -> None:
        super().__init__(seat, role, persona, rng)
        self.monologues: list[int] = []
        self._side: set[asyncio.Task[None]] = set()

    async def reflect(self, view: View) -> None:
        async def write(night: int) -> None:
            await asyncio.sleep(0.2)
            self.monologues.append(night)
        self._side.add(asyncio.ensure_future(write(view.day)))

    async def aclose(self) -> None:
        await asyncio.gather(*self._side)

    def review(self) -> ReviewData:
        return ReviewData(thoughts=[ReviewThought(day=n, phase='night_wolf', kind='monologue', text=f'第{n}夜')
                                    for n in self.monologues])


async def test_review_waits_briefly_for_late_monologues(cfg):
    c = ScriptedClient(5)
    s = _session(cfg, lambda seat, role, p, rng: LateMonologue(seat, role, p, rng), seed=5, client=c)
    await asyncio.wait_for(s.run(), 60)
    review = next(m for m in c.msgs if isinstance(m, Review))
    last_night = max(e.day for e in s.game.events('judge') if e.type == EventType.PHASE
                     and e.data['phase'] == Phase.NIGHT_WOLF)
    nights = {t.day for seat in review.seats for t in seat.thoughts if t.kind == 'monologue'}
    assert last_night in nights
    assert all(t.done() for p in s.players.values() for t in p._side)  # type: ignore[attr-defined]


def test_request_kind_names_cover_faults():
    assert {k.value for k in RequestKind} >= {'vote', 'speak', 'wolf_propose', 'wolf_kill', 'seer_check',
                                              'witch_act', 'hunter_shoot'}


async def test_second_new_game_while_ai_waits_for_reflection(cfg, tmp_path):
    """开新局要能取消旧局：旧局的 AI 正等着反思时，取消不能被吞掉（否则 new_game 一直等旧局打完）。"""
    from werewolf.protocol import NewGame
    from werewolf.server.app import create_app

    settings, ww = cfg
    app = create_app(settings, ww, offline=True, dev=True, speed='instant', runs_dir=tmp_path / 'runs',
                     player_factory=lambda seat, role, p, rng: SlowReflect(seat, role, p, rng))
    hub = app.state.hub
    got: list[ServerMsg] = []

    async def send(msg: ServerMsg) -> None:
        got.append(msg)

    try:
        await hub.new_game(NewGame(role='villager', seat=5, seed=1), send)
        first = hub.session
        for _ in range(400):
            await asyncio.sleep(0.005)
            if first._reflect and first._tasks:
                break
        assert first._tasks
        t0 = asyncio.get_running_loop().time()
        await asyncio.wait_for(hub.new_game(NewGame(role='villager', seat=5, seed=2), send), 2)
        assert asyncio.get_running_loop().time() - t0 < 0.5
        assert hub.session is not first and first.result is None
    finally:
        await asyncio.wait_for(hub.shutdown(), 5)


async def test_npc_slow_at_night_uses_rule_choice(cfg):
    """真的 NpcAgent：夜里 act 到点没答完，交规则版的选择（source='rules'），不是随机兜底。"""
    from werewolf.server.npc import NpcAgent, make_npc

    class SlowNpc(NpcAgent):
        async def act(self, request, view):
            if request.phase in NIGHT_PHASES or request.phase == Phase.HUNTER:
                await asyncio.sleep(0.3)
            return await super().act(request, view)

    settings, ww = cfg
    gw = gateway(cfg)
    npcs: list[NpcAgent] = []

    def factory(seat, role, p, rng):
        npc = make_npc(seat, role, p, rng, gw, ww, s_cfg[0])
        npc.__class__ = SlowNpc
        npcs.append(npc)
        return npc

    from werewolf.kernel.types import GameConfig
    s_cfg = [GameConfig(human_seat=5)]
    c = ScriptedClient(6)
    s = _session(cfg, factory, seed=6, client=c, speed='normal', time_scale=0.005)
    await asyncio.wait_for(s.run(), 60)
    await c.drain()
    assert_clean(s, c.msgs)
    night = [d for n in npcs for d in n.decisions if d.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL,
                                                              RequestKind.SEER_CHECK, RequestKind.WITCH_ACT,
                                                              RequestKind.HUNTER_SHOOT)]
    assert night and all(d.source == 'rules' for d in night)
    assert sum(n.timeouts for n in npcs) == len(night)
    rows = [t for seat in s.review.seats for t in seat.thoughts if t.kind == 'decision']
    assert all(t.source != 'fallback' for t in rows)


async def test_human_speech_parse_failure_does_not_hang(cfg, monkeypatch):
    """解析真人发言时出了意外（模型客户端被关等）：发言照样提交（只有原话、没有言语行为），一局打得完。"""
    import werewolf.server.session as session_mod

    async def boom(*a, **kw):
        raise RuntimeError('client has been closed')
    monkeypatch.setattr(session_mod, 'parse_speech', boom)
    for preview in (True, False):
        c = ScriptedClient(1, preview=preview)
        c.speech_text = lambda: '我觉得3号很可疑，今天出3号'  # type: ignore[method-assign]
        s = _session(cfg, lambda seat, role, p, rng: RandomPlayer(seat, role, p, rng), seed=1, client=c)
        await asyncio.wait_for(s.run(), 10)
        await c.drain()
        assert_clean(s, c.msgs)
        mine = [e for e in s.game.events('judge') if e.type == EventType.SPEECH and e.actor == s.human_seat]
        assert mine and all(e.data['speech']['text'].startswith('我觉得3号') for e in mine)
