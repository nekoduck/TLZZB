"""台词核对（ARCHITECTURE 6.2「核对」）。

`check_rules` 是确定性的：出戏、露底、超长、文字与计划对不上、提到计划之外的座位。
`check_speech` 在规则之后再让 Jev 回答三道是非题，任一概率达到 `check.block` 即不过。

本文件只见 `SpeechBrief`（公开族）与文字，不碰任何带底牌的类型。
"""

from __future__ import annotations

import re

from tianlong.llm.errors import LLMError
from werewolf.kernel.types import ActKind, Role, Seat
from werewolf.llm.gateway import Gateway
from werewolf.mind.types import SpeechBrief
from werewolf.voice import canon

JEV_KIND = 'J5_check'

_OOC = re.compile(r'作为\s*(?:一个|一名)?\s*(?:ai|人工智能|语言模型|大模型|机器人|程序)|语言模型|大模型|人工智能|提示词|系统提示|'
                  r'我是\s*(?:一个)?\s*(?:ai|机器人|程序|npc)|\bai\s*(?:助手|模型)|prompt|\bnpc\b|角色设定', re.I)
_LEAK = re.compile(r'我们狼|我们是狼|我们狼人|咱们狼|狼队友|狼同伴|狼队|我的队友|自家狼|我们昨晚(?:刀|杀|商量)|'
                   r'我们(?:刀|杀)了|昨晚我们(?:刀|杀|商量)|刀口|我是狼|我就是狼|我是个狼|我们三狼|三狼|'
                   r'(?:我|咱|我们)(?:这边|们)?(?:狼人|狼)(?:阵营|团队)|队友(?:是|在)狼')
# 「我不是狼」「别说我是狼」「6号报我是狼」（转述别人对我的指认）不算露底
_NEG_LEAK = re.compile(r'(?:不是|不像|别说|说我|报我|验我|查我|认我|认为我|怀疑我|当我|把我|踩我)[^，,。]{0,3}(?:狼|狼人)')


def _allowed_seats(brief: SpeechBrief) -> set[Seat]:
    ok: set[Seat] = {brief.seat, *brief.addressed, *brief.situation.rivals}
    for p in brief.points:
        if p.act.target is not None:
            ok.add(p.act.target)
        ok.update(canon.seats_in(p.text))
        if p.reason:
            ok.update(canon.seats_in(p.reason))
    for line in brief.own_record:
        ok.update(canon.seats_in(line))
    for h in brief.mood.hints:
        ok.update(canon.seats_in(h))
    return ok


def _fmt_act(a: object) -> str:
    kind = a.kind.value  # type: ignore[attr-defined]
    parts = [kind]
    if getattr(a, 'role', None) is not None:
        parts.append(a.role.value)  # type: ignore[attr-defined]
    if getattr(a, 'target', None) is not None:
        parts.append(f'{a.target}号')  # type: ignore[attr-defined]
    if getattr(a, 'result', None) is not None:
        parts.append(str(a.result))  # type: ignore[attr-defined]
    return ':'.join(parts)


def check_rules(text: str, brief: SpeechBrief) -> list[str]:
    """违例列表，空 = 通过。"""
    bad: list[str] = []
    t = (text or '').strip()
    if not t:
        return ['empty']
    if _OOC.search(t):
        bad.append('out_of_character')
    for leak in _LEAK.finditer(t):
        if not _NEG_LEAK.search(t[max(0, leak.start() - 3): leak.end()]):
            bad.append(f'leak_phrase:{leak.group(0)}')
            break
    if len(t) > brief.max_chars:
        bad.append(f'too_long:{len(t)}>{brief.max_chars}')
    planned = [p.act for p in brief.points]
    extra, missing = canon.claim_diff(t, planned, brief.day)
    bad += [f'off_plan:{_fmt_act(a)}' for a in extra]
    bad += [f'missing:{_fmt_act(a)}' for a in missing]
    # 自称是狼人：不管计划（计划不会这么写）
    for a in canon.claims_in(t, brief.day):
        if a.kind == ActKind.CLAIM_ROLE and a.role == Role.WEREWOLF and 'off_plan:claim_role:werewolf' not in bad:
            bad.append('off_plan:claim_role:werewolf')
    ok = _allowed_seats(brief)
    stray = [s for s in canon.seats_in(t) if s not in ok]
    if stray:
        bad.append('seat_not_in_plan:' + ','.join(f'{s}号' for s in stray))
    return bad


# --------------------------------------------------------------------------- Jev 三道是非题


def _jev_state(text: str, brief: SpeechBrief) -> str:
    cover = '没有公开过身份' if brief.cover_role is None else f'对外说自己是{canon.role_word(brief.cover_role)}'
    pts = '；'.join(p.text for p in brief.points) or '（没有要点）'
    safe = text.replace('>>>', '＞＞＞')
    return (
        f'【发言者】{brief.seat}号。{cover}。\n'
        f'【这次允许说的要点】{pts}\n'
        f'【要核对的台词】\n<<<\n{safe}\n>>>'
    )


_QUESTIONS = {
    'reveals_wolf': {'type': 'noul', 'instructions': '这段台词让人直接看出发言者是狼人阵营的人：提到队友、自己人、'
                     '「我们」商量过夜里的事，或者承认自己在骗人。'},
    'wrong_role': {'type': 'noul', 'instructions': '这段台词里发言者自称的游戏牌面身份（预言家、女巫、猎人、平民、狼人），不同于【发言者】一行写的对外身份，'
                   '也不在【这次允许说的要点】里。现实里的职业不算，要点里已有的身份声明不算。'},
    'night_leak': {'type': 'noul', 'instructions': '这段台词提到了夜里的私有信息（刀口、谁商量了什么、自己夜里做了什么），'
                   '而且这件事不在【这次允许说的要点】里。要点里已经写明的查验、用药不算。'},
}


async def check_speech(gw: Gateway, text: str, brief: SpeechBrief) -> list[str]:
    """规则先行；规则过了再问 Jev。Jev 不可用或出错时只靠规则。"""
    bad = check_rules(text, brief)
    if bad or gw.route('check') != 'jev':
        return bad
    try:
        ans = await gw.ask_jev(JEV_KIND, _jev_state(text, brief), _QUESTIONS,
                               timeout_s=gw.ww.limits.check_timeout_s)
    except (LLMError, TimeoutError, ValueError):
        return bad
    block = gw.ww.check.block
    for qid in _QUESTIONS:
        a = ans.get(qid)
        p = float(a.get('noul', 0.0)) if a is not None else 0.0
        if p >= block:
            bad.append(f'jev:{qid}={p:.2f}')
    return bad
