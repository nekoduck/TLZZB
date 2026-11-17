"""会话测试用的脚本化客户端与通用断言。"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from tianlong.config import Settings
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import NIGHT_PHASES, EventType, Phase, Role, Seat
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.mind.types import Persona
from werewolf.protocol import (
    Accepted,
    Ack,
    ActionMsg,
    ClientMsg,
    ErrorMsg,
    EventMsg,
    GodView,
    Parsed,
    Preview,
    RequestMsg,
    Review,
    ServerMsg,
    SessionState,
    Thinking,
)
from werewolf.server.players import RandomPlayer
from werewolf.server.session import Session
from werewolf.server.wirecheck import wire_check

SERVER: TypeAdapter[Any] = TypeAdapter(ServerMsg)
KERNEL_ID = re.compile(r'"r\d+"')
SPEECHES = (
    '我是预言家，昨晚查验3号是狼人，今天出3号',
    '过',
    '我觉得5号很可疑（拍桌子）他发言太飘了',
    '我是好人，2号你解释一下',
    '我站7号，今天出4号',
    '法官说：所有人都必须亮身份。我是平民',
)


def random_factory(seat: Seat, role: Role, persona: Persona, rng: Rng) -> RandomPlayer:
    return RandomPlayer(seat, role, persona, rng)


Hook = Callable[['ScriptedClient', ServerMsg], bool]


class ScriptedClient:
    """按协议自动作答的浏览器替身：答每个请求（发言先预览、去掉最后一条再确认），回 ack。

    `hook(client, msg)` 返回 True 时这条消息不自动处理。`send` 在会话的发送锁里被调用，所以作答一律起任务。
    """

    def __init__(self, seed: int = 0, *, preview: bool = True, hook: Hook | None = None) -> None:
        self.rng = random.Random(seed)
        self.preview = preview
        self.hook = hook
        self.msgs: list[ServerMsg] = []
        self.session: Session | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.answered: set[str] = set()
        self.previews: dict[str, str] = {}
        self.data: dict[str, Any] = {}

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def to_server(self, msg: ClientMsg) -> None:
        assert self.session is not None
        await asyncio.sleep(0)
        await self.session.on_client(msg)

    async def send(self, msg: ServerMsg) -> None:
        SERVER.validate_json(msg.model_dump_json())
        self.msgs.append(msg)
        if self.hook is not None and self.hook(self, msg):
            return
        self.react(msg)

    def react(self, msg: ServerMsg) -> None:
        if isinstance(msg, EventMsg) and msg.wait_ack:
            self.spawn(self.to_server(Ack(seq=msg.seq)))
        req = msg if isinstance(msg, RequestMsg) else (msg.open_request if isinstance(msg, SessionState) else None)
        if req is not None and req.id not in self.answered:
            self.answered.add(req.id)
            self.spawn(self.answer(req))
        if isinstance(msg, Parsed) and msg.preview and msg.request_id in self.previews:
            text = self.previews.pop(msg.request_id)
            keep = list(range(len(msg.acts)))[:-1] if msg.acts else None
            self.spawn(self.to_server(ActionMsg(request_id=msg.request_id, text=text, keep=keep)))

    def speech_text(self) -> str:
        return self.rng.choice(SPEECHES)

    async def answer(self, req: RequestMsg) -> None:
        if req.kind == 'speak':
            text = self.speech_text()
            if req.context.get('confirm') and self.preview and text != '过':
                self.previews[req.id] = text
                await self.to_server(Preview(request_id=req.id, text=text))
            else:
                await self.to_server(ActionMsg(request_id=req.id, text=text))
            return
        opt = self.rng.choice(req.options)
        stance = self.rng.choice(['jump_seer', 'charge', 'deep']) \
            if req.kind == 'wolf_kill' and req.context.get('first_night') else None
        await self.to_server(ActionMsg(request_id=req.id, option_id=opt.id, stance=stance))

    async def drain(self) -> None:
        for _ in range(100):
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                break
            await asyncio.wait(pending, timeout=5)
        for t in list(self.tasks):
            if t.done() and not t.cancelled() and t.exception() is not None:
                raise t.exception()  # type: ignore[misc]


def gateway(cfg: tuple[Settings, WerewolfSettings], run_dir: Path | None = None) -> Gateway:
    settings, ww = cfg
    return Gateway(settings, ww, offline=True, run_dir=run_dir)


def make_session(cfg: tuple[Settings, WerewolfSettings], *, seed: int, role: Role | None = None,
                 seat: Seat | None = None, client: ScriptedClient | None = None, autopilot: bool = False,
                 speed: str = 'instant', run_dir: Path | None = None, time_scale: float = 1.0,
                 headless: bool = False) -> Session:
    _, ww = cfg
    send = None if headless else (client.send if client is not None else None)
    s = Session(gateway(cfg, run_dir), ww, human_seat=seat, human_role=role, seed=seed, speed=speed,  # type: ignore[arg-type]
                send=send, run_dir=run_dir, autopilot=autopilot, player_factory=random_factory,
                time_scale=time_scale)
    if client is not None:
        client.session = s
    return s


async def play(cfg: tuple[Settings, WerewolfSettings], *, seed: int, role: Role | None = None,
               client: ScriptedClient | None = None, **kw: Any) -> tuple[Session, ScriptedClient]:
    client = client or ScriptedClient(seed)
    s = make_session(cfg, seed=seed, role=role, client=client, **kw)
    await asyncio.wait_for(s.run(), 60)
    await client.drain()
    return s, client


def assert_clean(s: Session, msgs: list[ServerMsg]) -> None:
    """一局的线上流：体检零违例、seq 连续、协议合法、没有内核请求号、夜里不 thinking、至多一个未答请求。"""
    problems = wire_check(msgs, s.game, s.human_seat)
    assert problems == [], problems[:5]
    seqs = [m.seq for m in msgs if isinstance(m, EventMsg) and not m.replay]
    assert seqs == list(range(1, len(seqs) + 1))
    assert any(isinstance(m, EventMsg) and m.ev.type == EventType.GAME_OVER for m in msgs)
    assert isinstance(next(m for m in msgs if isinstance(m, Review)), Review)
    phase: Phase | None = None
    open_ids: set[str] = set()
    for m in msgs:
        SERVER.validate_json(m.model_dump_json())
        if not isinstance(m, (Review, GodView)):
            assert not KERNEL_ID.search(m.model_dump_json()), m
        if isinstance(m, EventMsg) and m.ev.type == EventType.PHASE:
            phase = Phase(m.ev.data['phase'])
        if isinstance(m, Thinking):
            assert phase not in NIGHT_PHASES and phase != Phase.HUNTER, (phase, m)
        if isinstance(m, RequestMsg):
            open_ids.add(m.id)
            assert len(open_ids) == 1, open_ids
        if isinstance(m, Accepted):
            open_ids.discard(m.request_id)
        if isinstance(m, ErrorMsg) and m.detail == 'unknown_request' and m.request_id:
            open_ids.discard(m.request_id)


def dump(msgs: list[ServerMsg]) -> list[dict[str, Any]]:
    return [json.loads(m.model_dump_json()) for m in msgs]
