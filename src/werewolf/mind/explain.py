"""白话说明：把理由代码与座位号渲染成中性的中文（ARCHITECTURE 4.2）。

`Decision.thought`、`Decision.note`、`ScoredOption.reasons`、`SpeechPlan.label`、`SpeechPlan.thought`
都从这里出。措辞中性，不带人设口吻，不调模型；同样的输入永远得到同样的文字。
"""

from __future__ import annotations

from collections.abc import Sequence

from werewolf.kernel.types import ROLE_ZH, ActKind, Role, Seat
from werewolf.mind.types import PlannedAct, ReasonCode

Why = tuple[str, Seat | None]  # (理由键, 涉及的座位)

# 理由键 → 模板；{t} 是座位号。键是 ReasonCode 的值，外加效用层自己的几种。
_TEXT: dict[str, str] = {
    ReasonCode.SEER_CHECK.value: '{t}号的查验结果',
    ReasonCode.ACCUSED_ME.value: '{t}号踩过我',
    ReasonCode.VOTED_ME.value: '{t}号投过我',
    ReasonCode.VOTED_GOOD.value: '{t}号把票投给了好人',
    ReasonCode.VOTED_WITH_WOLF.value: '{t}号和可疑的人同票',
    ReasonCode.DEFENDED_WOLF.value: '{t}号替可疑的人说话',
    ReasonCode.ACCUSED_BY_MANY.value: '好几个人都踩{t}号',
    ReasonCode.DEAD_ACCUSED.value: '夜里死的人生前怀疑{t}号',
    ReasonCode.COUNTER_CLAIM.value: '{t}号在对跳',
    ReasonCode.LATE_CLAIM.value: '{t}号亮身份太晚',
    ReasonCode.CONTRADICTION.value: '{t}号前后说法矛盾',
    ReasonCode.QUIET.value: '{t}号划水',
    ReasonCode.TOO_EAGER.value: '{t}号带节奏太急',
    ReasonCode.FOLLOWS_CROWD.value: '{t}号只会跟票',
    ReasonCode.KILL_PATTERN.value: '从刀口反推{t}号',
    ReasonCode.CLAIM_UNCONTESTED.value: '{t}号的身份没人对跳',
    ReasonCode.VOUCHED_BY_TRUSTED.value: '信得过的人保了{t}号',
    ReasonCode.SAVED_BY_WITCH.value: '{t}号是银水',
    ReasonCode.OFF_GAME.value: '{t}号场外',
    ReasonCode.SELF_PRESERVE.value: '先保住自己',
    ReasonCode.GUT.value: '对{t}号的直觉',
    ReasonCode.TEAM.value: '狼队的打算',
    ReasonCode.HIDDEN_CHECK.value: '没公开的查验：{t}号',
    ReasonCode.REFLECTION.value: '夜里想过{t}号',
    # 效用层自己的理由
    'suspect': '{t}号最像狼',
    'trusted': '{t}号像好人',
    'hard_wolf': '确定{t}号是狼',
    'hard_good': '确定{t}号是好人',
    'teammate': '{t}号是队友',
    'commit': '白天说过要投{t}号',
    'called': '有人号召投{t}号',
    'crowd': '大家都在投{t}号',
    'grudge': '跟{t}号有仇',
    'counter': '{t}号踩过我，反击',
    'threat_seer': '{t}号是跳出来的预言家',
    'threat_witch': '{t}号是跳出来的女巫',
    'threat_logic': '{t}号逻辑强、在找狼',
    'hunter': '{t}号是猎人，刀了会开枪',
    'bias': '{t}号在跟队友对跳，刀了等于替他证明',
    'leader': '头狼提议刀{t}号',
    'mate_pick': '队友提议刀{t}号',
    'unknown': '{t}号身份不明',
    'active': '{t}号发言多、影响大',
    'claimed': '{t}号自称神职',
    'save': '救{t}号',
    'save_first': '首夜一般救人',
    'save_suspect': '{t}号可疑，不值得救',
    'keep_potion': '药留着更有用',
    'poison': '毒{t}号',
    'hold_fire': '没有把握，不开枪',
    'abstain': '没有把握，弃票',
    'empty_kill': '空刀',
    'pk_rival': '{t}号是 PK 对手',
    'cover': '{t}号那边站着队友',
    'bus': '{t}号保不住了，倒钩',
}

_STATE_ZH = {'tilted': '心态崩了', 'tense': '压力很大', 'confident': '状态正好', 'steady': ''}


def reason(key: str | ReasonCode, seat: Seat | None = None) -> str:
    k = key.value if isinstance(key, ReasonCode) else key
    tpl = _TEXT.get(k, k)
    if '{t}' in tpl:
        return tpl.format(t=seat if seat is not None else '某')
    return tpl


def reasons(whys: Sequence[Why]) -> list[str]:
    out: list[str] = []
    for k, s in whys:
        txt = reason(k, s)
        if txt not in out:
            out.append(txt)
    return out


def option_label(option_id: str) -> str:
    kind, _, rest = option_id.partition(':')
    t = rest if rest and rest not in ('none', 'abstain') else ''
    return {
        'kill': f'刀 {t} 号' if t else '空刀',
        'check': f'查验 {t} 号',
        'poison': f'毒 {t} 号',
        'shoot': f'带走 {t} 号' if t else '不开枪',
        'vote': f'投 {t} 号' if t else '弃票',
    }.get(kind, {'save': '用解药', 'pass': '不用药'}.get(option_id, option_id))


def thought(option_id: str, whys: Sequence[Why], state: str = 'steady') -> str:
    """一次决定的内心想法：「投 7 号：7号最像狼；白天说过要投7号」。"""
    body = '；'.join(reasons(whys)[:3])
    head = option_label(option_id)
    mood = _STATE_ZH.get(state, '')
    parts = [f'{head}：{body}' if body else head]
    if mood:
        parts.append(f'（{mood}）')
    return ''.join(parts)


def wolf_note(target: Seat | None, whys: Sequence[Why]) -> str:
    """狼人夜里给队友的一句话（至多 80 字）。"""
    if target is None:
        return '今晚空刀'
    why = reasons(whys)[:1]
    return (f'刀 {target} 号，' + why[0])[:80] if why else f'刀 {target} 号'


# --------------------------------------------------------------------------- 发言计划

_ACT_ZH = {
    ActKind.ACCUSE: '踩{t}号', ActKind.VOUCH: '保{t}号', ActKind.DEFEND: '辩解', ActKind.VOTE_INTENT: '号召投{t}号',
    ActKind.BACK_CLAIM: '站边{t}号', ActKind.DOUBT_CLAIM: '质疑{t}号的身份', ActKind.QUESTION: '点名{t}号',
    ActKind.PASS: '过', ActKind.CLAIM_SAVE: '报救了{t}号', ActKind.CLAIM_POISON: '报毒了{t}号',
}

_VARIANT_ZH = {
    'push': '踩人', 'vouch': '保人', 'defend': '辩解', 'side': '站边', 'follow': '跟票', 'pass': '划水',
    'reveal': '亮身份', 'counter_claim': '对跳', 'hide': '藏身份', 'jump': '悍跳', 'charge': '冲锋',
    'deep': '深水', 'fake_claim': '冒领身份', 'bus': '倒钩', 'last': '遗言',
}


def act_text(a: PlannedAct) -> str:
    if a.kind == ActKind.CLAIM_ROLE and a.role is not None:
        return f'自称{ROLE_ZH[a.role]}'
    if a.kind == ActKind.CLAIM_CHECK:
        res = '狼人' if a.result == 'wolf' else '好人'
        return f'报第{a.night}夜验{a.target}号是{res}'
    if a.kind == ActKind.DEFEND and a.target is not None:
        return f'替{a.target}号辩解'
    tpl = _ACT_ZH.get(a.kind, a.kind.value)
    return tpl.format(t=a.target) if '{t}' in tpl else tpl


def plan_label(variant: str, acts: Sequence[PlannedAct]) -> str:
    """一句中文标签：「踩人：踩7号，号召投7号」。"""
    head = _VARIANT_ZH.get(variant, variant)
    body = '，'.join(act_text(a) for a in acts[:4])
    return f'{head}：{body}' if body else head


def plan_thought(variant: str, acts: Sequence[PlannedAct], cover: Role | None = None) -> str:
    """计划的内心想法：真实动机与说出口的理由分开写。"""
    bits: list[str] = []
    for a in acts:
        if a.kind in (ActKind.PASS,):
            continue
        tgt = a.target
        motive = a.motive.value if a.motive is not None else None
        said = a.reason.value if a.reason is not None else None
        if motive and motive != said:
            bits.append(f'{act_text(a)}（真实原因：{reason(motive, tgt)}；嘴上说：{reason(said, tgt) if said else "不说"}）')
        elif said:
            bits.append(f'{act_text(a)}（{reason(said, tgt)}）')
        if len(bits) >= 3:
            break
    head = _VARIANT_ZH.get(variant, variant)
    if cover is not None:
        head += f'，口径{ROLE_ZH[cover]}'
    return head + ('：' + '；'.join(bits) if bits else '')
