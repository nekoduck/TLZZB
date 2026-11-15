"""Jev 的状态文本（ARCHITECTURE 6.2）。

小节固定：【我是谁】【此刻心情】【交情】【想起的事】【昨夜的想法】【场上】。
中文短句；座位一律称「N号」；绝不标谁是真人；不含任何人的原话（视图里本来就没有）。
状态只放与决定有关的东西：Jev 的弱点是无关上下文会拉低准确率。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from werewolf.kernel.types import PlayerPublic, PublicView, Role, Seat, View
from werewolf.mind.types import MindSnapshot, Persona

ROLE_ZH = {Role.WEREWOLF: '狼人', Role.VILLAGER: '平民', Role.SEER: '预言家', Role.WITCH: '女巫',
           Role.HUNTER: '猎人'}
STATE_ZH = {'steady': '平稳', 'confident': '自信', 'tense': '紧张', 'tilted': '破防'}
PHASE_ZH = {
    'setup': '发牌', 'night_wolf': '狼人行动', 'night_seer': '预言家行动', 'night_witch': '女巫行动', 'dawn': '天亮',
    'hunter': '技能环节', 'last_words': '遗言', 'day_speech': '白天发言', 'day_vote': '投票', 'pk_speech': 'PK 发言',
    'pk_vote': 'PK 投票', 'game_over': '终局',
}
STRATEGY_ZH = {'jump_seer': '悍跳预言家', 'charge': '冲锋，力挺悍跳的队友、攻击真预言家', 'deep': '深水，装平民',
               'hide_god': '藏着神职身份', 'open_god': '亮出神职身份'}
DEATH_ZH = {'night': '夜里死亡', 'vote': '被放逐', 'shot': '被猎人带走'}
RESULT_ZH = {'wolf': '狼人', 'good': '好人'}


def _seats(seats: Sequence[Seat]) -> str:
    return '、'.join(f'{s}号' for s in seats) if seats else '无'


def _trust_word(v: float) -> str:
    if v <= -0.7:
        return '很记仇'
    if v <= -0.3:
        return '有些不满'
    if v >= 0.7:
        return '很感激'
    return '有些好感'


def _dead_line(players: Sequence[PlayerPublic]) -> str:
    parts = []
    for p in players:
        if not p.alive:
            how = DEATH_ZH.get(p.died_when or '', '出局')
            day = f'第{p.died_day}天' if p.died_day else ''
            parts.append(f'{p.seat}号（{day}{how}）')
    return '、'.join(parts) if parts else '无'


def _suspicion_line(susp: Mapping[Seat, float], living: Sequence[Seat], me: Seat | None) -> str:
    items = [(s, p) for s, p in susp.items() if s in living and s != me]
    items.sort(key=lambda kv: -kv[1])
    return '、'.join(f'{s}号{round(p * 100)}%' for s, p in items) if items else '没有判断'


def _identity(view: View, persona: Persona, snap: MindSnapshot) -> str:
    me = view.seat
    lines = [f'你是{me}号，{persona.name}，{persona.title}。你的身份是{ROLE_ZH[view.role]}。']
    if persona.tags:
        lines.append('你的脾气：' + '、'.join(persona.tags) + '。')
    if persona.choices:
        lines.append('你的取舍：' + persona.choices[0])
    if view.wolf is not None:
        alive = [t for t in view.wolf.teammates if t in view.living()]
        lines.append(f'你的狼队友：{_seats(view.wolf.teammates)}（还活着：{_seats(alive)}）。')
        if view.wolf.kills:
            lines.append('狼队的刀口：' + '、'.join(
                f'第{n}夜{"空刀" if t is None else f"{t}号"}' for n, t in view.wolf.kills) + '。')
    if view.seer_checks:
        lines.append('你验过：' + '；'.join(
            f'第{c.night}夜 {c.target}号是{RESULT_ZH[c.result]}' for c in view.seer_checks) + '。')
    if view.witch is not None:
        w = view.witch
        lines.append(f'解药{"还在" if w.antidote else "已用"}，毒药{"还在" if w.poison else "已用"}。')
        if w.tonight_victim is not None:
            lines.append(f'今夜被刀的是{w.tonight_victim}号。')
        if w.saved:
            lines.append('你救过：' + '、'.join(f'第{n}夜{s}号' for n, s in w.saved) + '。')
        if w.poisoned:
            lines.append('你毒过：' + '、'.join(f'第{n}夜{s}号' for n, s in w.poisoned) + '。')
    if view.hunter_can_shoot is not None:
        lines.append('你死时可以开枪。' if view.hunter_can_shoot else '你现在开不了枪。')
    revealed = {s: r for s, r in view.known_roles.items() if s != me and (view.wolf is None or s not in view.wolf.teammates)}
    if revealed:
        lines.append('已公开的身份：' + '、'.join(f'{s}号是{ROLE_ZH[r]}' for s, r in sorted(revealed.items())) + '。')
    if snap.strategy:
        lines.append('你的打法：' + STRATEGY_ZH.get(snap.strategy, snap.strategy) + '。')
    if snap.pack:
        lines.append('狼队分工：' + '、'.join(f'{s}号{STRATEGY_ZH.get(v, v).split("，")[0]}'
                                           for s, v in sorted(snap.pack.items())) + '。')
    return '\n'.join(lines)


def _mood(snap: MindSnapshot) -> str:
    out = [f'{STATE_ZH.get(snap.state, snap.state)}，心情{round(snap.mood)}/100，压力{round(snap.stress)}/100。']
    live = [t for t in snap.thoughts if t.expires_day is None or t.expires_day >= snap.day]
    for t in live[:3]:
        out.append(f'- {t.text}')
    return '\n'.join(out)


def _trust(snap: MindSnapshot) -> str:
    items = sorted(((s, v) for s, v in snap.trust.items() if abs(v) >= 0.3), key=lambda kv: -abs(kv[1]))
    if not items:
        return '没有特别的交情。'
    return '\n'.join(f'对{s}号：{_trust_word(v)}。' for s, v in items[:5])


def _memories(snap: MindSnapshot) -> str:
    if not snap.memories:
        return '没有特别想起的事。'
    return '\n'.join(f'- {m.text}' for m in snap.memories[:4])


def _board(day: int, phase: str, players: Sequence[PlayerPublic], susp: Mapping[Seat, float], me: Seat | None,
           digest: Sequence[str], snap: MindSnapshot | None, label: str) -> str:
    living = [p.seat for p in players if p.alive]
    lines = [f'第{day}天，{PHASE_ZH.get(phase, phase)}。', f'存活：{_seats(living)}。', f'已出局：{_dead_line(players)}。',
             f'{label}：{_suspicion_line(susp, living, me)}。']
    if snap is not None:
        if snap.focus is not None:
            lines.append(f'眼下最在意：{snap.focus}号。')
        if snap.vote_intent is not None:
            lines.append(f'今天你说过要投：{snap.vote_intent}号。')
    if digest:
        lines.append('最近发生的事：')
        lines.extend(f'- {d}' for d in digest)
    return '\n'.join(lines)


def decision_state(view: View, snap: MindSnapshot, persona: Persona, digest: Sequence[str]) -> str:
    """做决定用的状态文本（含底牌，只给 Jev）。"""
    sections = [
        ('我是谁', _identity(view, persona, snap)),
        ('此刻心情', _mood(snap)),
        ('交情', _trust(snap)),
        ('想起的事', _memories(snap)),
        ('昨夜的想法', snap.last_thought or '没有。'),
        ('场上', _board(view.day, view.phase.value, view.players, snap.suspicion, view.seat, digest, snap,
                       '你对各人是狼的怀疑')),
    ]
    return '\n'.join(f'【{k}】\n{v}' for k, v in sections)


def public_state(pv: PublicView, snap_public: Mapping[Seat, float], persona: Persona, digest: Sequence[str],
                 *, seat: Seat | None = None) -> str:
    """狼人反思用的状态文本：只有公开局面与公开心智的判断，没有身份。"""
    who = f'你是{seat}号，' if seat is not None else '你是'
    sections = [
        ('我是谁', f'{who}{persona.name}，{persona.title}。' + (f'你的脾气：{"、".join(persona.tags)}。'
                                                          if persona.tags else '')),
        ('场上', _board(pv.day, pv.phase.value, pv.players, snap_public, seat, digest, None,
                       '你对各人是狼的怀疑')),
    ]
    return '\n'.join(f'【{k}】\n{v}' for k, v in sections)
