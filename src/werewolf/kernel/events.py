"""造事件的唯一地方。每种事件一个函数，data 的形状与可见性严格照 ARCHITECTURE 3.3 的表。

每个函数的第一个参数是 `Stamp`（编号、天、阶段），由引擎给出；这里不保存任何状态。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple

from werewolf.kernel.types import Event, EventType, Phase, Role, Seat, Speech, SpeechKind, strip_text

__all__ = [
    'Stamp', 'strip_text', 'game_start', 'role_dealt', 'wolf_team', 'phase', 'wolf_propose', 'wolf_kill',
    'seer_result', 'witch_notice', 'witch_save', 'witch_poison', 'dawn', 'death', 'death_cause',
    'hunter_blocked', 'hunter_shot', 'role_revealed', 'speech', 'vote', 'vote_result', 'exile', 'game_over',
]

After = Literal['night', 'vote']


class Stamp(NamedTuple):
    id: int
    day: int
    phase: Phase


def _ev(
    st: Stamp,
    type: EventType,
    *,
    actor: Seat | None = None,
    target: Seat | None = None,
    data: dict[str, Any] | None = None,
    vis: Literal['public', 'wolves', 'seats', 'judge'] = 'public',
    vis_seats: Sequence[Seat] = (),
) -> Event:
    return Event(id=st.id, day=st.day, phase=st.phase, type=type, actor=actor, target=target,
                 data=data or {}, vis=vis, vis_seats=list(vis_seats))


# ---- 发牌 ----


def game_start(st: Stamp, names: Mapping[Seat, str], roles: Sequence[Role]) -> Event:
    seats = sorted(names)
    return _ev(st, EventType.GAME_START, data={
        'seats': seats,
        'names': [{'seat': s, 'name': names[s]} for s in seats],
        'role_counts': {r.value: list(roles).count(r) for r in Role},
    })


def role_dealt(st: Stamp, seat: Seat, role: Role) -> Event:
    return _ev(st, EventType.ROLE_DEALT, actor=seat, data={'role': role.value}, vis='seats', vis_seats=[seat])


def wolf_team(st: Stamp, wolves: Sequence[Seat]) -> Event:
    return _ev(st, EventType.WOLF_TEAM, data={'wolves': sorted(wolves)}, vis='wolves')


# ---- 阶段 ----


def phase(
    st: Stamp,
    *,
    order: Sequence[Seat] | None = None,
    after: After | None = None,
    voters: Sequence[Seat] | None = None,
    candidates: Sequence[Seat] | None = None,
) -> Event:
    """`st.phase` 就是新进入的阶段。附加字段只按阶段给：见 3.3 表。"""
    p = st.phase
    data: dict[str, Any] = {'phase': p.value, 'day': st.day}
    if p in (Phase.DAY_SPEECH, Phase.PK_SPEECH, Phase.LAST_WORDS):
        data['order'] = list(order or [])
    if p in (Phase.HUNTER, Phase.LAST_WORDS):
        data['after'] = after
    if p in (Phase.DAY_VOTE, Phase.PK_VOTE):
        data['voters'] = list(voters or [])
    if p in (Phase.DAY_VOTE, Phase.PK_VOTE, Phase.PK_SPEECH):
        data['candidates'] = list(candidates or [])
    return _ev(st, EventType.PHASE, data=data)


# ---- 夜 ----


def wolf_propose(st: Stamp, wolf: Seat, option_id: str, target: Seat | None, note: str | None,
                 stance: str | None) -> Event:
    return _ev(st, EventType.WOLF_PROPOSE, actor=wolf, target=target,
               data={'option_id': option_id, 'note': note, 'stance': stance}, vis='wolves')


def wolf_kill(st: Stamp, target: Seat | None, by: Literal['leader', 'plurality', 'tiebreak'],
              tally: Mapping[str, int], stance: str | None) -> Event:
    return _ev(st, EventType.WOLF_KILL, target=target, data={'by': by, 'tally': dict(tally), 'stance': stance},
               vis='wolves')


def seer_result(st: Stamp, seer: Seat, target: Seat, result: Literal['wolf', 'good'], night: int) -> Event:
    return _ev(st, EventType.SEER_RESULT, actor=seer, target=target, data={'result': result, 'night': night},
               vis='seats', vis_seats=[seer])


def witch_notice(st: Stamp, witch: Seat, victim: Seat | None, antidote: bool, poison: bool) -> Event:
    """解药还在才告知刀口；用掉后 target 为空、victim_known 为假。"""
    return _ev(st, EventType.WITCH_NOTICE, target=victim if antidote else None,
               data={'antidote': antidote, 'poison': poison, 'victim_known': antidote},
               vis='seats', vis_seats=[witch])


def witch_save(st: Stamp, witch: Seat, target: Seat) -> Event:
    return _ev(st, EventType.WITCH_SAVE, actor=witch, target=target, vis='seats', vis_seats=[witch])


def witch_poison(st: Stamp, witch: Seat, target: Seat) -> Event:
    return _ev(st, EventType.WITCH_POISON, actor=witch, target=target, vis='seats', vis_seats=[witch])


# ---- 死亡与技能 ----


def dawn(st: Stamp, deaths: Sequence[Seat]) -> Event:
    d = sorted(deaths)
    return _ev(st, EventType.DAWN, data={'deaths': d, 'peaceful': not d})


def death(st: Stamp, seat: Seat, when: Literal['night', 'vote', 'shot']) -> Event:
    return _ev(st, EventType.DEATH, target=seat, data={'when': when, 'day': st.day})


def death_cause(st: Stamp, seat: Seat, cause: Literal['wolf', 'poison', 'vote', 'shot']) -> Event:
    return _ev(st, EventType.DEATH_CAUSE, target=seat, data={'cause': cause}, vis='judge')


def hunter_blocked(st: Stamp, hunter: Seat) -> Event:
    return _ev(st, EventType.HUNTER_BLOCKED, target=hunter, vis='seats', vis_seats=[hunter])


def hunter_shot(st: Stamp, hunter: Seat, target: Seat | None) -> Event:
    """开枪公开；不开只有猎人自己看得见。"""
    if target is None:
        return _ev(st, EventType.HUNTER_SHOT, actor=hunter, vis='seats', vis_seats=[hunter])
    return _ev(st, EventType.HUNTER_SHOT, actor=hunter, target=target)


def role_revealed(st: Stamp, seat: Seat, role: Role) -> Event:
    return _ev(st, EventType.ROLE_REVEALED, target=seat, data={'role': role.value})


# ---- 白天 ----


def speech(st: Stamp, seat: Seat, kind: SpeechKind, sp: Speech) -> Event:
    return _ev(st, EventType.SPEECH, actor=seat, data={'kind': kind, 'speech': sp.model_dump(mode='json')})


def vote(st: Stamp, voter: Seat, target: Seat | None, round: Literal['main', 'pk'], option_id: str) -> Event:
    return _ev(st, EventType.VOTE, actor=voter, target=target, data={'round': round, 'option_id': option_id})


def vote_result(
    st: Stamp,
    round: Literal['main', 'pk'],
    tally: Sequence[Mapping[str, Any]],
    abstain: Sequence[Seat],
    outcome: Literal['exile', 'pk', 'none'],
    reason: Literal['top', 'tie', 'pk_tie', 'all_abstain'],
    tied: Sequence[Seat],
    exiled: Seat | None,
) -> Event:
    return _ev(st, EventType.VOTE_RESULT, target=exiled, data={
        'round': round,
        'tally': [{'target': row['target'], 'voters': list(row['voters'])} for row in tally],
        'abstain': list(abstain),
        'outcome': outcome,
        'reason': reason,
        'tied': list(tied),
    })


def exile(st: Stamp, seat: Seat) -> Event:
    return _ev(st, EventType.EXILE, target=seat)


def game_over(st: Stamp, winner: str | None, reason: str, roles: Mapping[Seat, Role]) -> Event:
    return _ev(st, EventType.GAME_OVER, data={
        'winner': winner,
        'reason': reason,
        'roles': [{'seat': s, 'role': roles[s].value} for s in sorted(roles)],
    })
