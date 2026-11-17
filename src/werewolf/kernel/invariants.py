"""内核不变量（ARCHITECTURE 3.4）。`check(game)` 返回违例描述，空表示没有；测试里每一步都跑。

「公开序列与身份无关」要两局对照，不在这里查，见 tests/ww/kernel 的专门测试。
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from werewolf.kernel.types import Event, EventType, RequestKind, Role, strip_text
from werewolf.kernel.views import build_view

if TYPE_CHECKING:
    from werewolf.kernel.engine import Game

FORBIDDEN_KEYS = frozenset({'truthful', 'motive', 'reason', 'evidence', 'fuzzy', 'thought', 'utility'})
# 契约 3.3 规定的 data 顶层 `reason`（票的结果、终局原因）不算私货
_ALLOWED_TOP_REASON = frozenset({EventType.VOTE_RESULT, EventType.GAME_OVER})


def _bad_keys(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                found.add(k)
            found |= _bad_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _bad_keys(v)
    return found


def _smuggled(ev: Event) -> set[str]:
    data = ev.model_dump(mode='json')
    keys = set()
    for k, v in data['data'].items():
        if k in FORBIDDEN_KEYS and not (k == 'reason' and ev.type in _ALLOWED_TOP_REASON):
            keys.add(k)
        keys |= _bad_keys(v)
    return keys


def check(game: Game, *, views: bool = True) -> list[str]:
    """`views=False` 跳过逐座位重算视图（最贵的一项），供大批量终局检查用。"""
    errs: list[str] = []
    cfg = game.config
    evs = game.events('judge')
    roles = {s: game.role_of(s) for s in range(1, cfg.seats + 1)}

    # ---- 身份、编号、终局、JSON 往返 ----
    if Counter(roles.values()) != Counter(cfg.roles):
        errs.append('role multiset differs from config')
    for i, ev in enumerate(evs, 1):
        if ev.id != i:
            errs.append(f'event id {ev.id} at position {i}')
            break
    overs = [i for i, e in enumerate(evs) if e.type == EventType.GAME_OVER]
    if overs:
        if len(overs) > 1 or overs[0] != len(evs) - 1:
            errs.append('events after game_over')
        if game.pending():
            errs.append('requests pending after game_over')
        if not game.over:
            errs.append('game_over event but game not over')
    elif game.over:
        errs.append('game over without game_over event')
    elif not game.pending():
        errs.append('game stalled: no pending requests and not over')
    cache: dict[int, int] = game.__dict__.setdefault('_inv_json_ok', {})
    for ev in evs:
        if cache.get(ev.id) == id(ev):
            continue
        if Event.model_validate_json(ev.model_dump_json()) != ev:
            errs.append(f'event {ev.id} fails JSON round trip')
            continue
        bad = _smuggled(ev)
        if bad:
            errs.append(f'event {ev.id} carries private keys {sorted(bad)}')
            continue
        cache[ev.id] = id(ev)

    # ---- 死亡、行动者、药、查验、投票 ----
    alive = set(roles)
    deaths: Counter[int] = Counter()
    causes: Counter[int] = Counter()
    antidote_nights: list[int] = []
    poison_nights: list[int] = []
    checks_by_night: Counter[int] = Counter()
    checked: set[int] = set()
    exiles_by_day: Counter[int] = Counter()
    voters: set[int] = set()
    for ev in evs:
        t = ev.type
        if t == EventType.DEATH:
            deaths[ev.target] += 1  # type: ignore[index]
            alive.discard(ev.target)  # type: ignore[arg-type]
        elif t == EventType.DEATH_CAUSE:
            causes[ev.target] += 1  # type: ignore[index]
        elif t in (EventType.WOLF_PROPOSE, EventType.SEER_RESULT, EventType.WITCH_SAVE, EventType.WITCH_POISON):
            if ev.actor not in alive:
                errs.append(f'event {ev.id}: dead seat {ev.actor} acted at night')
            if t == EventType.WOLF_PROPOSE and roles.get(ev.actor) != Role.WEREWOLF:  # type: ignore[arg-type]
                errs.append(f'event {ev.id}: non-wolf proposed a kill')
        if t == EventType.WITCH_SAVE:
            antidote_nights.append(ev.day)
        elif t == EventType.WITCH_POISON:
            poison_nights.append(ev.day)
            if ev.target == ev.actor:
                errs.append(f'event {ev.id}: witch poisoned herself')
        elif t == EventType.SEER_RESULT:
            checks_by_night[ev.day] += 1
            if ev.target in checked:
                errs.append(f'event {ev.id}: seer re-checked {ev.target}')
            if ev.target == ev.actor:
                errs.append(f'event {ev.id}: seer checked self')
            checked.add(ev.target)  # type: ignore[arg-type]
            expect = 'wolf' if roles.get(ev.target) == Role.WEREWOLF else 'good'  # type: ignore[arg-type]
            if ev.data.get('result') != expect:
                errs.append(f'event {ev.id}: wrong seer result')
        elif t == EventType.EXILE:
            exiles_by_day[ev.day] += 1
        elif t == EventType.PHASE and 'voters' in ev.data:
            voters = set(ev.data['voters'])
        elif t == EventType.VOTE:
            if ev.actor not in alive:
                errs.append(f'event {ev.id}: dead seat {ev.actor} voted')
            if ev.actor not in voters:
                errs.append(f'event {ev.id}: seat {ev.actor} voted without the right')
            if ev.target is not None and ev.target == ev.actor:
                errs.append(f'event {ev.id}: seat {ev.actor} voted for self')
    for s in roles:
        dead = not game.is_alive(s)
        if dead != (s not in alive):
            errs.append(f'seat {s}: is_alive disagrees with death events')
        want = 1 if dead else 0
        if deaths[s] != want or causes[s] != want:
            errs.append(f'seat {s}: {deaths[s]} death / {causes[s]} death_cause events (dead={dead})')
    if len(antidote_nights) > 1 or len(poison_nights) > 1:
        errs.append('a potion was used more than once')
    if set(antidote_nights) & set(poison_nights):
        errs.append('both potions used in one night')
    if any(n > 1 for n in checks_by_night.values()):
        errs.append('seer checked twice in one night')
    if any(n > 1 for n in exiles_by_day.values()):
        errs.append('more than one exile in a day')

    # ---- 请求 ----
    for req in game.pending():
        if req.kind != RequestKind.SPEAK and len(req.options) < 2:
            errs.append(f'request {req.id} has fewer than two options')
        if not game.is_alive(req.seat) and req.kind not in (RequestKind.SPEAK, RequestKind.HUNTER_SHOOT):
            errs.append(f'request {req.id} ({req.kind}) sent to dead seat {req.seat}')
        if req.kind == RequestKind.VOTE and any(o.target == req.seat for o in req.options):
            errs.append(f'request {req.id} lets seat {req.seat} vote for self')

    # ---- 视图不泄露 ----
    if views:
        wolves = {s for s, r in roles.items() if r == Role.WEREWOLF}
        stripped = [strip_text(e) for e in evs]
        for s in roles:
            v = game.view(s)
            if v != build_view(cfg, s, [e for e in stripped if game.visible(e, s)]):
                errs.append(f'seat {s}: view differs from build_view of visible events')
            allowed = {s} | {p.seat for p in v.players if p.revealed_role is not None}
            if roles[s] == Role.WEREWOLF:
                allowed |= wolves
            if not set(v.known_roles) <= allowed:
                errs.append(f'seat {s}: known_roles leaks {sorted(set(v.known_roles) - allowed)}')
            if any(k in v.known_roles and v.known_roles[k] != roles[k] for k in v.known_roles):
                errs.append(f'seat {s}: known_roles has a wrong role')
            for e in v.events:
                if e.type == EventType.SPEECH and e.data.get('speech', {}).get('text'):
                    errs.append(f'seat {s}: speech event {e.id} in view has text')
                    break
                if not game.visible(e, s):
                    errs.append(f'seat {s}: invisible event {e.id} in view')
                    break
    return errs
