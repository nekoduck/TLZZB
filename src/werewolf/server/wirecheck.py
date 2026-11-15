"""对一局发给浏览器的消息做体检（ARCHITECTURE 第 8 节 `wire_check`）。

终局与上帝视角之前：
- 每条 `event` 恰好是真人可见的下一条内核事件（`ev.id` 改写成 `seq`），即只发看得见的、顺序与内核一致；
- 任何消息里都没有别人的身份（公开翻出的、真人狼的队友除外），没有整份身份表；
另外全程：
- `seq` 从 1 起连续，`ev.id == seq`；`reaction` / `private` / `parsed` 引用的 seq 都已发过；
- 请求号不是内核的 `r<数字>`；
- 夜间环节与技能环节里不发 `thinking`；
- 同一时刻至多一个未答的请求。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from werewolf.kernel.engine import Game
from werewolf.kernel.types import NIGHT_PHASES, EventType, Phase, Role, Seat
from werewolf.protocol import (
    Accepted,
    ErrorMsg,
    EventMsg,
    GameStarted,
    GodView,
    Parsed,
    PrivateInfo,
    ReactionMsg,
    RequestMsg,
    Review,
    ServerMsg,
    SessionState,
    Thinking,
)

_KERNEL_REQ = re.compile(r'^r\d+$')
_ROLE_VALUES = {r.value for r in Role}
_SEAT_KEYS = ('seat', 'actor', 'target')


def _role_leaks(obj: Any, allowed: set[Seat], path: str, out: list[str]) -> None:
    """在 JSON 树里找「某个座位 + 身份」的组合，或整份身份表。"""
    if isinstance(obj, dict):
        if 'roles' in obj and isinstance(obj['roles'], list) and obj['roles']:
            out.append(f'{path}: 整份身份表')
        role = obj.get('role')
        if isinstance(role, str) and role in _ROLE_VALUES:
            for k in _SEAT_KEYS:
                s = obj.get(k)
                if isinstance(s, int) and not isinstance(s, bool) and s not in allowed:
                    if obj.get('kind') == 'claim_role':  # 言语行为里的自称不是事实
                        continue
                    out.append(f'{path}: {k}={s} 的身份 {role}')
        for k, v in obj.items():
            _role_leaks(v, allowed, f'{path}.{k}', out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _role_leaks(v, allowed, f'{path}[{i}]', out)


def wire_check(msgs: Sequence[ServerMsg], game: Game, human_seat: Seat) -> list[str]:
    problems: list[str] = []
    visible = game.events(human_seat)
    human_role = game.role_of(human_seat)
    wolves = {s for s in range(1, game.config.seats + 1) if game.role_of(s) == Role.WEREWOLF}
    allowed: set[Seat] = {human_seat}
    if human_role == Role.WEREWOLF:
        allowed |= wolves
    sent: set[int] = set()
    next_seq = 1
    vis_i = 0
    unrestricted = False  # 终局或上帝视角之后
    phase: Phase | None = None
    open_req: str | None = None

    for i, m in enumerate(msgs):
        where = f'#{i} {m.t}'
        if isinstance(m, GameStarted) and (m.you != human_seat or m.your_role != human_role):
            problems.append(f'{where}: 座位或身份不是真人的（{m.you} 号 {m.your_role}）')
        if isinstance(m, GodView):
            unrestricted = True
        if isinstance(m, Review):
            unrestricted = True
        if isinstance(m, EventMsg) and not m.replay:
            if m.seq != next_seq:
                problems.append(f'{where}: seq {m.seq}，应为 {next_seq}')
            next_seq = m.seq + 1
            if m.ev.id != m.seq:
                problems.append(f'{where}: ev.id {m.ev.id} != seq {m.seq}')
            sent.add(m.seq)
            if m.ev.type == EventType.PHASE:
                phase = Phase(m.ev.data.get('phase', m.ev.phase))
            if m.ev.type == EventType.ROLE_REVEALED and m.ev.target is not None:
                allowed.add(m.ev.target)
            if not unrestricted:
                if vis_i >= len(visible):
                    problems.append(f'{where}: 多出来的事件 {m.ev.type}')
                else:
                    want = visible[vis_i].model_copy(update={'id': m.seq})
                    if m.ev != want:
                        problems.append(f'{where}: 与真人可见的第 {vis_i + 1} 条内核事件不符（{m.ev.type} / {want.type}）')
                    vis_i += 1
            if m.ev.type == EventType.GAME_OVER:
                unrestricted = True
                continue
        if isinstance(m, (ReactionMsg, PrivateInfo, Parsed)) and not getattr(m, 'replay', False):
            seq = m.seq
            if seq is not None and seq not in sent:
                problems.append(f'{where}: 引用了未发过的 seq {seq}')
        if isinstance(m, RequestMsg):
            if _KERNEL_REQ.match(m.id):
                problems.append(f'{where}: 请求号 {m.id} 像内核编号')
            if open_req is not None and open_req != m.id:
                problems.append(f'{where}: 请求 {open_req} 未答就发了 {m.id}')
            open_req = m.id
        if isinstance(m, SessionState) and m.open_request is not None and _KERNEL_REQ.match(m.open_request.id):
            problems.append(f'{where}: 请求号 {m.open_request.id} 像内核编号')
        if isinstance(m, Accepted) and m.request_id == open_req:
            open_req = None
        if isinstance(m, ErrorMsg) and m.detail == 'unknown_request' and m.request_id == open_req:
            open_req = None
        if isinstance(m, Thinking) and m.on and phase is not None and (phase in NIGHT_PHASES or phase == Phase.HUNTER):
            problems.append(f'{where}: 夜间或技能环节发了 thinking（{phase}）')
        if not unrestricted and not isinstance(m, (GodView, Review)):
            leaks: list[str] = []
            _role_leaks(m.model_dump(mode='json'), allowed, m.t, leaks)
            problems.extend(f'{where}: {x}' for x in leaks)
    return problems
