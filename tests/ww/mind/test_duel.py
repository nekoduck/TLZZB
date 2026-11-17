"""预言家对跳的复盘：好人只用公开事实分辨真假预言家（刀口、查验与公开事实对不对得上、坐实的女巫、
后跳当场查杀先跳、站边与帮票），以及「放逐了更不信的那位之后，剩下那位不再当成不信的那位」。

身份见 DEFAULT_ROLES：狼 2、5、8；预言家 3；女巫 6；猎人 9；平民 1、4、7。观察者是 7 号平民。
"""

from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.mind.beliefs import Beliefs
from werewolf.mind.mind import BELIEF_WEIGHTS
from werewolf.mind.strategy.common import LONE_TRUST, doubted_claimant
from werewolf.mind.types import MindTuning, ReasonCode, Traits
from werewolf.testing import Scenario, act

from .policy_helpers import config_of, mind_for

MID = Traits(boldness=0.5, logic=0.5, talkative=0.5, stubborn=0.5, vengeful=0.5, conformity=0.5, deception=0.5)
SEER_CLAIM = act('claim_role', role=Role.SEER, weight=3)


def _beliefs(sc: Scenario, seat: int = 7) -> Beliefs:
    b = Beliefs(seat, sc.roles[seat], MID, config_of(sc), MindTuning(), BELIEF_WEIGHTS)
    view = sc.view(seat)
    for ev in view.events:
        b.observe(ev, view)
    return b


def _reasons(b: Beliefs, seat: int) -> set[ReasonCode]:
    return {e.reason for e in b.evidence(seat, 'public') if e.delta > 0}


def _duel_day1(fake_first: bool = True) -> Scenario:
    """第 1 夜刀 4 号、女巫救了（平安夜），3 号验 1 号好人。第 1 天 2 号（狼）与 3 号对跳，2 号报 4 号查杀。"""
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 1, 'good'), save=True)
    sc.dawn([])
    sc.start_speeches([1, 2, 3, 4, 5, 6, 7, 8, 9])
    fake = [SEER_CLAIM, act('claim_check', 4, result='wolf', night=1), act('accuse', 4, weight=2)]
    real = [SEER_CLAIM, act('claim_check', 1, result='good', night=1), act('doubt_claim', 2)]
    order = [(2, fake), (3, real)] if fake_first else [(3, real), (2, fake)]
    for seat, acts in order:
        sc.speech(seat, '我是预言家。', acts)
    return sc


def _night2(sc: Scenario, kill: int, deaths: list[int] | None = None, poison: int | None = None) -> Scenario:
    sc.vote({1: 9, 2: 9, 3: 9, 4: 9, 5: 9, 6: 9, 7: 9, 8: 9, 9: 1}, exiled=9)
    sc.night(2, kill=kill, poison=poison)
    sc.dawn(deaths if deaths is not None else [kill], {poison: 'poison'} if poison else None)
    sc.start_speeches([7, 8])
    return sc


def test_knifed_claimant_makes_the_surviving_rival_suspect() -> None:
    """对跳的一位夜里被刀（狼几乎不刀自家悍跳的）：活着的另一位更像狼。"""
    knifed = _beliefs(_night2(_duel_day1(), kill=3))
    control = _beliefs(_night2(_duel_day1(), kill=1))
    assert knifed.p(2) > control.p(2) + 0.15, (knifed.p(2), control.p(2))
    assert ReasonCode.KILL_PATTERN in _reasons(knifed, 2)
    assert knifed.claim_trust(3, 'public', Role.SEER) > control.claim_trust(3, 'public', Role.SEER)


def test_knifed_check_target_refutes_the_check() -> None:
    """被报查杀的人（报了之后）在夜里独死，那就是刀口：狼不刀队友，那条查杀是假的。没有对跳也算。"""
    def lone(kill: int) -> Scenario:
        sc = Scenario()
        sc.night(1, kill=4, save=True)
        sc.dawn([])
        sc.speech(2, '我是预言家，4号查杀。', [SEER_CLAIM, act('claim_check', 4, result='wolf', night=1)])
        return _night2(sc, kill=kill)

    refuted = _beliefs(lone(4))
    control = _beliefs(lone(1))
    assert refuted.p(2) > control.p(2) + 0.2, (refuted.p(2), control.p(2))
    assert ReasonCode.CONTRADICTION in _reasons(refuted, 2)
    # 两人同死（其中一个是毒）：说不准哪个是刀口，作用小得多
    both = _beliefs(_night2(_duel_day1(), kill=1, deaths=[1, 4], poison=4))
    single = _beliefs(_night2(_duel_day1(), kill=4))
    assert single.p(2) > both.p(2) + 0.1, (single.p(2), both.p(2))


def test_verified_witch_is_trusted_and_her_checker_exposed() -> None:
    """唯一的女巫声明、报的银水与平安夜对得上：女巫可信；查杀她的预言家声明者可疑。"""
    def scen(witch_claims: bool) -> Scenario:
        sc = Scenario()
        sc.night(1, kill=4, save=True)
        sc.dawn([])
        sc.speech(2, '我是预言家，6号查杀。', [SEER_CLAIM, act('claim_check', 6, result='wolf', night=1)])
        if witch_claims:
            sc.speech(6, '我是女巫，昨晚救了4号。', [act('claim_role', role=Role.WITCH, weight=3),
                                                 act('claim_save', 4, night=1)])
        else:
            sc.speech(6, '我是好人。', [act('defend')])
        return sc

    claimed, quiet = _beliefs(scen(True)), _beliefs(scen(False))
    assert claimed.p(6) < quiet.p(6) - 0.15, (claimed.p(6), quiet.p(6))
    assert claimed.p(2) > quiet.p(2) + 0.1, (claimed.p(2), quiet.p(2))
    assert ReasonCode.CONTRADICTION in _reasons(claimed, 2)


def test_late_counter_check_on_the_first_claimer_is_the_textbook_fake() -> None:
    """后跳的一位起跳当场查杀先跳的那位：更像悍跳；先跳的那位不因这条查杀更像狼。"""
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 1, 'good'), save=True)
    sc.dawn([])
    sc.speech(3, '我是预言家，1号金水。', [SEER_CLAIM, act('claim_check', 1, result='good', night=1)])
    sc.speech(2, '我才是预言家，3号查杀。', [SEER_CLAIM, act('claim_check', 3, result='wolf', night=1)])
    b = _beliefs(sc)
    assert b.p(2) > b.p(3) + 0.1, (b.p(2), b.p(3))
    # 后跳的报的是别人（不是先跳的那位）：没有这条
    sc2 = Scenario()
    sc2.night(1, kill=4, check=(3, 1, 'good'), save=True)
    sc2.dawn([])
    sc2.speech(3, '我是预言家，1号金水。', [SEER_CLAIM, act('claim_check', 1, result='good', night=1)])
    sc2.speech(2, '我才是预言家，7号金水。', [SEER_CLAIM, act('claim_check', 7, result='good', night=1)])
    b2 = _beliefs(sc2)
    assert not any(e.reason == ReasonCode.COUNTER_CLAIM and e.delta > 0.6 for e in b2.evidence(2, 'public'))


def test_backers_and_gold_of_an_exposed_claimant_become_suspect() -> None:
    """2 号的查杀成了刀口而露馅：站他边、帮他投 3 号的 5 号，和他给的金水 8 号，都跟着可疑（随时重算）。"""
    def scen(kill: int) -> Scenario:
        sc = Scenario()
        sc.night(1, kill=4, check=(3, 1, 'good'), save=True)
        sc.dawn([])
        sc.speech(2, '我是预言家，4号查杀，8号金水。', [SEER_CLAIM, act('claim_check', 4, result='wolf', night=1)])
        sc.speech(3, '我是预言家，1号金水。', [SEER_CLAIM, act('claim_check', 1, result='good', night=1)])
        sc.speech(5, '我站2号。', [act('back_claim', 2, weight=2)])
        sc.vote({1: 2, 2: 3, 3: 2, 4: 2, 5: 3, 6: 2, 7: 9, 8: 3, 9: 2}, exiled=2)
        sc.night(2, kill=kill)
        sc.dawn([kill])
        sc.speech(9, '……', [act('pass')])
        return sc

    exposed, control = _beliefs(scen(4)), _beliefs(scen(1))
    assert exposed.p(5) > control.p(5) + 0.05, (exposed.p(5), control.p(5))
    assert {ReasonCode.DEFENDED_WOLF} & _reasons(exposed, 5)


def test_lone_survivor_trusted_more_than_the_exiled_rival_is_not_the_doubted_one() -> None:
    """对跳里更不信的那位被放逐了：剩下的那位是自己站的边，投票时不再按「不信的那位」加效用。"""
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 1, 'good'), save=True)
    sc.dawn([])
    sc.speech(3, '我是预言家，1号金水。', [SEER_CLAIM, act('claim_check', 1, result='good', night=1)])
    sc.speech(2, '我才是预言家，3号查杀。', [SEER_CLAIM, act('claim_check', 3, result='wolf', night=1)])
    sc.vote({1: 2, 2: 3, 3: 2, 4: 2, 5: 3, 6: 2, 7: 2, 8: 3, 9: 2}, exiled=2)
    sc.night(2, kill=1)
    sc.dawn([1])
    sc.start_speeches([3, 4, 5, 6, 7, 8, 9])
    m = mind_for(sc, 7)
    ctx = m._ctx(sc.view(7))
    trust3 = m.beliefs.claim_trust(3, 'private', Role.SEER, m.ledger)
    assert m.beliefs.claim_trust(2, 'private', Role.SEER, m.ledger) < trust3 < LONE_TRUST  # 以前：仍算「不信的那位」
    assert doubted_claimant(ctx) is None, trust3


def test_dump_load_keeps_the_backing_record() -> None:
    sc = _duel_day1()
    sc.speech(5, '我站2号。', [act('back_claim', 2, weight=2)])
    b = _beliefs(sc)
    b2 = Beliefs(7, Role.VILLAGER, MID, config_of(sc), MindTuning(), BELIEF_WEIGHTS)
    b2.load(b.dump())
    assert b2.dump()['backs'] == b.dump()['backs'] != []
