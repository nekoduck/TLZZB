"""验收测试的对局装置：会话层 + NpcAgent（离线或替身模型），真人座位由确定的客户端作答。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from tianlong.config import Settings
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import Action, Event, GameConfig, Request, RequestKind, Role, Seat, View
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.mind.types import Decision, Persona
from werewolf.server.npc import NpcAgent
from werewolf.server.session import Session

from .fakes import AutoClient


class ProbeNpc(NpcAgent):
    """NpcAgent，外加：每天主投票时记下私有怀疑度与账本里每个座位的自称身份。"""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.vote_susp: dict[int, dict[Seat, float]] = {}
        self.vote_roles: dict[int, dict[Seat, Role | None]] = {}

    async def act(self, request: Request, view: View) -> tuple[Action, Decision]:
        if request.kind == RequestKind.VOTE and request.context.get('round', 'main') == 'main':
            self.vote_susp[request.day] = dict(self.mind.beliefs.all('private'))
            self.vote_roles[request.day] = {s: self.mind.ledger.current_role(s) for s in range(1, 10)}
        return await super().act(request, view)


def probe_factory(gw: Gateway, ww: WerewolfSettings, human_seat: Seat | None,
                  **kw: Any) -> Callable[[Seat, Role, Persona, Rng], ProbeNpc]:
    cfg = GameConfig(human_seat=human_seat)

    def make(seat: Seat, role: Role, persona: Persona, rng: Rng) -> ProbeNpc:
        return ProbeNpc(seat, role, persona, rng, gw, ww, cfg, **kw)

    return make


async def run_game(cfg: tuple[Settings, WerewolfSettings], *, seed: int, seat: Seat | None = None,
                   role: Role | None = None, client: AutoClient | None = None, gateway: Gateway | None = None,
                   autopilot: bool = False, human_name: str = '你', run_dir: Any = None,
                   timeout: float = 60) -> tuple[Session, AutoClient | None]:
    settings, ww = cfg
    gw = gateway or Gateway(settings, ww, offline=True)
    hs = seat if seat is not None else Rng(seed, 'session').fork('seat').randint(1, 9)
    send = client.send if client is not None else None
    s = Session(gw, gw.ww, human_seat=hs, human_role=role, seed=seed, speed='instant', send=send,
                run_dir=run_dir, autopilot=autopilot, player_factory=probe_factory(gw, gw.ww, hs),
                human_name=human_name)
    if client is not None:
        client.session = s
    await asyncio.wait_for(s.run(), timeout)
    if client is not None:
        await client.drain()
    for p in s.players.values():
        if isinstance(p, NpcAgent):
            await p.aclose()
    return s, client


def kernel_trace(events: list[Event]) -> list[tuple[Any, ...]]:
    """去掉发言文字与标记之后的事件序列（言语行为、票、死亡……都还在）。"""
    out = []
    for ev in events:
        data = dict(ev.data)
        if 'speech' in data:
            sp = dict(data['speech'])
            sp.pop('flags', None)
            sp.pop('text', None)
            data['speech'] = sp
        out.append((ev.id, ev.type.value, ev.actor, ev.target, repr(sorted(data.items()))))
    return out
