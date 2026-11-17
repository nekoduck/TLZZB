"""怀疑度：两层、硬知识、各类证据、防刷、场外、不看真人、人格调节、存取。"""

from __future__ import annotations

import pytest

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import Role
from werewolf.mind.beliefs import Beliefs
from werewolf.mind.types import ReasonCode
from werewolf.testing import Scenario, act, duel_scenario

from .helpers import beliefs_for, traits, visible


def day1(peaceful: bool = False) -> Scenario:
    sc = Scenario()
    if peaceful:
        sc.night(1, kill=4, save=True)
        sc.dawn([])
    else:
        sc.night(1, kill=4, check=(3, 5, 'wolf'))
        sc.dawn([4])
    sc.start_speeches()
    return sc


def reasons(b: Beliefs, seat: int, layer: str = 'private') -> list[ReasonCode]:
    return [e.reason for e in b.evidence(seat, layer)]  # type: ignore[arg-type]


# ---------------------------------------------------------------- 预言家声明


def test_sole_seer_kill_raises_and_gold_lowers() -> None:
    sc = day1()
    base = beliefs_for(sc, 1, tr=traits(logic=0.5))
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    b = beliefs_for(sc, 1, tr=traits(logic=0.5))
    assert b.p(5, 'public') >= 0.7 and b.p(5) >= 0.7
    assert b.p(5) > base.p(5) + 0.3
    assert ReasonCode.SEER_CHECK in reasons(b, 5)
    assert ReasonCode.CLAIM_UNCONTESTED in reasons(b, 3)
    assert b.claim_trust(3, 'public') > 0.6

    sc2 = day1()
    sc2.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='good', night=1)])
    g = beliefs_for(sc2, 1)
    assert g.p(7) < base.p(7) - 0.1
    assert [e for e in g.evidence(7) if e.reason == ReasonCode.SEER_CHECK][0].delta < 0


def test_counter_claim_splits_trust_and_real_seer_knows() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    solo = beliefs_for(sc, 1)
    sc.speech(7, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 9, result='good', night=1)])
    b = beliefs_for(sc, 1)
    t3, t7 = b.claim_trust(3), b.claim_trust(7)
    assert t3 < solo.claim_trust(3) and t3 + t7 <= 1.0 + 1e-9
    assert abs(t3 - t7) < 0.1
    assert b.p(5) < solo.p(5)  # 查杀的信用被分走
    assert ReasonCode.COUNTER_CLAIM in reasons(b, 3) and ReasonCode.COUNTER_CLAIM in reasons(b, 7)
    seer = beliefs_for(sc, 3)
    assert seer.p(7) >= 0.9  # 冒领我的身份
    assert seer.p(5) == 1.0 and seer.is_hard(5)
    assert seer.claim_trust(7) == 0.0 and seer.claim_trust(3) == 1.0


def test_checked_me_wolf_means_liar() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 1, result='wolf', night=1)])
    b = beliefs_for(sc, 1)
    assert b.p(5) > 0.7 and ReasonCode.CONTRADICTION in reasons(b, 5)


# ---------------------------------------------------------------- 两层与硬知识


def test_wolf_private_knows_all_public_equals_villager() -> None:
    sc = duel_scenario()
    wolf = beliefs_for(sc, 2)
    assert wolf.all('private') == {1: 0.0, 3: 0.0, 4: 0.0, 5: 1.0, 6: 0.0, 7: 0.0, 8: 1.0, 9: 0.0}
    assert all(wolf.is_hard(s) for s in wolf.others)
    vil = beliefs_for(sc, 2, role=Role.VILLAGER, public_only=True)
    assert wolf.all('public') == vil.all('public')
    for s in wolf.others:
        assert wolf.evidence(s, 'public') == vil.evidence(s, 'public')
    assert not wolf.is_hard(5, 'public')


def test_public_layer_same_for_every_role_in_seat() -> None:
    sc = duel_scenario()
    ref = beliefs_for(sc, 9, role=Role.VILLAGER, public_only=True).all('public')
    for role in Role:
        assert beliefs_for(sc, 9, role=role).all('public') == ref


def test_seer_private_hard_checks() -> None:
    sc = duel_scenario()
    seer = beliefs_for(sc, 3)
    assert seer.p(2) == 1.0 and seer.p(7) == 0.0
    assert seer.evidence(2)[0].hard and seer.evidence(2)[0].reason == ReasonCode.SEER_CHECK
    assert seer.p(2, 'public') < 1.0
    # 其余两只狼在剩下的不明座位里，期望狼数 = 3 − 1
    unknown = [s for s in seer.others if not seer.is_hard(s)]
    assert sum(seer.p(s) for s in unknown) == pytest.approx(2.0, abs=0.05)


def test_normalisation_public_expected_wolves() -> None:
    b = beliefs_for(duel_scenario(), 1)
    assert sum(b.all('public').values()) == pytest.approx(3.0, abs=0.05)


def test_witch_knows_victim_is_good() -> None:
    sc = day1()
    w = beliefs_for(sc, 6)
    assert w.p(4) < w.p(4, 'public')
    assert ReasonCode.KILL_PATTERN in reasons(w, 4)


def test_revealed_role_hard_in_both_layers() -> None:
    sc = day1()
    sc.vote({1: 9, 2: 9, 3: 9, 5: 9, 6: 9, 7: 9, 8: 9, 9: 1}, exiled=9)
    sc.shot(9, 2)
    b = beliefs_for(sc, 1)
    assert b.p(9, 'public') == 0.0 and b.p(9) == 0.0 and b.is_hard(9, 'public')
    assert ReasonCode.DEAD_ACCUSED in reasons(b, 2)


# ---------------------------------------------------------------- 软证据


def test_dead_accuser_gains_weight() -> None:
    sc = day1()
    sc.speech(7, 'x', [act('accuse', 8)])
    sc.speech(9, 'x', [act('accuse', 2)])
    before = beliefs_for(sc, 1)
    gap0 = before.p(8) - before.p(2)
    assert abs(gap0) < 0.01
    sc.vote({1: 5, 2: 5, 3: 5, 5: 3, 6: 5, 7: 5, 8: 5, 9: 5}, exiled=5)
    sc.night(2, kill=7)
    sc.dawn([7])
    after = beliefs_for(sc, 1)
    assert after.p(8) - after.p(2) > gap0 + 0.05
    assert ReasonCode.DEAD_ACCUSED in reasons(after, 8)
    assert after.p(7) < 0.2  # 夜里死的人大概率是好人


def test_vote_evidence() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='good', night=1)])
    sc.vote({1: 2, 2: 7, 3: 2, 5: 1, 6: 2, 7: 2, 8: 7, 9: 2}, exiled=2)
    b = beliefs_for(sc, 1, tr=traits(vengeful=0.5))
    assert ReasonCode.VOTED_ME in reasons(b, 5)
    assert ReasonCode.VOTED_GOOD in reasons(b, 8)  # 投了金水
    assert b.p(8) > b.p(9)
    hi = beliefs_for(sc, 1, tr=traits(vengeful=1.0))
    lo = beliefs_for(sc, 1, tr=traits(vengeful=0.0))
    d_hi = sum(e.delta for e in hi.evidence(5) if e.reason == ReasonCode.VOTED_ME)
    d_lo = sum(e.delta for e in lo.evidence(5) if e.reason == ReasonCode.VOTED_ME)
    assert d_hi > 2 * d_lo > 0


def test_voted_with_wolf() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    sc.vote({1: 5, 2: 3, 3: 5, 5: 3, 6: 5, 7: 5, 8: 5, 9: 5}, exiled=5)
    b = beliefs_for(sc, 1)
    assert ReasonCode.VOTED_WITH_WOLF in reasons(b, 2)


def test_accused_me_vengeful() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('accuse', 1)])
    hi = beliefs_for(sc, 1, tr=traits(vengeful=1.0))
    lo = beliefs_for(sc, 1, tr=traits(vengeful=0.0))
    assert hi.p(5) > lo.p(5)
    assert ReasonCode.ACCUSED_ME in reasons(hi, 5)


def test_speaker_cap_twelve_equals_one() -> None:
    once = day1()
    once.speech(5, 'x', [act('accuse', 7)])
    many = day1()
    for _ in range(12):
        many.speech(5, 'x', [act('accuse', 7)])
    assert beliefs_for(once, 1).p(7) == pytest.approx(beliefs_for(many, 1).p(7), abs=1e-12)
    # 指认、号召、质疑全上，也不超过 speaker_cap
    heavy = day1()
    heavy.speech(5, 'x', [act('accuse', 7, weight=3), act('vote_intent', 7, weight=3), act('doubt_claim', 7, weight=3)])
    b = beliefs_for(heavy, 1, tr=traits(conformity=1.0))
    moved = sum(abs(e.delta) for e in b.evidence(7) if e.source == 5)
    assert moved <= b.tuning.speaker_cap + 1e-9
    # 第二天重新算
    heavy.night(2, kill=None)
    heavy.dawn([])
    heavy.speech(5, 'x', [act('accuse', 7)])
    b2 = beliefs_for(heavy, 1, tr=traits(conformity=1.0))
    assert sum(abs(e.delta) for e in b2.evidence(7) if e.source == 5) > moved


def test_accused_by_many() -> None:
    sc = day1()
    for s in (2, 3, 5):
        sc.speech(s, 'x', [act('accuse', 9)])
    assert ReasonCode.ACCUSED_BY_MANY in reasons(beliefs_for(sc, 1), 9)


def test_conformity_scales_accusations() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('accuse', 7)])
    assert beliefs_for(sc, 1, tr=traits(conformity=1.0)).p(7) > beliefs_for(sc, 1, tr=traits(conformity=0.0)).p(7)


def test_off_game_once_per_day() -> None:
    sc = day1()
    sc.speech(5, 'x', [], flags=['injection'])
    sc.speech(5, 'x', [], flags=['injection'])
    sc.speech(7, 'x', [], flags=['stage_direction'])
    b = beliefs_for(sc, 1)
    off = [e for e in b.evidence(5) if e.reason == ReasonCode.OFF_GAME]
    assert len(off) == 1
    assert off[0].delta <= b.w.accuse
    assert not [e for e in b.evidence(7) if e.reason == ReasonCode.OFF_GAME]
    sc.night(2, kill=None)
    sc.dawn([])
    sc.speech(5, 'x', [], flags=['injection'])
    assert len([e for e in beliefs_for(sc, 1).evidence(5) if e.reason == ReasonCode.OFF_GAME]) == 2


def test_contradiction_evidence_any_speaker() -> None:
    sc = day1()
    sc.speech(1, 'x', [act('claim_check', 9, result='good', night=3)])  # 真人座位也一样
    sc.speech(6, 'x', [act('claim_role', role=Role.WITCH), act('claim_save', 4, night=1)])
    b = beliefs_for(sc, 7)
    assert ReasonCode.CONTRADICTION in reasons(b, 1)
    assert ReasonCode.CONTRADICTION in reasons(b, 6)


def test_late_claim_little_credit() -> None:
    sc = day1()
    sc.vote({1: 7, 2: 9, 3: 7, 5: 9, 6: 7, 7: 9, 8: 1, 9: 7})  # 4 比 4
    sc.speech(7, 'x', [act('claim_role', role=Role.WITCH)], kind='pk')
    b = beliefs_for(sc, 1)
    assert ReasonCode.LATE_CLAIM in reasons(b, 7)
    sc2 = day1()
    sc2.speech(7, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    early = beliefs_for(sc2, 1)
    sc3 = day1()
    sc3.vote({1: 7, 2: 9, 3: 7, 5: 9, 6: 7, 7: 9, 8: 1, 9: 7})
    sc3.speech(7, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)], kind='pk')
    late = beliefs_for(sc3, 1)
    assert late.claim_trust(7) < 0.5 * early.claim_trust(7)


def test_silver_water_only_on_peaceful_night() -> None:
    ok = day1(peaceful=True)
    ok.speech(6, 'x', [act('claim_role', role=Role.WITCH), act('claim_save', 4, night=1)])
    b = beliefs_for(ok, 1)
    assert ReasonCode.SAVED_BY_WITCH in reasons(b, 4)
    bad = day1()
    bad.speech(6, 'x', [act('claim_role', role=Role.WITCH), act('claim_save', 7, night=1)])
    assert ReasonCode.SAVED_BY_WITCH not in reasons(beliefs_for(bad, 1), 7)


def test_quiet_pass() -> None:
    sc = day1()
    sc.speech(7, 'x', [act('pass')])
    assert ReasonCode.QUIET in reasons(beliefs_for(sc, 1), 7)


def test_defended_wolf() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    sc.speech(8, 'x', [act('vouch', 5)])
    assert ReasonCode.DEFENDED_WOLF in reasons(beliefs_for(sc, 1), 8)


def test_stubborn_discounts_vouch_for_top_suspect() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    sc.speech(9, 'x', [act('vouch', 5)])

    def vouch_delta(stubborn: float) -> float:
        b = beliefs_for(sc, 1, tr=traits(stubborn=stubborn))
        return sum(e.delta for e in b.evidence(5) if e.reason == ReasonCode.VOUCHED_BY_TRUSTED)

    assert vouch_delta(0.0) < vouch_delta(1.0) < 0
    assert vouch_delta(1.0) == pytest.approx(vouch_delta(0.0) * (1 - 0.7))


# ---------------------------------------------------------------- 不看真人、确定性


def test_swapping_is_human_changes_nothing() -> None:
    def build(human: int) -> Scenario:
        sc = Scenario(human=human)
        sc.night(1, kill=4, check=(3, 5, 'wolf'))
        sc.dawn([4])
        sc.speech(1, 'x', [act('accuse', 5)])
        sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 1, result='wolf', night=1)])
        sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
        sc.vote({1: 5, 2: 1, 3: 5, 5: 1, 6: 5, 7: 5, 8: 1, 9: 5}, exiled=5)
        return sc

    a, b = build(1), build(5)
    for seat in (2, 7, 9):
        ba, bb = beliefs_for(a, seat), beliefs_for(b, seat)
        assert ba.all('private') == bb.all('private') and ba.all('public') == bb.all('public')


def test_deterministic_and_incremental_with_or_without_ledger() -> None:
    sc = duel_scenario()
    a = beliefs_for(sc, 7)
    b = Beliefs(7, Role.VILLAGER, traits())
    for ev in visible(sc, 7):
        b.observe(ev, sc.view(7))  # 不给账本，用内部账本
        b.observe(ev, sc.view(7))  # 重复喂无副作用
    assert a.all() == b.all()


# ---------------------------------------------------------------- 查询、反思、存取


def test_ranked_and_evidence_order() -> None:
    b = beliefs_for(duel_scenario(), 7)
    r = b.ranked('private', living=[1, 2, 3, 5, 9])
    assert [s for s, _ in r][0] in (2, 5) and 7 not in [s for s, _ in r] and len(r) == 5
    assert [p for _, p in r] == sorted((p for _, p in r), reverse=True)
    ev = b.evidence(2)
    assert all(abs(ev[i].delta) >= abs(ev[i + 1].delta) for i in range(len(ev) - 1))
    assert all(e.event_id is not None for e in ev)


def test_nudge() -> None:
    b = beliefs_for(duel_scenario(), 3)
    p9 = b.p(9)
    d = b.nudge(9, 0.1)
    assert d > 0 and b.p(9) > p9
    assert b.evidence(9)[0].reason == ReasonCode.REFLECTION or ReasonCode.REFLECTION in reasons(b, 9)
    assert b.nudge(2, -0.5) == 0.0 and b.p(2) == 1.0  # 硬知识不动
    assert b.nudge(3, 0.2) == 0.0
    b.nudge(9, 0.9)
    assert b.p(9) <= 0.99


def test_dump_load_round_trip() -> None:
    sc = duel_scenario()
    evs = visible(sc, 3)
    half = len(evs) // 2
    full = beliefs_for(sc, 3)
    a = Beliefs(3, Role.SEER, traits())
    lg = ClaimLedger()
    for ev in evs[:half]:
        a.observe(ev, None, lg)
    a.nudge(9, 0.05)
    data = a.dump()
    b = Beliefs(3, Role.SEER, traits())
    b.load(data)
    assert b.all() == a.all() and b.all('public') == a.all('public')
    assert b.dump() == data
    for ev in evs[half:]:
        a.observe(ev, None, lg)
        b.observe(ev, sc.view(3))  # 恢复后的内部账本从视图重建
    assert b.all() == a.all()
    assert full.p(2) == b.p(2)
