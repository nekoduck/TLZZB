"""随机合法动作与随机对局：给测试和别的模块用。"""

from __future__ import annotations

from typing import get_args

from werewolf.kernel.engine import Game
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    EMOTES,
    Action,
    ActKind,
    GameConfig,
    Request,
    RequestKind,
    Role,
    Speech,
    SpeechAct,
    Tone,
    WolfStance,
)

_STANCES: tuple[str, ...] = get_args(WolfStance)


def random_act(rng: Rng, n_seats: int, day: int) -> SpeechAct:
    kind = rng.choice(list(ActKind))
    target = None if kind == ActKind.PASS else rng.randint(1, n_seats)
    kw: dict = {}
    if kind == ActKind.CLAIM_ROLE:
        kw['role'] = rng.choice(list(Role))
        target = None
    elif kind == ActKind.CLAIM_CHECK:
        kw['result'] = rng.choice(['wolf', 'good'])
        kw['night'] = rng.randint(1, max(1, day))
    elif kind in (ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON):
        kw['night'] = rng.randint(1, max(1, day))
    elif kind == ActKind.DEFEND and rng.chance(0.5):
        target = None
    return SpeechAct(kind=kind, target=target, weight=rng.randint(1, 3), **kw)


def random_speech(rng: Rng, seat: int, n_seats: int, day: int) -> Speech:
    acts = [random_act(rng, n_seats, day) for _ in range(rng.randint(0, 3))]
    emote = rng.choice([None, *EMOTES])
    return Speech(text=f'{seat}号的占位发言。', acts=acts, tone=rng.choice(list(Tone)), emote=emote)


def random_action(game: Game, request: Request, rng: Rng) -> Action:
    """随机合法动作；发言是带随机 acts 的占位文字。"""
    if request.kind == RequestKind.SPEAK:
        sp = random_speech(rng, request.seat, game.config.seats, request.day)
        return Action(request_id=request.id, seat=request.seat, speech=sp)
    opt = rng.choice(request.options)
    note = stance = None
    if request.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL):
        if request.context.get('first_night'):
            stance = rng.choice(_STANCES)
        if request.kind == RequestKind.WOLF_PROPOSE and rng.chance(0.3):
            note = f'刀{opt.target}号吧' if opt.target else '空刀吧'
    return Action(request_id=request.id, seat=request.seat, option_id=opt.id, note=note, stance=stance)  # type: ignore[arg-type]


def play_random(config: GameConfig, seed: int, max_steps: int = 5000) -> Game:
    """随机打一局（max_steps 是 submit 次数上限）。返回的对局通常已终局；超限则原样返回。"""
    game = Game.new(config, seed)
    rng = Rng(seed, 'autoplay')
    steps = 0
    while steps < max_steps:
        reqs = game.pending()
        if not reqs:
            break
        for req in reqs:
            game.submit(random_action(game, req, rng))
            steps += 1
    return game
