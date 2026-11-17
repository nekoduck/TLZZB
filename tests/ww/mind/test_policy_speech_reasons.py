"""发言里说出口的理由像真人（审查回归）：首日能从公开言行里找到说法，不满桌「纯属直觉」；
不站正在踩我的预言家的边；预言家、女巫的遗言把报过的查验与用药再报一遍。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Role
from werewolf.mind.speechplan import validate_plan
from werewolf.mind.strategy.common import accuse_acts, public_cite
from werewolf.mind.types import ReasonCode
from werewolf.testing import Scenario, act

from .policy_helpers import mind_for, persona

ME = 7  # 平民


def _day1(*speeches: tuple[int, list]) -> Scenario:
    sc = Scenario()
    sc.night(1, kill=None)
    sc.dawn([])
    sc.start_speeches([s for s, _ in speeches] + [ME])
    for s, acts in speeches:
        sc.speech(s, 'x', acts)
    return sc


def test_day1_push_cites_public_behaviour_instead_of_gut() -> None:
    # 1 号第一个发言就凭感觉号召投 4 号；9 号跟着 1 号喊投 4 号，没给自己的理由
    sc = _day1((1, [act('accuse', 4, weight=1), act('vote_intent', 4, weight=1)]),
               (9, [act('vote_intent', 4, weight=1)]))
    m = mind_for(sc, ME, persona('plain'))
    view = sc.view(ME)
    ctx = m._ctx(view)
    eager = public_cite(ctx, 1)
    assert eager is not None and eager.reason == ReasonCode.TOO_EAGER and eager.evidence
    follow = public_cite(ctx, 9)
    assert follow is not None and follow.reason == ReasonCode.FOLLOWS_CROWD
    assert public_cite(ctx, 5) is None  # 还没开口的人没有公开言行可说
    acts = accuse_acts(ctx, 1)
    assert [(a.kind, a.reason, a.weight) for a in acts] == [(ActKind.ACCUSE, ReasonCode.TOO_EAGER, 1),
                                                           (ActKind.VOTE_INTENT, ReasonCode.TOO_EAGER, 1)]
    assert all(a.evidence == eager.evidence for a in acts)  # 软理由只给轻重 1，且带证据
    assert accuse_acts(ctx, 5)[0].reason == ReasonCode.GUT
    for p in m.speech_variants(sc.request_speak(ME), view):
        assert validate_plan(p, view, m.ledger) == []


def test_vouching_me_is_not_quiet() -> None:
    sc = _day1((9, [act('vouch', ME, weight=2)]), (1, [act('pass')]))
    m = mind_for(sc, ME, persona('plain'))
    ctx = m._ctx(sc.view(ME))
    assert public_cite(ctx, 9) is None
    c = public_cite(ctx, 1)
    assert c is not None and c.reason == ReasonCode.QUIET


def test_no_backing_for_the_claimant_who_is_pushing_me() -> None:
    # 3 号独跳预言家，同时踩我、号召投我：我嘴上不会再说「信他的身份」
    sc = _day1((3, [act('claim_role', role='seer', weight=3), act('claim_check', 1, result='good', night=1),
                    act('accuse', ME, weight=2), act('vote_intent', ME, weight=2)]))
    for i in range(6):
        m = mind_for(sc, ME, persona(f'herd{i}', conformity=0.9))
        plans = m.speech_variants(sc.request_speak(ME), sc.view(ME))
        assert not any(a.kind == ActKind.BACK_CLAIM and a.target == 3 for p in plans for a in p.acts)
    # 对照：没踩我的独跳预言家，从众的人照样站边
    sc2 = _day1((3, [act('claim_role', role='seer', weight=3), act('claim_check', 1, result='good', night=1),
                     act('accuse', 4, weight=2), act('vote_intent', 4, weight=2)]))
    m2 = mind_for(sc2, ME, persona('herd', conformity=0.9))
    plans2 = m2.speech_variants(sc2.request_speak(ME), sc2.view(ME))
    assert any(a.kind == ActKind.BACK_CLAIM and a.target == 3 for p in plans2 for a in p.acts)


def test_seer_last_words_restate_earlier_checks() -> None:
    sc = Scenario()
    seer = sc.seat_of(Role.SEER)
    sc.night(1, kill=None, check=(seer, 2, 'wolf'))
    sc.dawn([])
    sc.start_speeches([seer, 1, 4])
    sc.speech(seer, 'x', [act('claim_role', role='seer', weight=3), act('claim_check', 2, result='wolf', night=1),
                          act('accuse', 2, weight=3), act('vote_intent', 2, weight=2)])
    sc.vote({1: seer, 2: seer, 4: seer, 5: seer, 8: seer, seer: 2, 6: 2, 7: 2, 9: 2}, exiled=seer)
    m = mind_for(sc, seer, persona('seer'))
    plans = m.speech_variants(sc.request_speak(seer, kind='last_words'), sc.view(seer))
    checks = [a for p in plans for a in p.acts if a.kind == ActKind.CLAIM_CHECK]
    assert any(a.target == 2 and a.result == 'wolf' and a.night == 1 for a in checks)
    for p in plans:
        assert validate_plan(p, sc.view(seer), m.ledger) == []
