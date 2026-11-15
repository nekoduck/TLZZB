"""测试数据：手工拼的狼人发言计划、简报与人设（不引用 voice.brief）。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Role, SpeechAct
from werewolf.mind.types import (
    AvatarSpec,
    BriefPoint,
    Persona,
    PersonaVoice,
    PlannedAct,
    PublicMood,
    ReasonCode,
    SpeechBrief,
    SpeechPlan,
    StyleCard,
    Traits,
)

VOICE = PersonaVoice(
    id='p1', name='老周', title='退休的语文老师', tags=['稳', '爱讲道理'],
    bio='我教了三十年书，说话慢，讲究有凭有据。', choices=['被冤枉时我宁可拍桌也不解释'],
    style=StyleCard(sentence_len='medium', catchphrases=['说句公道话'], openers=['我先说两句'],
                    quirks=['爱引用课文'], samples=['这事得讲个道理。']),
)


def persona() -> Persona:
    return Persona(
        id='p1', name='老周', title='退休的语文老师', tags=['稳', '爱讲道理'],
        traits=Traits(boldness=0.4, logic=0.8, talkative=0.5, stubborn=0.5, vengeful=0.3, conformity=0.3,
                      deception=0.6),
        bio='我教了三十年书，说话慢，讲究有凭有据。', choices=['被冤枉时我宁可拍桌也不解释'],
        style=VOICE.style, avatar=AvatarSpec(bg='#223', skin='#fca', hair='#111', hair_style='short'))


def wolf_plan(seat: int = 2) -> SpeechPlan:
    """狼人（队友 5、8）的发言计划：悍跳预言家，报 5 号以外的查杀；私有动机与真假只在 PlannedAct 里。"""
    return SpeechPlan(
        id='jump_seer', seat=seat, day=2, kind='day', variant='counter_claim', label='悍跳预言家',
        acts=[
            PlannedAct(kind=ActKind.CLAIM_ROLE, role=Role.SEER, weight=3, motive=ReasonCode.TEAM, truthful=False),
            PlannedAct(kind=ActKind.CLAIM_CHECK, target=3, result='wolf', night=1, weight=3,
                       motive=ReasonCode.TEAM, truthful=False),
            PlannedAct(kind=ActKind.VOTE_INTENT, target=3, motive=ReasonCode.TEAM, truthful=False),
        ],
        cover_role=Role.SEER, thought='5号和8号是我的狼队友，我必须悍跳。',
    )


def wolf_brief(seat: int = 2) -> SpeechBrief:
    """由 wolf_plan().public() 手工拼出的简报：要点只有计划里说出口的事。"""
    plan = wolf_plan(seat).public()
    points = []
    for a in plan.acts:
        sa = SpeechAct(**{k: v for k, v in a.model_dump().items() if k in SpeechAct.model_fields})
        if a.kind == ActKind.CLAIM_ROLE:
            txt = '自称预言家'
        elif a.kind == ActKind.CLAIM_CHECK:
            txt = f'报查验：昨夜验了{a.target}号，是狼人'
        else:
            txt = f'表态：今天投{a.target}号'
        points.append(BriefPoint(act=sa, text=txt))
    return SpeechBrief(
        seat=seat, day=2, kind='day', persona=VOICE, cover_role=plan.cover_role, max_chars=120, points=points,
        alive=[1, 2, 3, 4, 5, 6, 7, 8, 9], dead=[], recent=['第 1 天，3 号自称预言家，报 7 号是好人'],
        own_record=[], mood=PublicMood(), forbidden=['没列出的身份', '没列出的查验与夜间细节', '自己是 AI'],
    )
