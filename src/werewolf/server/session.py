"""一局游戏的会话层（ARCHITECTURE 第 7、8 节）：编排内核与玩家，把真人看得见的东西按节奏发给浏览器。

主循环：
    取 pending() → AI 的请求起 asyncio 任务并行作答（夜里先等该座位的反思）、真人的请求发给浏览器等答复
    → 全部到齐后按座位升序 submit → 新事件经 fanout 喂给每个玩家的 observe（收集神态）
    → 起下一批 AI 任务（所以下一位的发言在上一段发出前就开始算）→ 把真人可见的事件按节奏发出去。
任何一个玩家的 observe 都不会落在它的「开始作答」与「提交」之间：任务只在全部事件观察完之后才起。

节奏与不漏信息：
- 每条 event / narration 带 `hold_ms`（director.pacing），之后的「主线消息」（event、narration、request、
  thinking、review）至少等这么久才发；别人的发言带 `wait_ack`，等 ack 或超时。神态、私有提示、
  受理、解析结果、错误、state、cost 这些附属消息不受 hold 约束（神态要在那条事件的动画期间出现）。
- 夜间环节与技能环节：「睁眼」那句旁白（技能环节是 'skill' 那句）的 hold 取 'night_step' / 'skill'
  分布，与谁活着、有没有请求无关；AI 的结果在这段时间里算好，下一条消息到点才发。
  真人自己在这个环节有请求时，旁白只取普通时长，以真人作答为准。夜里与技能环节不发 thinking。
- `speed='instant'` 时 hold 恒为 0、不等 ack。`time_scale` 只缩放真实等待（测试用），不改报给前端的数。

断线：没有连接时，下一条消息之前暂停；`attach(send)` 重发可重放的历史（replay=True）再发 state。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from werewolf.director import narration, pacing, reactions
from werewolf.kernel import rules
from werewolf.kernel.autoplay import random_action
from werewolf.kernel.engine import Game
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    EMOTES,
    NIGHT_PHASES,
    ROLE_ZH,
    SPEECH_FLAGS,
    Action,
    ActKind,
    Event,
    EventType,
    GameConfig,
    GameResult,
    IllegalAction,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    Speech,
    SpeechAct,
)
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.llm.parse import ParsedSpeech, parse_speech
from werewolf.llm.speak import SpeechResult
from werewolf.mind.types import Decision, Persona, Quote, Reaction, SpeechPlan
from werewolf.protocol import (
    Accepted,
    Ack,
    ActionMsg,
    ClientMsg,
    ClientOption,
    CostMsg,
    ErrorMsg,
    EventMsg,
    GameStarted,
    GodView,
    Narration,
    NarrationKey,
    Parsed,
    PlayerCard,
    Preview,
    PrivateInfo,
    ReactionMsg,
    RequestMsg,
    Resume,
    Review,
    ReviewDay,
    ReviewSeat,
    ReviewStats,
    ReviewThought,
    RulesInfo,
    SeatRole,
    ServerMsg,
    SessionState,
    SetOptions,
    Speed,
    SuspicionPoint,
    Thinking,
)
from werewolf.server.players import Player, RandomPlayer, ReviewData
from werewolf.server.store import RunStore
from werewolf.voice import personas as persona_mod
from werewolf.voice import phrases

log = logging.getLogger(__name__)

Send = Callable[[ServerMsg], Awaitable[None]]
PlayerFactory = Callable[[Seat, Role, Persona, Rng], Player]

REPLAY_TYPES = frozenset({'game', 'event', 'narration', 'private', 'parsed', 'god', 'review'})
GATED_TYPES = frozenset({'event', 'narration', 'request', 'thinking', 'review'})
QUICK_PASS = frozenset({'过', '过。', '过了', 'pass', 'Pass', 'PASS'})
REFLECT_GRACE_S = 2.0  # 反思超时之外再宽限多久
AI_GRACE_S = 5.0
MAX_SPEECH_CHARS = 600  # 内核对 Speech.text 的上限  # AI 作答的兜底超时在模型超时之外再宽限多久

# 事件 → 停顿种类（director.pacing.HOLD_RANGES）
EVENT_HOLD: dict[EventType, str] = {
    EventType.PHASE: 'phase',
    EventType.DAWN: 'dawn',
    EventType.DEATH: 'death',
    EventType.VOTE_RESULT: 'vote_reveal',
    EventType.EXILE: 'exile',
    EventType.ROLE_DEALT: 'deal',
    EventType.GAME_OVER: 'game_over',
    EventType.SPEECH: 'speech',
    EventType.VOTE: 'vote',
}
# 夜间环节：前一句（上一环节闭眼）、后一句（本环节睁眼）
NIGHT_NARRATION: dict[str, tuple[NarrationKey, NarrationKey | None]] = {
    Phase.NIGHT_WOLF: ('night_fall', 'wolves_wake'),
    Phase.NIGHT_SEER: ('wolves_sleep', 'seer_wake'),
    Phase.NIGHT_WITCH: ('seer_sleep', 'witch_wake'),
    Phase.DAWN: ('witch_sleep', None),
}
REQ_EVENT: dict[RequestKind, tuple[EventType, ...]] = {
    RequestKind.WOLF_PROPOSE: (EventType.WOLF_PROPOSE,),
    RequestKind.WOLF_KILL: (EventType.WOLF_KILL,),
    RequestKind.SEER_CHECK: (EventType.SEER_RESULT,),
    RequestKind.WITCH_ACT: (EventType.WITCH_SAVE, EventType.WITCH_POISON),
    RequestKind.HUNTER_SHOOT: (EventType.HUNTER_SHOT,),
    RequestKind.SPEAK: (EventType.SPEECH,),
    RequestKind.VOTE: (EventType.VOTE,),
}
OPTION_ACTION = {'kill': 'kill', 'check': 'check', 'save': 'save', 'poison': 'poison', 'shoot': 'shoot',
                 'vote': 'vote'}
BOARD = '9 人标准局：3 狼 3 民 预言家 女巫 猎人'


class _BadAction(Exception):
    def __init__(self, detail: str, message: str) -> None:
        super().__init__(message)
        self.detail = detail
        self.message = message


@dataclass
class _Open:
    """真人此刻的请求。"""

    token: str
    req: Request
    msg: RequestMsg
    future: asyncio.Future[Action]
    sent: bool = False
    preview: tuple[str, ParsedSpeech] | None = None


@dataclass
class _Answer:
    req: Request
    action: Action
    decision: Decision | None = None
    plan: SpeechPlan | None = None
    result: SpeechResult | None = None
    brief: Any = None
    human: bool = False


@dataclass
class _SeatRecord:
    thoughts: list[ReviewThought] = field(default_factory=list)
    days: list[ReviewDay] = field(default_factory=list)


def _option_action(option_id: str) -> str:
    head, _, tail = option_id.partition(':')
    if tail in ('none', 'abstain') or option_id == 'pass':
        return 'none'
    return OPTION_ACTION.get(head, 'none')


def _seats_zh(seats: Sequence[int]) -> str:
    return '、'.join(f'{s}号' for s in seats)


class Session:
    def __init__(self, gateway: Gateway, ww: WerewolfSettings, *, human_seat: Seat | None, human_role: Role | None,
                 seed: int, speed: Speed, send: Send | None, run_dir: Path | None, autopilot: bool = False,
                 player_factory: PlayerFactory | None = None, dev: bool = False, human_name: str = '你',
                 game_id: str | None = None, time_scale: float = 1.0) -> None:
        self.gateway = gateway
        self.ww = ww
        self.seed = seed
        self.speed: Speed = speed
        self.autopilot = autopilot
        self.dev = dev
        self.human_name = human_name
        self.time_scale = time_scale
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.game_id = game_id or (self.run_dir.name if self.run_dir is not None else f'g{seed:x}')
        self.store = RunStore(self.run_dir)
        self.rng = Rng(seed, 'session')
        self._hold_rng = self.rng.fork('hold')
        self._narr_rng = self.rng.fork('narration')
        self._quip_rng = self.rng.fork('quip')
        self._tok_rng = self.rng.fork('token')
        self._fallback_rng = self.rng.fork('fallback')

        # ---- 发牌、人设、对局 ----
        self.human_seat: Seat = human_seat if human_seat is not None else self.rng.fork('seat').randint(1, 9)
        config = GameConfig(human_seat=self.human_seat)
        deck = list(config.roles)
        self.rng.fork('deal').shuffle(deck)
        roles = {i + 1: r for i, r in enumerate(deck)}
        if human_role is not None and roles[self.human_seat] != human_role:
            other = self.rng.fork('swap').choice([s for s, r in roles.items() if r == human_role])
            roles[other], roles[self.human_seat] = roles[self.human_seat], roles[other]
        self.game = Game.new(config, seed, roles)
        drafted = persona_mod.draft(self.rng.fork('personas'), 9)
        ai_seats = [s for s in range(1, config.seats + 1) if s != self.human_seat]
        self.personas: dict[Seat, Persona] = dict(zip(ai_seats, drafted[:8], strict=True))
        factory = player_factory or self._default_factory()
        self.players: dict[Seat, Player] = {
            s: factory(s, roles[s], self.personas[s], self.rng.fork(f'npc:{s}')) for s in ai_seats
        }
        if autopilot:
            self.host_persona = drafted[8]
            self.players[self.human_seat] = factory(self.human_seat, roles[self.human_seat], drafted[8],
                                                    self.rng.fork(f'npc:{self.human_seat}'))

        # ---- 线上状态 ----
        self._send: Send | None = send
        self._headless = send is None
        self._attached = asyncio.Event()
        if send is not None:
            self._attached.set()
        self._wire_lock = asyncio.Lock()
        self.outbox: list[ServerMsg] = []  # 发出的全部消息（含无连接时记下的），测试与重放用
        self._seq = 0
        self._seq_of: dict[int, int] = {}  # 内核事件号 → seq
        self._cursor = 0  # 发送端已处理到的内核事件号
        self._next_ok = 0.0
        self._ack_seq: int | None = None
        self._ack_evt = asyncio.Event()
        self._open: _Open | None = None
        self._tokens: set[str] = set()
        self.god_view = False
        self._can_god = False
        self._gate = reactions.ReactionGate()
        self._reactions: dict[int, list[Reaction]] = {}
        self._recent_lines: list[str] = []
        self._heard: list[Quote] = []
        self._human_parsed: ParsedSpeech | None = None
        self._reflect: dict[Seat, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._records: dict[Seat, _SeatRecord] = {s: _SeatRecord() for s in range(1, config.seats + 1)}
        self._speech_tiers: Counter[str] = Counter()
        self._spent0 = gateway.spent()
        self._calls0 = dict(gateway.llm.ledger.calls_by_kind)
        self.result: GameResult | None = None
        self.review: Review | None = None
        self.store.meta({'game_id': self.game_id, 'seed': seed, 'human_seat': self.human_seat,
                         'autopilot': autopilot, 'speed': speed, 'started_ms': int(time.time() * 1000)})

    # ------------------------------------------------------------------ 装配

    def _default_factory(self) -> PlayerFactory:
        """有 server/npc.py 就用它的 make_npc，否则退到 RandomPlayer。"""
        try:
            npc = importlib.import_module('werewolf.server.npc')
        except ModuleNotFoundError as exc:
            if exc.name not in ('werewolf.server.npc',):
                raise
            npc = None
        make = getattr(npc, 'make_npc', None) if npc is not None else None
        if make is None:
            return lambda seat, role, persona, rng: RandomPlayer(seat, role, persona, rng)
        gw, ww, cfg = self.gateway, self.ww, self.game.config
        return lambda seat, role, persona, rng: make(seat, role, persona, rng, gw, ww, cfg)

    @property
    def over(self) -> bool:
        return self.game.over

    @property
    def attached(self) -> bool:
        return self._attached.is_set()

    @property
    def human_role(self) -> Role:
        return self.game.role_of(self.human_seat)

    # ------------------------------------------------------------------ 发送

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _hold(self, kind: str) -> int:
        return pacing.hold_ms(kind, self.speed, self._hold_rng)

    async def _wait_gate(self) -> None:
        """等上一条主线消息的 hold 与 ack。"""
        if self.speed == 'instant':
            self._next_ok, self._ack_seq = 0.0, None
            return
        delay = self._next_ok - self._now()
        if delay > 0:
            await asyncio.sleep(delay)
        if self._ack_seq is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._ack_evt.wait(), self.ww.limits.ack_timeout_s * self.time_scale)
            self._ack_seq = None

    def _log_out(self, msg: ServerMsg) -> None:
        self.outbox.append(msg)
        self.store.wire('out', msg)
        if isinstance(msg, RequestMsg) and self._open is not None and self._open.msg is msg:
            self._open.sent = True

    async def _deliver(self, msg: ServerMsg) -> None:
        if self._headless:
            self._log_out(msg)
            return
        while True:
            await self._attached.wait()
            async with self._wire_lock:
                if not self._attached.is_set() or self._send is None:
                    continue
                self._log_out(msg)
                try:
                    await self._send(msg)
                except Exception:  # noqa: BLE001 — 连接断了：消息已记下，重连时重放
                    log.info('send failed; detaching')
                    self.detach()
                return

    async def _emit(self, msg: ServerMsg) -> None:
        if msg.t in GATED_TYPES:
            await self._wait_gate()
        await self._deliver(msg)
        hold = getattr(msg, 'hold_ms', 0) or 0
        if msg.t in GATED_TYPES and hold:
            self._next_ok = max(self._next_ok, self._now() + hold / 1000 * self.time_scale)
        if isinstance(msg, EventMsg) and msg.wait_ack:
            self._ack_seq = msg.seq
            self._ack_evt.clear()

    def detach(self) -> None:
        """连接断了：会话在下一条消息之前暂停。"""
        self._attached.clear()
        self._send = None

    async def attach(self, send: Send) -> None:
        """（重新）接上浏览器：按原顺序重发可重放的消息（replay=True），最后一条 state，然后继续现场。"""
        async with self._wire_lock:
            self._send = send
            self._headless = False
            try:
                for m in self.replay_messages():
                    await send(m)
                await send(self._state())
            except Exception:  # noqa: BLE001
                self._send = None
                return
            self._ack_seq = None
            self._ack_evt.set()  # 未完成的 ack 视为已收到
            self._attached.set()

    def replay_messages(self) -> list[ServerMsg]:
        out: list[ServerMsg] = []
        for m in self.outbox:
            if m.t not in REPLAY_TYPES:
                continue
            if isinstance(m, Parsed) and m.preview:
                continue
            out.append(m.model_copy(update={'replay': True}) if 'replay' in type(m).model_fields else m)
        return out

    def _state(self) -> SessionState:
        op = self._open
        return SessionState(speed=self.speed, god_view=self.god_view, can_god_view=self._can_god,
                            open_request=op.msg if op is not None and op.sent and not op.future.done() else None,
                            over=self.game.over and self.review is not None)

    async def _narrate(self, key: NarrationKey, tension: float, hold_kind: str = 'narration', **fmt: Any) -> None:
        text = narration.line(key, tension, self._narr_rng, avoid=self._recent_lines, **fmt)
        if not text:
            return
        self._recent_lines = [*self._recent_lines[-5:], text]
        await self._emit(Narration(key=key, text=text, hold_ms=self._hold(hold_kind)))

    async def _cost(self) -> None:
        led = self.gateway.llm.ledger
        frac = led.fraction
        budget = 'exhausted' if frac >= 1 else 'warn' if frac >= 0.8 else 'ok'
        await self._emit(CostMsg(spent_usd=round(self.gateway.spent() - self._spent0, 2), budget=budget))

    # ------------------------------------------------------------------ 主循环

    async def run(self) -> GameResult:
        try:
            await self._emit(self._game_msg())
            await self._emit(self._state())
            events = self.game.events('judge')
            while True:
                await self._observe(events)
                reqs = self.game.pending()
                tasks = self._start_ai(reqs)
                await self._send_events(events, reqs)
                if self.game.over:
                    break
                answers = await self._collect(reqs, tasks)
                events = self._submit(answers)
            await self._finish()
            assert self.game.result is not None
            self.result = self.game.result
            return self.result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('session crashed')
            with contextlib.suppress(Exception):
                await self._deliver(ErrorMsg(code='internal', message='服务端出错，这一局无法继续'))
            raise
        finally:
            self._cancel_tasks()
            self.store.close()

    def _cancel_tasks(self) -> None:
        for t in [*self._tasks, *self._reflect.values()]:
            if not t.done():
                t.cancel()
        if self._open is not None and not self._open.future.done():
            self._open.future.cancel()

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ---- 观察 ----

    async def _observe(self, events: Sequence[Event]) -> None:
        for ev in events:
            self.store.event(ev)
        if any(ev.type == EventType.DAWN for ev in events):
            await self._await_reflections()
        fan = self.game.fanout(events)
        for seat, player in self.players.items():
            evs = fan.get(seat) or []
            if not evs:
                continue
            view = self.game.view(seat)
            for ev in evs:
                try:
                    got = player.observe(ev, view) or []
                except Exception:  # noqa: BLE001
                    log.exception('observe failed for seat %s', seat)
                    got = []
                for r in got:
                    if r.seat == seat and seat != self.human_seat and r.emote in EMOTES and ev.vis == 'public':
                        self._reactions.setdefault(ev.id, []).append(r)
        for ev in events:
            if ev.type != EventType.PHASE:
                continue
            ph = ev.data.get('phase')
            if ph in (Phase.DAY_SPEECH, Phase.PK_SPEECH, Phase.LAST_WORDS):
                self._heard = []  # 真人的原话只给同一轮里之后发言的 AI；要在起下一批任务之前清
            if ph == Phase.NIGHT_WOLF:
                self._start_reflections()
            elif ph == Phase.DAY_SPEECH:
                self._snapshot_days(ev.day)

    def _start_reflections(self) -> None:
        for seat, player in self.players.items():
            if self.game.is_alive(seat):
                old = self._reflect.get(seat)
                if old is not None and not old.done():
                    old.cancel()
                self._reflect[seat] = asyncio.ensure_future(self._reflect_one(player, seat))

    async def _reflect_one(self, player: Player, seat: Seat) -> None:
        try:
            await asyncio.wait_for(player.reflect(self.game.view(seat)),
                                   self.ww.limits.reflect_timeout_s + REFLECT_GRACE_S)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            log.exception('reflect failed for seat %s', seat)

    async def _await_reflection(self, seat: Seat) -> None:
        t = self._reflect.pop(seat, None)
        if t is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await t

    async def _await_reflections(self) -> None:
        for seat in list(self._reflect):
            await self._await_reflection(seat)

    def _snapshot_days(self, day: int) -> None:
        for seat, player in self.players.items():
            snap_fn = getattr(player, 'snapshot', None)
            if snap_fn is None:
                mind = getattr(player, 'mind', None)
                snap_fn = getattr(mind, 'snapshot', None)
            if not callable(snap_fn):
                continue
            try:
                snap = snap_fn()
            except Exception:  # noqa: BLE001
                continue
            if snap is None:
                continue
            susp = [SuspicionPoint(seat=s, p=round(float(p), 3)) for s, p in sorted(snap.suspicion.items())]
            self._records[seat].days.append(ReviewDay(day=day, mood=round(float(snap.mood), 1),
                                                      state=str(snap.state), suspicion=susp))

    # ---- AI 作答 ----

    def _start_ai(self, reqs: Sequence[Request]) -> dict[str, asyncio.Task[_Answer]]:
        heard = tuple(self._heard)
        return {r.id: self._spawn(self._ai_answer(r, heard)) for r in reqs if r.seat in self.players}

    async def _ai_answer(self, req: Request, heard: Sequence[Quote] = ()) -> _Answer:
        player = self.players[req.seat]
        if req.phase in NIGHT_PHASES:
            await self._await_reflection(req.seat)
        view = self.game.view(req.seat)
        lim = self.ww.limits
        try:
            if req.kind == RequestKind.SPEAK:
                pv = self.game.public_view()
                coro = player.speak(req, view, pacing.pace(pv), list(heard))
                action, plan, dec, result = await asyncio.wait_for(
                    coro, lim.speech_timeout_s + lim.decide_timeout_s + AI_GRACE_S)
                return _Answer(req, action, dec, plan, result, getattr(player, 'last_brief', None))
            action, dec = await asyncio.wait_for(player.act(req, view), lim.decide_timeout_s * 2 + AI_GRACE_S)
            return _Answer(req, action, dec)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 游戏永远不因玩家出错而停
            log.exception('player %s failed on %s; random fallback', req.seat, req.kind)
            return self._fallback(req)

    def _fallback(self, req: Request) -> _Answer:
        action = random_action(self.game, req, self._fallback_rng)
        dec = Decision(request_id=req.id, seat=req.seat, day=req.day, kind=req.kind,
                       option_id=action.option_id or 'fallback', source='fallback', thought='（出错，随机作答）')
        return _Answer(req, action, dec)

    # ---- 收集与提交 ----

    async def _collect(self, reqs: Sequence[Request], tasks: dict[str, asyncio.Task[_Answer]]) -> list[_Answer]:
        answers: list[_Answer] = []
        speak = next((r for r in reqs if r.kind == RequestKind.SPEAK), None)
        human = next((r for r in reqs if r.seat == self.human_seat and r.id not in tasks), None)
        thinking = (speak is not None and speak.id in tasks and speak.seat != self.human_seat
                    and speak.phase not in NIGHT_PHASES)
        if thinking:
            assert speak is not None
            await self._emit(Thinking(seat=speak.seat, on=True, what='speak'))
        if human is not None:
            action = await self._ask_human(human)
            answers.append(_Answer(human, action, human=True))
        for t in tasks.values():
            answers.append(await t)
        if thinking:
            assert speak is not None
            await self._emit(Thinking(seat=speak.seat, on=False, what='speak'))
        return answers

    def _submit(self, answers: list[_Answer]) -> list[Event]:
        events: list[Event] = []
        for ans in sorted(answers, key=lambda a: a.req.seat):
            act = ans.action
            if act.request_id != ans.req.id or act.seat != ans.req.seat:
                act = act.model_copy(update={'request_id': ans.req.id, 'seat': ans.req.seat})
            try:
                events.extend(self.game.submit(act))
            except IllegalAction as exc:
                log.warning('seat %s illegal action (%s); random fallback', ans.req.seat, exc.code)
                fb = self._fallback(ans.req)
                ans.action, ans.decision, ans.plan, ans.result = fb.action, fb.decision, None, None
                events.extend(self.game.submit(fb.action))
        for ans in answers:
            self._record(ans, events)
        return events

    def _record(self, ans: _Answer, events: Sequence[Event]) -> None:
        req = ans.req
        types = REQ_EVENT.get(req.kind, ())
        ev = next((e for e in events if e.type in types and (e.actor == req.seat or e.type == EventType.WOLF_KILL)),
                  None)
        if ans.human:
            return
        dec = ans.decision
        row: dict[str, Any] = {'seat': req.seat, 'request_id': req.id, 'kind': req.kind.value, 'day': req.day,
                               'phase': req.phase.value, 'event_id': ev.id if ev else None,
                               'decision': dec.model_dump(mode='json') if dec else None}
        labels = {o.id: o.label for o in req.options}
        if ans.plan is not None:
            row['plan'] = ans.plan.model_dump(mode='json')
        if ans.result is not None:
            row['tier'] = ans.result.tier
            self._speech_tiers[ans.result.tier] += 1
            brief = ans.brief.model_dump(mode='json') if hasattr(ans.brief, 'model_dump') else ans.brief
            self.store.brief({'request_id': req.id, 'brief': brief, 'text': ans.result.text,
                              'tier': ans.result.tier, 'rejected': ans.result.rejected})
        self.store.decision(row)
        rec = self._records[req.seat]
        if ans.plan is not None:
            plan = ans.plan
            rec.thoughts.append(ReviewThought(
                day=req.day, phase=req.phase, kind='speech', text=plan.thought or plan.label or '发言',
                event_id=ev.id if ev else None, choice=plan.label or plan.id,
                lies=[i for i, a in enumerate(plan.acts) if not a.truthful],
                state=dec.state if dec else None, source=ans.result.tier if ans.result else None))
        elif dec is not None:
            choice = labels.get(ans.action.option_id or '', ans.action.option_id)
            best = labels.get(dec.best_option_id) if dec.best_option_id and dec.best_option_id != dec.option_id \
                else None
            rec.thoughts.append(ReviewThought(
                day=req.day, phase=req.phase, kind='decision', text=dec.thought or (choice or ''),
                event_id=ev.id if ev else None, choice=choice, best=best, state=dec.state, source=dec.source))

    # ---- 真人请求 ----

    def _token(self) -> str:
        while True:
            tok = f'q{self._tok_rng.randint(0, 16 ** 6 - 1):06x}'
            if tok not in self._tokens:
                self._tokens.add(tok)
                return tok

    def _request_msg(self, token: str, req: Request) -> RequestMsg:
        ctx: dict[str, Any] = dict(req.context)
        if req.kind == RequestKind.WOLF_KILL:
            ctx = {'proposals': list(req.context.get('proposals', [])), 'first_night': req.day == 1}
        elif req.kind == RequestKind.SPEAK:
            lim = self.ww.limits
            ctx['max_chars'] = min(lim.human_speech_chars, 500)
            ctx['confirm'] = bool(lim.confirm_parse)
        elif req.kind == RequestKind.SEER_CHECK:
            ctx = {}
        opts = [ClientOption(id=o.id, label=o.label, target=o.target, action=_option_action(o.id))  # type: ignore[arg-type]
                for o in req.options]
        return RequestMsg(id=token, kind=req.kind.value, options=opts, speech_kind=req.speech_kind,  # type: ignore[arg-type]
                          context=ctx)

    async def _ask_human(self, req: Request) -> Action:
        token = self._token()
        fut: asyncio.Future[Action] = asyncio.get_running_loop().create_future()
        op = _Open(token=token, req=req, msg=self._request_msg(token, req), future=fut)
        self._open = op
        await self._emit(op.msg)
        try:
            return await fut
        finally:
            if self._open is op:
                self._open = None

    async def on_client(self, msg: ClientMsg) -> None:
        """浏览器来的消息（app 已用 TypeAdapter(ClientMsg) 校验过）。new_game 由 app 处理。"""
        self.store.wire('in', msg)
        if isinstance(msg, Ack):
            if self._ack_seq is not None and msg.seq == self._ack_seq:
                self._ack_evt.set()
        elif isinstance(msg, SetOptions):
            await self._on_set(msg)
        elif isinstance(msg, Preview):
            await self._on_preview(msg)
        elif isinstance(msg, ActionMsg):
            await self._on_action(msg)
        elif isinstance(msg, Resume):
            if self._send is not None:
                await self.attach(self._send)
        else:
            await self._deliver(ErrorMsg(code='bad_message', message='这条消息要由服务入口处理'))

    async def _error(self, code: str, message: str, request_id: str | None = None, detail: str | None = None) -> None:
        await self._deliver(ErrorMsg(code=code, message=message, request_id=request_id, detail=detail))  # type: ignore[arg-type]

    def _live_open(self, token: str) -> _Open | None:
        op = self._open
        if op is None or op.token != token or op.future.done() or not op.sent:
            return None
        return op

    async def _on_set(self, msg: SetOptions) -> None:
        ok = True
        if msg.god_view is not None:
            if not self._can_god:
                ok = False
                await self._error('not_allowed', '现在还不能打开上帝视角')
            elif msg.god_view and not self.god_view:
                await self._deliver(self._god_msg())
                self.god_view = True
            elif not msg.god_view:
                self.god_view = False
        if msg.speed is not None:
            self.speed = msg.speed
            if msg.speed == 'instant':
                self._ack_evt.set()
        if ok or msg.speed is not None:
            await self._deliver(self._state())

    def _god_msg(self) -> GodView:
        roles = [SeatRole(seat=s, role=self.game.role_of(s)) for s in range(1, self.game.config.seats + 1)]
        evs = [e for e in self.game.events('judge') if e.id <= self._cursor and e.id not in self._seq_of]
        return GodView(roles=roles, events=evs)

    async def _on_preview(self, msg: Preview) -> None:
        op = self._live_open(msg.request_id)
        if op is None:
            await self._error('illegal_action', '这个请求已经失效', msg.request_id, 'unknown_request')
            return
        if op.req.kind != RequestKind.SPEAK:
            await self._error('illegal_action', '这一步不是发言', msg.request_id, 'not_an_option')
            return
        text = msg.text.strip()
        parsed = await self._parse(text, op.req.seat)
        op.preview = (text, parsed)
        if self._live_open(msg.request_id) is not op:
            return
        await self._deliver(Parsed(preview=True, request_id=op.token, acts=parsed.acts, flags=parsed.flags,
                                   ignored=parsed.ignored, notice=parsed.notice))

    async def _parse(self, text: str, seat: Seat) -> ParsedSpeech:
        if text in QUICK_PASS:
            return ParsedSpeech(acts=[SpeechAct(kind=ActKind.PASS)], clean_text=text)
        return await parse_speech(self.gateway, text, self.game.public_view(), seat)

    async def _on_action(self, msg: ActionMsg) -> None:
        op = self._live_open(msg.request_id)
        if op is None:
            await self._error('illegal_action', '这个请求已经失效', msg.request_id, 'unknown_request')
            return
        req = op.req
        if req.kind == RequestKind.SPEAK:
            await self._on_speech(op, msg)
            return
        if msg.option_id not in {o.id for o in req.options}:
            await self._error('illegal_action', '这个选项不在可选范围里', op.token, 'not_an_option')
            return
        stance = msg.stance if req.kind == RequestKind.WOLF_KILL and req.day == 1 else None
        action = Action(request_id=req.id, seat=req.seat, option_id=msg.option_id, stance=stance)
        self._open = None
        await self._deliver(Accepted(request_id=op.token, option_id=action.option_id))
        if not op.future.done():
            op.future.set_result(action)

    async def _on_speech(self, op: _Open, msg: ActionMsg) -> None:
        """真人发言：空的回 bad_speech（请求仍有效）；否则先回 accepted（输入框可以关了），再解析、提交。"""
        req = op.req
        raw = (msg.text or '').strip()
        if not raw:
            await self._error('illegal_action', '发言不能为空', op.token, 'bad_speech')
            return
        self._open = None  # 认领：此后同一请求再来就是 unknown_request
        await self._deliver(Accepted(request_id=op.token))
        try:
            speech, parsed = await self._human_speech(op, raw, msg.keep)
        except _BadAction:  # 解析结果过不了结构校验：只留原话，不记言语行为
            log.warning('human speech failed structural check; dropping acts')
            limit = min(MAX_SPEECH_CHARS, self.ww.limits.human_speech_chars)
            speech = Speech(text=raw[:limit], acts=[], source='human',
                            flags=['truncated'] if len(raw) > limit else [])
            parsed = ParsedSpeech(acts=[], flags=list(speech.flags), clean_text=speech.text)
        self._human_parsed = parsed
        clean = parsed.clean_text[: min(self.ww.limits.quote_chars, 80)].strip()
        if clean:
            self._heard.append(Quote(seat=req.seat, text=clean, flags=list(speech.flags)))
        if not op.future.done():
            op.future.set_result(Action(request_id=req.id, seat=req.seat, speech=speech))

    async def _human_speech(self, op: _Open, raw: str, keep: list[int] | None) -> tuple[Speech, ParsedSpeech]:
        if op.preview is not None and op.preview[0] == raw:
            parsed = op.preview[1]
            if keep is not None:
                idx = sorted({i for i in keep if 0 <= i < len(parsed.acts)})
                parsed = parsed.model_copy(update={'acts': [parsed.acts[i] for i in idx]})
        else:
            parsed = await self._parse(raw, op.req.seat)
        limit = min(MAX_SPEECH_CHARS, self.ww.limits.human_speech_chars)
        flags = [f for f in dict.fromkeys(parsed.flags) if f in SPEECH_FLAGS]
        if len(raw) > limit:
            raw = raw[:limit].rstrip() or raw[:limit]
            if 'truncated' not in flags:
                flags.append('truncated')
        acts = list(parsed.acts)[:12]
        speech = Speech(text=raw, acts=acts, source='human', flags=flags)
        problem = rules.speech_problem(speech, self.game.config.seats)
        if problem:
            raise _BadAction('bad_speech', f'发言不合规：{problem}')
        return speech, parsed.model_copy(update={'acts': acts, 'flags': flags})

    # ---- 发事件 ----

    def _event_visible(self, ev: Event) -> bool:
        return self.god_view or self.game.visible(ev, self.human_seat)

    def _human_in_step(self, ev: Event, last_phase_id: int, reqs: Sequence[Request]) -> bool:
        """真人自己在这个夜间环节 / 技能环节有请求：以真人作答为准，不另加时长。"""
        if ev.data.get('phase') == Phase.NIGHT_WOLF and self.game.role_of(self.human_seat) == Role.WEREWOLF \
                and self.game.is_alive(self.human_seat):
            return True
        return ev.id == last_phase_id and any(r.seat == self.human_seat for r in reqs)

    async def _send_events(self, events: Sequence[Event], reqs: Sequence[Request]) -> None:
        if not events:
            return
        tension = pacing.tension(self.game.public_view())
        phases = [e.id for e in events if e.type == EventType.PHASE]
        last_phase_id = phases[-1] if phases else -1
        for ev in events:
            ph = ev.data.get('phase') if ev.type == EventType.PHASE else None
            if ph in NIGHT_NARRATION:
                await self._narrate(NIGHT_NARRATION[ph][0], tension, day=ev.day)
            visible = self._event_visible(ev)
            self._cursor = ev.id
            if visible:
                await self._send_event(ev)
            if ev.type == EventType.GAME_START:
                await self._narrate('deal', tension, hold_kind='deal')
            await self._after_event(ev, ph, tension, last_phase_id, reqs)
        await self._update_god()

    async def _send_event(self, ev: Event) -> None:
        self._seq += 1
        seq = self._seq
        self._seq_of[ev.id] = seq
        kind = EVENT_HOLD.get(ev.type, 'event')
        wait = (ev.type == EventType.SPEECH and ev.actor != self.human_seat and self.speed != 'instant')
        await self._emit(EventMsg(seq=seq, ev=ev.model_copy(update={'id': seq}), hold_ms=self._hold(kind),
                                  wait_ack=wait))
        if ev.vis != 'public':
            info = self._private(ev)
            if info is not None:
                await self._deliver(PrivateInfo(key=info[0], text=info[1], seq=seq))  # type: ignore[arg-type]
        if ev.type == EventType.SPEECH and ev.actor == self.human_seat and self._human_parsed is not None:
            p = self._human_parsed
            self._human_parsed = None
            await self._deliver(Parsed(preview=False, seq=seq, acts=p.acts, flags=p.flags, ignored=p.ignored,
                                       notice=p.notice))
        rs = self._reactions.pop(ev.id, [])
        for r in self._gate.pick(rs):
            persona = self.personas.get(r.seat)
            quip = None
            if r.quip_key and persona is not None:
                quip = phrases.quip(persona_mod.voice_of(persona), r.quip_key, self._quip_rng)
            await self._deliver(ReactionMsg(seat=r.seat, emote=r.emote, label=reactions.label(r.emote), quip=quip,
                                            intensity=r.intensity, seq=seq))

    async def _after_event(self, ev: Event, ph: Any, tension: float, last_phase_id: int,
                           reqs: Sequence[Request]) -> None:
        d = ev.data
        if ph is not None:
            if ph in NIGHT_NARRATION and NIGHT_NARRATION[ph][1] is not None:
                kind = 'narration' if self._human_in_step(ev, last_phase_id, reqs) else 'night_step'
                await self._narrate(NIGHT_NARRATION[ph][1], tension, hold_kind=kind)  # type: ignore[arg-type]
            elif ph == Phase.HUNTER:
                kind = 'narration' if self._human_in_step(ev, last_phase_id, reqs) else 'skill'
                await self._narrate('skill', tension, hold_kind=kind)
            elif ph == Phase.LAST_WORDS:
                await self._narrate('last_words', tension, seats=d.get('order', []))
            elif ph == Phase.DAY_SPEECH:
                await self._cost()
                order = d.get('order') or [None]
                await self._narrate('speech_start', tension, seat=order[0], day=ev.day)
            elif ph == Phase.DAY_VOTE:
                await self._narrate('vote_start', tension)
            elif ph == Phase.PK_SPEECH:
                await self._narrate('pk_start', tension, seats=d.get('candidates', []))
            elif ph == Phase.PK_VOTE:
                await self._narrate('pk_vote', tension)
            return
        if ev.type == EventType.DAWN:
            deaths = d.get('deaths') or []
            if deaths:
                await self._narrate('dawn_deaths', tension, seats=deaths)
            else:
                await self._narrate('dawn_peaceful', tension)
        elif ev.type == EventType.HUNTER_SHOT and ev.vis == 'public' and ev.target is not None:
            await self._narrate('hunter_shot', tension, seat=ev.target)
        elif ev.type == EventType.VOTE_RESULT:
            if d.get('outcome') == 'exile':
                await self._narrate('vote_result', tension)
            elif d.get('outcome') == 'none':
                await self._narrate('no_exile', tension)
        elif ev.type == EventType.EXILE:
            await self._narrate('exile', tension, seat=ev.target)
        elif ev.type == EventType.GAME_OVER:
            await self._narrate('game_over', tension)

    def _private(self, ev: Event) -> tuple[str, str] | None:
        """真人的私有事件 → (key, 可直接显示的中文)。上帝视角下别人的私有事件不配提示。"""
        h = self.human_seat
        if not self.game.visible(ev, h):
            return None
        t, d = ev.type, ev.data
        if t == EventType.ROLE_DEALT and ev.actor == h:
            return 'role', f'你的身份是：{ROLE_ZH[Role(d["role"])]}'
        if t == EventType.WOLF_TEAM:
            mates = [s for s in d.get('wolves', []) if s != h]
            return 'wolf_team', f'你的狼队友是：{_seats_zh(mates)}' if mates else '你是场上唯一的狼人'
        if t == EventType.WOLF_PROPOSE:
            what = f'刀 {ev.target} 号' if ev.target else '空刀'
            note = f'（{d["note"]}）' if d.get('note') else ''
            return 'wolf_propose', f'{ev.actor} 号队友提议{what}{note}'
        if t == EventType.WOLF_KILL:
            return 'wolf_kill', f'今夜狼队刀了 {ev.target} 号' if ev.target else '今夜狼队选择空刀'
        if t == EventType.SEER_RESULT:
            res = '狼人' if d.get('result') == 'wolf' else '好人'
            return 'seer_result', f'{ev.target} 号的查验结果：{res}'
        if t == EventType.WITCH_NOTICE:
            if not d.get('victim_known'):
                return 'witch_notice', '解药已经用掉，你不知道今夜谁被刀'
            return 'witch_notice', f'今夜 {ev.target} 号被刀了' if ev.target else '今夜没有人被刀'
        if t == EventType.WITCH_SAVE:
            return 'witch_used', f'你用解药救了 {ev.target} 号'
        if t == EventType.WITCH_POISON:
            return 'witch_used', f'你用毒药毒了 {ev.target} 号'
        if t == EventType.HUNTER_BLOCKED:
            return 'hunter_blocked', '你中了毒，不能开枪'
        if t == EventType.HUNTER_SHOT and ev.vis != 'public':
            return 'hunter_pass', '你选择不开枪'
        return None

    # ---- 上帝视角 ----

    def _human_settled(self) -> bool:
        """真人已死，且内核不会再向他要任何决定（开枪与遗言已提交或不适用）。"""
        h = self.human_seat
        if self.game.is_alive(h):
            return False
        if any(r.seat == h for r in self.game.pending()):
            return False
        if self.game.over:
            return True
        evs = self.game.events('judge')
        last = next((e for e in reversed(evs) if e.type == EventType.PHASE), None)
        if last is None:
            return False
        ph = last.data.get('phase')
        if ph == Phase.HUNTER:
            return False
        if ph == Phase.LAST_WORDS and h in (last.data.get('order') or []):
            return any(e.type == EventType.SPEECH and e.actor == h and e.id > last.id for e in evs)
        return True

    async def _update_god(self) -> None:
        can = self._human_settled() and not self.autopilot
        if can != self._can_god:
            self._can_god = can
            await self._deliver(self._state())

    # ---- 终局 ----

    def _game_msg(self) -> GameStarted:
        cfg = self.game.config
        cards = []
        for s in range(1, cfg.seats + 1):
            if s == self.human_seat:
                cards.append(PlayerCard(seat=s, name=self.human_name, is_human=True))
            else:
                p = self.personas[s]
                cards.append(PlayerCard(seat=s, name=p.name, title=p.title, tags=list(p.tags), avatar=p.avatar))
        counts = Counter(r.value for r in cfg.roles)
        rules_info = RulesInfo(board=BOARD, role_counts=dict(counts), witch_self_save=cfg.witch_self_save,
                               last_words_nights=cfg.last_words_nights, allow_abstain=cfg.allow_abstain,
                               allow_empty_kill=cfg.allow_empty_kill, max_days=cfg.max_days)
        return GameStarted(game_id=self.game_id, you=self.human_seat, your_role=self.human_role, players=cards,
                           rules=rules_info)

    def _build_review(self) -> Review:
        res = self.game.result
        assert res is not None
        seats = []
        for s in range(1, self.game.config.seats + 1):
            player = self.players.get(s)
            rec = self._records[s]
            extra: ReviewData | None = None
            review_fn = getattr(player, 'review', None)
            if callable(review_fn):
                try:
                    got = review_fn()
                    extra = got if isinstance(got, ReviewData) else (ReviewData.model_validate(got) if got else None)
                except Exception:  # noqa: BLE001
                    log.exception('review hook failed for seat %s', s)
            thoughts = list(rec.thoughts)
            days = list(rec.days)
            strategy = ''
            if extra is not None:
                thoughts.extend(extra.thoughts)
                thoughts.sort(key=lambda t: (t.day, t.event_id if t.event_id is not None else 10 ** 9))
                if extra.days:
                    days = list(extra.days)
                strategy = extra.strategy
            human = s == self.human_seat
            name = self.human_name if human else self.personas[s].name
            seats.append(ReviewSeat(seat=s, name=name, role=self.game.role_of(s), is_human=human,
                                    alive=self.game.is_alive(s), strategy=strategy, thoughts=thoughts, days=days))
        calls = {k: v - self._calls0.get(k, 0) for k, v in self.gateway.llm.ledger.calls_by_kind.items()}
        jev = sum(v for k, v in calls.items() if k.startswith('J'))
        stats = ReviewStats(seed=self.seed, days=res.day, calls=sum(calls.values()), jev_calls=jev,
                            claude_calls=sum(calls.values()) - jev,
                            spent_usd=round(self.gateway.spent() - self._spent0, 4),
                            speech_sources=dict(self._speech_tiers))
        return Review(winner=res.winner.value if res.winner else None, reason=res.reason, seats=seats,
                      events=self.game.events('judge'), stats=stats)

    async def _finish(self) -> None:
        self.review = self._build_review()
        await self._emit(self.review)
        await self._cost()
        await self._update_god()
        await self._deliver(self._state())
