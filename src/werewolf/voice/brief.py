"""台词简报：把公开的发言计划写成写台词一方能看到的全部内容（ARCHITECTURE 5、6.1）。

输入全是公开族的类型（PublicPlan、PublicView、PersonaVoice、PublicMood），所以按构造拿不到底牌；
同一份输入不论发言者的真实身份，产出逐字相同。纯函数、无随机。
"""

from __future__ import annotations

from collections.abc import Sequence

from werewolf.kernel.types import ActKind, Event, EventType, PublicView, Role, Seat, SpeechAct
from werewolf.mind.types import (
    PRIVATE_REASONS,
    BriefPoint,
    PersonaVoice,
    PublicAct,
    PublicMood,
    PublicPlan,
    Quote,
    ReasonCode,
    Situation,
    SpeechBrief,
)
from werewolf.voice.canon import role_word
from werewolf.voice.digest import act_clause, own_record, public_digest

_RESULT = {'wolf': '狼人', 'good': '好人'}

# 理由的白话。{t} 是对方（座位号或「对方」）；每条都是能直接接在「因为」后面的分句，
# 并且不含任何会被 canon 读成声明的说法（核对由性质测试守住）。
REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.SEER_CHECK: '我手里有验人的结果',
    ReasonCode.ACCUSED_ME: '{t}之前踩过我',
    ReasonCode.VOTED_ME: '{t}之前投票冲着我来',
    ReasonCode.VOTED_GOOD: '{t}之前把票给了我看着清白的人',
    ReasonCode.VOTED_WITH_WOLF: '{t}和可疑的人站在一边',
    ReasonCode.DEFENDED_WOLF: '{t}一直在替可疑的人说话',
    ReasonCode.ACCUSED_BY_MANY: '好几个人都在说{t}有问题',
    ReasonCode.DEAD_ACCUSED: '夜里走的人生前怀疑过{t}',
    ReasonCode.COUNTER_CLAIM: '{t}在跟别人抢同一个身份',
    ReasonCode.LATE_CLAIM: '{t}亮身份亮得太晚了',
    ReasonCode.CONTRADICTION: '{t}前后说的对不上',
    ReasonCode.QUIET: '{t}一直在划水不表态',
    ReasonCode.TOO_EAGER: '{t}带节奏带得太急了',
    ReasonCode.FOLLOWS_CROWD: '{t}就只会跟着别人走',
    ReasonCode.KILL_PATTERN: '从夜里倒下的人来看，{t}说不过去',
    ReasonCode.CLAIM_UNCONTESTED: '这个身份没有人跟{t}对跳',
    ReasonCode.VOUCHED_BY_TRUSTED: '我信得过的人保了{t}',
    ReasonCode.SAVED_BY_WITCH: '{t}是夜里被人救下来的',
    ReasonCode.OFF_GAME: '{t}说的话不像是在好好玩游戏',
    ReasonCode.SELF_PRESERVE: '我得先保住自己',
    ReasonCode.GUT: '{t}让我不舒服，纯属直觉',
}

_TARGETLESS_OK = {ReasonCode.SEER_CHECK, ReasonCode.SELF_PRESERVE, ReasonCode.COUNTER_CLAIM}

_EVIDENCE_ACT = {
    ActKind.ACCUSE: '怀疑过{t}',
    ActKind.VOUCH: '保过{t}',
    ActKind.DOUBT_CLAIM: '质疑过{t}的身份',
    ActKind.QUESTION: '点名问过{t}',
}


def _nt(a: SpeechAct) -> str:
    return f'第{a.night}夜' if a.night else ''


def _point_text(a: SpeechAct) -> str:
    """要点的白话，给写台词的一方看。"""
    c = act_clause(a)
    if c is None:
        return '过'
    k = a.kind
    if k == ActKind.CLAIM_ROLE and a.role:
        return f'自称身份：{role_word(a.role)}'
    if k == ActKind.CLAIM_CHECK:
        n = f'第{a.night}夜' if a.night else '近期'
        return f'报查验：{n}验了{a.target}号，是{_RESULT[a.result or "good"]}'
    if k == ActKind.CLAIM_SAVE:
        return f'报用药：{_nt(a)}救了{a.target}号'
    if k == ActKind.CLAIM_POISON:
        return f'报用药：{_nt(a)}毒了{a.target}号'
    if k == ActKind.VOTE_INTENT:
        return f'表态投票：今天投{a.target}号'
    if k == ActKind.ACCUSE:
        return f'指认：怀疑{a.target}号'
    if k == ActKind.VOUCH:
        return f'担保：{a.target}号是好人'
    if k == ActKind.DEFEND:
        return f'辩解：替{a.target}号说话' if a.target else '辩解：替自己说话'
    if k == ActKind.BACK_CLAIM:
        return f'站边：相信{a.target}号的身份'
    if k == ActKind.DOUBT_CLAIM:
        return f'质疑：不信{a.target}号的身份'
    if k == ActKind.QUESTION:
        return f'追问：请{a.target}号解释'
    return c


def _evidence_clause(ev: Event, own: Seat, allowed: set[Seat]) -> str | None:
    """一条公开证据事件的短句；牵涉到点名之外的座位，就不用它（简报里不许出现无关座位）。"""

    def who(s: Seat | None) -> str | None:
        if s is None:
            return None
        if s == own:
            return '我'
        return f'{s}号' if s in allowed else None

    if ev.type == EventType.VOTE and ev.actor is not None:
        a = who(ev.actor)
        if a is None:
            return None
        if ev.target is None:
            return f'{a}第{ev.day}天弃了票'
        t = who(ev.target)
        return f'{a}第{ev.day}天把票给了{t}' if t else None
    if ev.type == EventType.SPEECH and ev.actor is not None:
        a = who(ev.actor)
        if a is None:
            return None
        for raw in (ev.data.get('speech') or {}).get('acts') or []:
            sa = SpeechAct.model_validate(raw)
            tpl = _EVIDENCE_ACT.get(sa.kind)
            if tpl and sa.target is not None:
                t = who(sa.target)
                if t:
                    return f'{a}第{ev.day}天' + tpl.format(t=t)
        return None
    if ev.type == EventType.DEATH and ev.target is not None:
        t = who(ev.target)
        return f'{t}第{ev.day}天出了局' if t else None
    return None


def _reason_text(a: PublicAct, pv: PublicView, own: Seat, allowed: set[Seat]) -> str | None:
    if a.reason is None or a.reason in PRIVATE_REASONS or a.reason not in REASON_TEXT:
        return None
    if a.target is None and a.reason not in _TARGETLESS_OK:
        return None  # 没有对象的要点，带「{t}」的理由没处安放
    t = f'{a.target}号' if a.target is not None else '对方'
    text = REASON_TEXT[a.reason].format(t=t)
    by_id = {e.id: e for e in pv.events}
    for eid in a.evidence:
        ev = by_id.get(eid)
        if ev is not None and ev.vis == 'public' and (c := _evidence_clause(ev, own, allowed)):
            return f'{text}，比如{c}'
    return text


def _forbidden(cover: Role | None, points: list[BriefPoint]) -> list[str]:
    kinds = {p.act.kind for p in points}
    out = ['不要说自己是AI、模型或程序，不要提游戏规则之外的任何事', '不要用引号、括号或舞台提示']
    if cover is None:
        out.append('不要说出自己的身份')
    else:
        others = [role_word(r) for r in (Role.SEER, Role.WITCH, Role.HUNTER, Role.VILLAGER, Role.WEREWOLF) if r != cover]
        out.append('不要说自己是' + '、'.join(others))
    if ActKind.CLAIM_CHECK not in kinds:
        out.append('不要报任何查验结果，不要提验人')
    if not kinds & {ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON}:
        out.append('不要提解药、毒药')
    if ActKind.VOTE_INTENT not in kinds:
        out.append('不要表态今天投谁')
    out.append('不要提要点之外的座位，不要写要点之外的夜间细节')
    return out


def build_brief(
    plan: PublicPlan,
    pv: PublicView,
    voice: PersonaVoice,
    mood: PublicMood,
    situation: Situation,
    heard: Sequence[Quote],
    max_chars: int,
) -> SpeechBrief:
    allowed: set[Seat] = {plan.seat, *plan.addressed}
    allowed.update(a.target for a in plan.acts if a.target is not None)
    points: list[BriefPoint] = []
    for a in plan.acts:
        reason = _reason_text(a, pv, plan.seat, allowed)
        sa = SpeechAct(**{k: v for k, v in a.model_dump().items() if k in SpeechAct.model_fields})
        points.append(BriefPoint(
            act=sa, text=_point_text(sa), reason=reason, hedge=bool(a.fuzzy or a.reason == ReasonCode.GUT),
        ))
    return SpeechBrief(
        seat=plan.seat, day=plan.day, kind=plan.kind, persona=voice, cover_role=plan.cover_role,
        tone=plan.tone, length=plan.length, max_chars=max_chars, emote=plan.emote, points=points,
        alive=pv.living(), dead=pv.dead(), situation=situation.model_copy(deep=True),
        recent=public_digest(pv), own_record=own_record(pv, plan.seat), heard=[q.model_copy() for q in heard],
        addressed=list(plan.addressed), mood=mood.model_copy(deep=True), forbidden=_forbidden(plan.cover_role, points),
    )
