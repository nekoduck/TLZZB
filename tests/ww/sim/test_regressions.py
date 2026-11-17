"""集成暴露出来的问题的回归测试（修在别的模块里，测试放这里）。"""

from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.mind.beliefs import Beliefs
from werewolf.mind.mind import BELIEF_WEIGHTS
from werewolf.mind.types import MindTuning, Traits
from werewolf.testing import Scenario, act

MID = Traits(boldness=0.5, logic=0.5, talkative=0.5, stubborn=0.5, vengeful=0.5, conformity=0.5, deception=0.5)


def _beliefs(sc: Scenario, seat: int) -> Beliefs:
    b = Beliefs(seat, sc.roles[seat], MID, sc_config(sc), MindTuning(), BELIEF_WEIGHTS)
    view = sc.view(seat)
    for ev in sc.events:
        if sc.visible(ev, seat):
            b.observe(ev, view)
    return b


def sc_config(sc: Scenario):  # noqa: ANN201
    from werewolf.kernel.types import GameConfig
    return GameConfig(human_seat=sc.human)


def _counter(order_checked_first: bool) -> Scenario:
    """4 号（真预言家）与 2 号（狼）对跳。order_checked_first：2 号先报 4 号是狼，4 号随后才起跳。"""
    sc = Scenario()  # DEFAULT_ROLES：狼 2、5、8；预言家 3 —— 这里把 3、4 对调，让 4 当预言家
    sc.roles[3], sc.roles[4] = Role.VILLAGER, Role.SEER
    sc.night(1, kill=None, check=(4, 9, 'good'))
    sc.dawn([])
    sc.start_speeches([1, 2, 3, 4, 5, 6, 7, 8, 9])
    seer4 = [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 9, result='good', night=1)]
    fake2 = [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 4, result='wolf', night=1)]
    if order_checked_first:
        sc.speech(2, '我是预言家，昨晚验了4号是狼。', fake2)
        sc.speech(4, '我才是预言家，昨晚验了9号是好人。', seer4)
    else:
        sc.speech(4, '我是预言家，昨晚验了9号是好人。', seer4)
        sc.speech(2, '我是预言家，昨晚验了4号是狼。', fake2)
    return sc


def test_counter_check_on_an_already_claimed_rival_is_not_extra_evidence() -> None:
    """对方已自称预言家之后才报他「查杀」：悍跳的一方总会这么报，不能让被报的那位凭这一条就更像狼
    （此前这条会让后跳的悍跳狼几乎必赢首日的对跳）。反过来，后跳的一位起跳当场查杀先跳的那位是悍跳的
    老套路（真预言家首夜恰好验到他的机会很小），会玩的好人因此更怀疑后跳的这位。"""
    b = _beliefs(_counter(order_checked_first=False), 7)
    assert b.p(2) > b.p(4) + 0.1, (b.p(4), b.p(2))
    assert any(e.reason.value == 'counter_claim' and e.delta >= 0.7 for e in b.evidence(2, 'public'))


def test_check_made_before_the_target_claimed_still_counts() -> None:
    """先被报查杀、之后才起跳的那位，查杀照样算数（被查杀后反跳是常见的狼人打法）。"""
    b = _beliefs(_counter(order_checked_first=True), 7)
    assert b.p(4) > b.p(2) + 0.12, (b.p(4), b.p(2))


def test_claude_overlong_text_drops_filler_sentences_only() -> None:
    """真模型自对弈里两成台词因为略超字数被拦（Claude 档不到 80%）：删不带座位、不带声明的闲话句，要点句一句不删。"""
    from werewolf.llm.speak import fit_length

    text = '哎呀今天这局真热闹，大家都挺能说的。我怀疑3号。他投了金水。说真的我也不太确定。我投3号。'
    cut = fit_length(text, 30)
    assert len(cut) <= 30 and '我怀疑3号。' in cut and cut.endswith('我投3号。')
    assert '热闹' not in cut
    assert fit_length(text, 200) == text
    long_core = '我怀疑3号，我怀疑3号，我怀疑3号。我投3号，我投3号。'
    assert fit_length(long_core, 10) == long_core  # 没有可删的闲话：原样交给核对


def test_quoting_an_accusation_against_me_is_not_a_leak() -> None:
    """「6号报我是狼」是转述别人对我的查杀，不是露底（此前被 leak_phrase:我是狼 拦下）。"""
    from werewolf.kernel.types import ActKind, SpeechAct
    from werewolf.llm.check import check_rules
    from werewolf.mind.types import BriefPoint, PersonaVoice, PublicMood, SpeechBrief, StyleCard

    voice = PersonaVoice(id='p', name='甲', title='乙', tags=[], bio='丙', style=StyleCard())
    brief = SpeechBrief(seat=2, day=2, kind='day', persona=voice, max_chars=120, mood=PublicMood(),
                        points=[BriefPoint(act=SpeechAct(kind=ActKind.DOUBT_CLAIM, target=6), text='质疑 6 号的身份')])
    assert not [b for b in check_rules('6号报我是狼，我不信他的身份。', brief) if b.startswith('leak')]
    assert [b for b in check_rules('其实我是狼，大家别投我。', brief) if b.startswith('leak')]
