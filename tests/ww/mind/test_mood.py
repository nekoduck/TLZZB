"""念头、心情、压力、状态与交情。"""

from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.mind.mood import Mood
from werewolf.mind.types import PublicMood, Traits
from werewolf.testing import Scenario, act, duel_scenario

from .helpers import traits, visible


def mood_for(sc: Scenario, seat: int, tr: Traits | None = None, public_only: bool = False) -> Mood:
    m = Mood(seat, tr or traits())
    for ev in visible(sc, seat, public_only):
        m.observe(ev, None, None)
    return m


def day1() -> Scenario:
    sc = Scenario()
    sc.night(1, kill=4)
    sc.dawn([4])
    sc.start_speeches()
    return sc


def test_baseline_steady() -> None:
    m = mood_for(day1(), 7)
    assert m.value() == 50.0 and m.state() == 'steady' and m.stress() == 0.0
    assert m.public() == PublicMood(state='steady', mood=50.0, hints=[])
    assert m.top_cause() is None


def test_accused_creates_thought_stress_and_trust() -> None:
    sc = day1()
    ev = sc.speech(5, 'x', [act('accuse', 7)])
    m = mood_for(sc, 7)
    t = m.top_cause()
    assert t is not None and t.key == 'accused_by:5' and t.about == 5 and t.event_id == ev.id and not t.private
    assert m.value() < 50 and m.stress() > 0 and m.trust(5) < 0
    assert m.public().hints == [t.text]


def test_vengeful_scales_thought_and_trust() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('accuse', 7)])
    sc.vote({1: 7, 2: 5, 3: 5, 5: 7, 6: 5, 7: 5, 8: 5, 9: 5}, exiled=5)
    hi = mood_for(sc, 7, traits(vengeful=1.0))
    lo = mood_for(sc, 7, traits(vengeful=0.0))
    assert hi.value() < lo.value()
    assert hi.trust(5) < lo.trust(5) < 0
    assert hi.trust(1) < lo.trust(1) < 0


def test_tilted_tense_confident_thresholds() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='wolf', night=1)])
    for s in (2, 8, 9):
        sc.speech(s, 'x', [act('accuse', 7)])
    sc.vote({1: 7, 2: 7, 3: 7, 5: 7, 6: 1, 7: 5, 8: 7, 9: 7}, exiled=7)
    m = mood_for(sc, 7)
    assert m.value() <= 25 and m.state() == 'tilted'
    assert m.stress() >= 60  # tilted 优先于 tense

    sc2 = day1()
    sc2.speech(5, 'x', [act('accuse', 7)])
    sc2.speech(8, 'x', [act('accuse', 7)])
    for s in (1, 3, 9):
        sc2.speech(s, 'x', [act('vouch', 7)])
    sc2.vote({1: 5, 2: 7, 3: 5, 5: 7, 6: 5, 7: 5, 8: 7, 9: 7})  # 4 比 4，7 号上 PK 台
    m2 = mood_for(sc2, 7, traits(vengeful=0.0))
    assert m2.value() > 25 and m2.stress() >= 60 and m2.state() == 'tense'

    sc3 = day1()
    sc3.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='good', night=1)])
    for s in (1, 2, 9):
        sc3.speech(s, 'x', [act('vouch', 7)])
    m3 = mood_for(sc3, 7)
    assert m3.value() >= 70 and m3.state() == 'confident'
    assert m3.trust(3) > 0 and m3.trust(9) > 0


def test_thought_expiry_and_relief() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('accuse', 7)])
    m = mood_for(sc, 7)
    t = m.top_cause()
    assert t is not None and t.expires_day is not None
    stress = m.stress()
    m.new_day(2)
    assert m.stress() < stress
    assert any(x.key == 'accused_by:5' for x in m.thoughts())
    m.new_day(t.expires_day + 1)
    assert not any(x.key == 'accused_by:5' for x in m.thoughts())
    assert m.value() == 50.0 and m.stress() == 0.0
    m.new_day(t.expires_day + 1)  # 重复调用无副作用


def test_accused_two_days_running() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('accuse', 7)])
    sc.night(2, kill=None)
    sc.dawn([])
    sc.speech(5, 'x', [act('accuse', 7)])
    m = mood_for(sc, 7)
    t = next(x for x in m.thoughts() if x.key == 'accused_by:5')
    assert '连着两天' in t.text and t.day == 2


def test_private_thoughts_excluded_from_public() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 5, result='wolf', night=1)])
    sc.vote({1: 5, 2: 3, 3: 5, 5: 3, 6: 5, 7: 5, 8: 5, 9: 5}, exiled=5)
    wolf = mood_for(sc, 2)
    keys = {t.key for t in wolf.thoughts('private')}
    assert {'mate_exposed:5', 'mate_died:5'} <= keys
    assert all(not t.private for t in wolf.thoughts('public'))
    assert wolf.value('private') < wolf.value('public')
    assert wolf.stress('private') > wolf.stress('public')
    # 公开心智与同座位、只看公开事件的平民相同
    vil = mood_for(sc, 2, public_only=True)
    assert wolf.public() == vil.public()
    assert wolf.value('public') == vil.value('public') and wolf.stress('public') == vil.stress('public')
    assert all('队友' not in h for h in wolf.public().hints)


def test_public_mood_same_for_any_role_in_seat() -> None:
    def build(roles: dict[int, Role]) -> Scenario:
        sc = Scenario(roles=roles)
        sc.night(1, kill=4, check=(sc.seat_of(Role.SEER), 2, 'wolf'))
        sc.dawn([4])
        sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 2, result='wolf', night=1)])
        sc.speech(5, 'x', [act('accuse', 2)])
        sc.vote({1: 2, 2: 3, 3: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 2}, exiled=2)
        return sc

    base = build({})
    ref = mood_for(base, 2).public()
    for role in Role:
        roles = dict(base.roles)
        other = next(s for s, r in sorted(roles.items()) if r == role)
        roles[2], roles[other] = role, roles[2]
        assert mood_for(build(roles), 2).public() == ref


def test_seer_found_wolf_private() -> None:
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 5, 'wolf'))
    sc.dawn([4])
    m = mood_for(sc, 3)
    assert any(t.key == 'found_wolf:5' and t.private for t in m.thoughts())
    assert m.value('private') > m.value('public')


def test_claim_contested_and_pk() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER)])
    sc.speech(7, 'x', [act('claim_role', role=Role.SEER)])
    sc.vote({1: 3, 2: 7, 3: 7, 5: 3, 6: 7, 7: 3, 8: 1, 9: 2})  # 3 比 3 进 PK
    m = mood_for(sc, 3)
    keys = {t.key for t in m.thoughts('public')}
    assert 'claim_contested:7' in keys and 'on_pk' in keys
    assert m.stress('public') > 0


def test_trust_up_down_decay() -> None:
    sc = day1()
    sc.speech(9, 'x', [act('vouch', 7)])
    sc.speech(5, 'x', [act('accuse', 7)])
    sc.vote({1: 5, 2: 7, 3: 5, 5: 7, 6: 5, 7: 5, 8: 5, 9: 5}, exiled=5)
    m = mood_for(sc, 7)
    assert m.trust(9) > 0 and m.trust(5) < 0 and m.trust(2) < 0
    assert m.trust(1) > 0  # 与我同票
    assert all(-1 <= v <= 1 for v in m.all_trust().values()) and 7 not in m.all_trust()
    assert any(t.key == 'push_won' for t in m.thoughts())
    t9, t5 = m.trust(9), m.trust(5)
    m.new_day(2)
    assert 0 < m.trust(9) < t9 and t5 < m.trust(5) < 0
    # 记仇的人旧怨散得慢
    hi, lo = mood_for(sc, 7, traits(vengeful=1.0)), mood_for(sc, 7, traits(vengeful=0.0))
    r_hi, r_lo = hi.trust(5), lo.trust(5)
    hi.new_day(2)
    lo.new_day(2)
    assert hi.trust(5) / r_hi > lo.trust(5) / r_lo


def test_trusted_player_dying() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='good', night=1)])
    sc.speech(9, 'x', [act('vouch', 7)])
    sc.night(2, kill=3)
    sc.dawn([3])
    m = mood_for(sc, 7)
    assert any(t.key == 'lost_friend:3' and not t.private for t in m.thoughts())


def test_strain_private_only() -> None:
    m = mood_for(day1(), 7)
    m.strain(30, 'forced_claim', '被迫亮了身份')
    assert m.stress('private') == 30 and m.stress('public') == 0
    assert m.top_cause('private') is not None and m.top_cause('private').key == 'forced_claim'  # type: ignore[union-attr]
    assert m.top_cause('public') is None and m.public().hints == []


def test_dump_load_round_trip() -> None:
    sc = duel_scenario()
    evs = visible(sc, 2)
    a = Mood(2, traits())
    for ev in evs[:len(evs) // 2]:
        a.observe(ev)
    a.strain(10, 'k', '违心')
    b = Mood(2, traits())
    b.load(a.dump())
    assert b.dump() == a.dump()
    for ev in evs[len(evs) // 2:]:
        a.observe(ev)
        b.observe(ev)
    assert b.dump() == a.dump() and b.public() == a.public()
