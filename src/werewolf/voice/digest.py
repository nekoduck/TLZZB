"""公开事实句：只从 acts 与其他公开事件渲染，从不引用任何人的原话（ARCHITECTURE 5）。

玩家一律称「N号」，不输出事件 id。发言带 injection / stage_direction 标记的，只加一句「已忽略」。
"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Event, EventType, PublicView, Role, Seat, SpeechAct
from werewolf.voice.canon import role_word

_KIND_LABEL = {'day': '', 'pk': 'PK发言时', 'last_words': '遗言里'}
_RESULT = {'wolf': '狼人', 'good': '好人'}


def _night(a: SpeechAct) -> str:
    return f'第{a.night}夜' if a.night else ''


def act_clause(a: SpeechAct, *, me: bool = False) -> str | None:
    """一条言语行为的白话。`me` 为真时主语是「我」，用于自己的记录（这里只管谓语）。"""
    t = f'{a.target}号' if a.target else ''
    k = a.kind
    if k == ActKind.CLAIM_ROLE and a.role:
        return f'自称{role_word(a.role)}'
    if k == ActKind.CLAIM_CHECK and a.target and a.result:
        return f'报{_night(a) or ""}验了{t}，是{_RESULT[a.result]}'
    if k == ActKind.CLAIM_SAVE and a.target:
        return f'称{_night(a)}救了{t}'
    if k == ActKind.CLAIM_POISON and a.target:
        return f'称{_night(a)}毒了{t}'
    if k == ActKind.ACCUSE and a.target:
        return f'怀疑{t}'
    if k == ActKind.VOUCH and a.target:
        return f'认{t}是好人'
    if k == ActKind.DEFEND:
        return f'替{t}辩护' if a.target else '替自己辩解'
    if k == ActKind.VOTE_INTENT and a.target:
        return f'表态要投{t}'
    if k == ActKind.BACK_CLAIM and a.target:
        return f'站{t}的边，信其身份'
    if k == ActKind.DOUBT_CLAIM and a.target:
        return f'质疑{t}的身份'
    if k == ActKind.QUESTION and a.target:
        return f'点名要{t}解释'
    if k == ActKind.PASS:
        return '选择过'
    return None


def _seat_list(seats: list[Seat]) -> str:
    return '、'.join(str(s) for s in seats)


def _speech(ev: Event) -> list[str]:
    sp = ev.data.get('speech') or {}
    who = f'{ev.actor}号'
    kind = ev.data.get('kind', 'day')
    clauses = []
    for raw in sp.get('acts') or []:
        c = act_clause(SpeechAct.model_validate(raw))
        if c:
            clauses.append(c)
    out: list[str] = []
    if clauses:
        out.append(f'第{ev.day}天，{who}{_KIND_LABEL.get(kind, "")}' + '，'.join(clauses))
    elif sp:
        out.append(f'第{ev.day}天，{who}{_KIND_LABEL.get(kind, "")}没有表态')
    if set(sp.get('flags') or []) & {'injection', 'stage_direction'}:
        out.append(f'{who}的话里夹了场外内容，已忽略')
    return out


def _vote_result(ev: Event) -> list[str]:
    d = ev.data
    rnd = 'PK' if d.get('round') == 'pk' else ''
    parts = []
    for row in d.get('tally') or []:
        parts.append(f'{_seat_list(sorted(row["voters"]))}投{row["target"]}号')
    if d.get('abstain'):
        parts.append(f'{_seat_list(sorted(d["abstain"]))}弃票')
    head = f'第{ev.day}天{rnd}投票：' + ('，'.join(parts) if parts else '无人投票')
    outcome = d.get('outcome')
    if outcome == 'exile' and ev.target:
        tail = f'{ev.target}号出局'
    elif outcome == 'pk':
        tail = f'{_seat_list(sorted(d.get("tied") or []))}号平票，进入PK'
    else:
        tail = '无人出局'
    return [f'{head}；{tail}']


def _event_sentences(ev: Event) -> list[str]:
    if ev.vis != 'public':
        return []
    t = ev.type
    if t == EventType.SPEECH:
        return _speech(ev)
    if t == EventType.DAWN:
        deaths = ev.data.get('deaths') or []
        if not deaths:
            return [f'第{ev.day}天，昨夜平安夜']
        return [f'第{ev.day}天，昨夜{_seat_list(sorted(deaths))}号出局']
    if t == EventType.VOTE_RESULT:
        return _vote_result(ev)
    if t == EventType.HUNTER_SHOT and ev.actor and ev.target:
        return [f'第{ev.day}天，{ev.actor}号开枪带走了{ev.target}号']
    if t == EventType.ROLE_REVEALED and ev.target and ev.data.get('role'):
        return [f'{ev.target}号翻出身份：{role_word(Role(ev.data["role"]))}']
    if t == EventType.GAME_OVER:
        w = ev.data.get('winner')
        return ['游戏结束，' + {'wolf': '狼人获胜', 'good': '好人获胜'}.get(w, '平局')]
    return []


def public_digest(pv: PublicView, limit: int = 12) -> list[str]:
    """最近的公开事实句，由旧到新，至多 limit 条。"""
    out: list[str] = []
    for ev in pv.events:
        out.extend(_event_sentences(ev))
    return out[-limit:] if limit > 0 else []


_OWN_KINDS = {
    ActKind.CLAIM_ROLE, ActKind.CLAIM_CHECK, ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON, ActKind.VOTE_INTENT,
    ActKind.ACCUSE, ActKind.VOUCH, ActKind.DEFEND, ActKind.BACK_CLAIM, ActKind.DOUBT_CLAIM,
}


def own_record(pv: PublicView, seat: Seat, limit: int = 10) -> list[str]:
    """该座位公开说过、做过的事：身份声明、查验与用药、表态、投过的票。不受 recent 窗口限制，由旧到新。"""
    out: list[str] = []
    for ev in pv.events:
        if ev.vis != 'public':
            continue
        if ev.type == EventType.SPEECH and ev.actor == seat:
            clauses = []
            for raw in (ev.data.get('speech') or {}).get('acts') or []:
                a = SpeechAct.model_validate(raw)
                if a.kind in _OWN_KINDS and (c := act_clause(a, me=True)):
                    clauses.append(c)
            if clauses:
                label = _KIND_LABEL.get(ev.data.get('kind', 'day'), '')
                out.append(f'第{ev.day}天，我{label}' + '，'.join(clauses))
        elif ev.type == EventType.VOTE and ev.actor == seat:
            rnd = 'PK' if ev.data.get('round') == 'pk' else ''
            what = f'投了{ev.target}号' if ev.target else '弃票'
            out.append(f'第{ev.day}天{rnd}投票，我{what}')
    return out[-limit:] if limit > 0 else []
