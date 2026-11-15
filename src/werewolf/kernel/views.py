"""视图：纯函数，只由配置的公开部分与（对该座位）可见的事件算出。发言事件一律去掉文字。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from werewolf.kernel.types import (
    Event,
    EventType,
    GameConfig,
    Phase,
    PlayerPublic,
    PublicView,
    Role,
    Seat,
    SeerCheck,
    View,
    WitchInfo,
    WolfInfo,
    strip_text,
    team_of,
)


def _clock(events: Sequence[Event]) -> tuple[int, Phase]:
    if not events:
        return 0, Phase.SETUP
    return events[-1].day, events[-1].phase


def _players(config: GameConfig, events: Sequence[Event]) -> list[PlayerPublic]:
    """座位卡只用公开事件：开局名单、死亡、猎人翻牌、终局亮牌。"""
    names = {s: config.names.get(s, f'{s}号') for s in range(1, config.seats + 1)}
    info: dict[Seat, dict[str, Any]] = {s: {'alive': True, 'died_day': None, 'died_when': None, 'revealed': None}
                                        for s in names}
    for ev in events:
        if ev.vis != 'public':
            continue
        if ev.type == EventType.GAME_START:
            for row in ev.data.get('names', []):
                names[row['seat']] = row['name']
        elif ev.type == EventType.DEATH and ev.target in info:
            info[ev.target].update(alive=False, died_day=ev.data['day'], died_when=ev.data['when'])
        elif ev.type == EventType.ROLE_REVEALED and ev.target in info:
            info[ev.target]['revealed'] = Role(ev.data['role'])
        elif ev.type == EventType.GAME_OVER:
            for row in ev.data.get('roles', []):
                info[row['seat']]['revealed'] = Role(row['role'])
    return [
        PlayerPublic(seat=s, name=names[s], alive=info[s]['alive'], is_human=(s == config.human_seat),
                     died_day=info[s]['died_day'], died_when=info[s]['died_when'],
                     revealed_role=info[s]['revealed'])
        for s in sorted(names)
    ]


def build_public_view(config: GameConfig, events: Sequence[Event]) -> PublicView:
    pub = [strip_text(e) for e in events if e.vis == 'public']
    day, phase = _clock(pub)
    return PublicView(day=day, phase=phase, players=_players(config, pub), events=pub)


def build_view(config: GameConfig, seat: Seat, events: Sequence[Event]) -> View:
    """`events` 是对 seat 可见的事件（带不带文字都行，这里会去掉）。"""
    evs = [strip_text(e) for e in events]
    day, phase = _clock(evs)
    players = _players(config, evs)
    role: Role | None = None
    wolves: list[Seat] = []
    checks: list[SeerCheck] = []
    saved: list[tuple[int, Seat]] = []
    poisoned: list[tuple[int, Seat]] = []
    kills: list[tuple[int, Seat | None]] = []
    notice: Event | None = None
    blocked = False
    for ev in evs:
        t = ev.type
        if t == EventType.ROLE_DEALT and ev.actor == seat:
            role = Role(ev.data['role'])
        elif t == EventType.WOLF_TEAM:
            wolves = list(ev.data['wolves'])
        elif t == EventType.SEER_RESULT and ev.actor == seat and ev.target is not None:
            checks.append(SeerCheck(night=ev.data['night'], target=ev.target, result=ev.data['result']))
        elif t == EventType.WITCH_SAVE and ev.actor == seat and ev.target is not None:
            saved.append((ev.day, ev.target))
        elif t == EventType.WITCH_POISON and ev.actor == seat and ev.target is not None:
            poisoned.append((ev.day, ev.target))
        elif t == EventType.WITCH_NOTICE:
            notice = ev
        elif t == EventType.WOLF_KILL:
            kills.append((ev.day, ev.target))
        elif t == EventType.HUNTER_BLOCKED and ev.target == seat:
            blocked = True
    if role is None:
        raise ValueError(f'build_view: no role_dealt for seat {seat}')
    known: dict[Seat, Role] = {p.seat: p.revealed_role for p in players if p.revealed_role is not None}
    if role == Role.WEREWOLF:
        known.update({s: Role.WEREWOLF for s in wolves})
    known[seat] = role
    witch: WitchInfo | None = None
    if role == Role.WITCH:
        antidote, poison = not saved, not poisoned
        victim = None
        if (antidote and notice is not None and phase == Phase.NIGHT_WITCH and notice.day == day):
            victim = notice.target
        witch = WitchInfo(antidote=antidote, poison=poison, tonight_victim=victim, saved=saved, poisoned=poisoned)
    alive = next(p.alive for p in players if p.seat == seat)
    return View(
        seat=seat, role=role, team=team_of(role), alive=alive, day=day, phase=phase, players=players,
        known_roles=dict(sorted(known.items())),
        seer_checks=checks if role == Role.SEER else [],
        witch=witch,
        wolf=WolfInfo(teammates=[s for s in wolves if s != seat], kills=kills) if role == Role.WEREWOLF else None,
        hunter_can_shoot=(not blocked) if role == Role.HUNTER else None,
        events=evs,
    )
