"""内核测试的小工具：固定身份开局、按脚本作答。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from werewolf.kernel.engine import Game
from werewolf.kernel.types import Action, Event, GameConfig, Request, RequestKind, Role, Seat, Speech
from werewolf.testing import DEFAULT_ROLES

# 狼 2、5、8；预言家 3；女巫 6；猎人 9；平民 1、4、7
ROLES: dict[Seat, Role] = dict(DEFAULT_ROLES)

Decide = Callable[[Request], 'str | Speech | None']


def new_game(roles: dict[Seat, Role] | None = None, seed: int = 1, **cfg: Any) -> Game:
    return Game.new(GameConfig(**cfg), seed, roles or ROLES)


def default_choice(req: Request) -> str | Speech:
    """默认：空刀、验第一个、不用药、不开枪、弃票、说一句占位话。"""
    if req.kind == RequestKind.SPEAK:
        return Speech(text=f'{req.seat}号发言。')
    ids = [o.id for o in req.options]
    for pref in ('kill:none', 'pass', 'shoot:none', 'vote:abstain'):
        if pref in ids:
            return pref
    return ids[0]


def action(req: Request, choice: str | Speech, **kw: Any) -> Action:
    if isinstance(choice, Speech):
        return Action(request_id=req.id, seat=req.seat, speech=choice, **kw)
    return Action(request_id=req.id, seat=req.seat, option_id=choice, **kw)


def play(game: Game, decide: Decide | None = None, until: Callable[[Game], bool] | None = None,
         max_steps: int = 2000) -> list[Event]:
    """按 decide 作答（返回 None 用默认），直到 until(game) 为真或终局。返回这期间新产生的事件。"""
    out: list[Event] = []
    for _ in range(max_steps):
        if game.over or (until is not None and until(game)):
            return out
        for req in game.pending():
            choice = decide(req) if decide else None
            out += game.submit(action(req, choice if choice is not None else default_choice(req)))
    raise AssertionError('play: too many steps')


def kinds(evs: list[Event]) -> list[str]:
    return [e.type.value if e.type != 'phase' else f'phase:{e.data["phase"]}' for e in evs]


def by_type(game: Game, type_: str) -> list[Event]:
    return [e for e in game.events('judge') if e.type == type_]


def waiting(kind: RequestKind, seat: Seat | None = None, day: int | None = None) -> Callable[[Game], bool]:
    def f(g: Game) -> bool:
        return any(r.kind == kind and (seat is None or r.seat == seat) and (day is None or r.day == day)
                   for r in g.pending())
    return f


def script(table: dict[tuple[str, int | None, Seat | None], Any]) -> Decide:
    """键 (请求种类, 天或 None, 座位或 None)；值是选项 id、Speech，或接收请求返回它们的函数。"""

    def decide(req: Request) -> str | Speech | None:
        k = req.kind.value
        for key in ((k, req.day, req.seat), (k, req.day, None), (k, None, req.seat), (k, None, None)):
            if key in table:
                v = table[key]
                return v(req) if callable(v) else v
        return None

    return decide


def vote_for(target: Seat) -> Callable[[Request], str | None]:
    """能投 target 就投，否则（target 自己）走默认弃票。"""
    def f(req: Request) -> str | None:
        oid = f'vote:{target}'
        return oid if any(o.id == oid for o in req.options) else None
    return f


def votes(mapping: dict[Seat, Seat | None]) -> Callable[[Request], str | None]:
    def f(req: Request) -> str | None:
        t = mapping.get(req.seat)
        return f'vote:{t}' if t is not None else 'vote:abstain'
    return f


def waiting_speech(kind: str, day: int | None = None) -> Callable[[Game], bool]:
    def f(g: Game) -> bool:
        return any(r.speech_kind == kind and (day is None or r.day == day) for r in g.pending())
    return f
