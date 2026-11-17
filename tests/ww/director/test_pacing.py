from statistics import mean

import pytest

from werewolf.director.pacing import (
    HOLD_RANGES,
    SPEED_FACTOR,
    counter_claim,
    hold_kinds,
    hold_ms,
    pace,
    tension,
    vote_closeness,
)
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import PublicView, Role
from werewolf.mind.types import Pace
from werewolf.testing import Scenario, act, duel_scenario

KINDS = ['phase', 'narration', 'dawn', 'death', 'vote_reveal', 'exile', 'night_step', 'skill', 'deal', 'reaction',
         'game_over']


def fresh_day1(deaths: list[int] | None = None) -> PublicView:
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 7, 'good'))
    sc.dawn([4] if deaths is None else deaths)
    sc.start_speeches()
    return sc.public_view()


def duel_with_counter() -> Scenario:
    sc = duel_scenario()
    sc.speech(5, '我才是预言家，3号是悍跳的。', [act('claim_role', role=Role.SEER, weight=3),
                                               act('claim_check', 3, result='wolf', night=2)])
    return sc


# ---------------------------------------------------------------- tension


def test_tension_in_range_and_monotone_over_a_game() -> None:
    pv1 = fresh_day1()
    pv2 = duel_scenario().public_view()
    for pv in (pv1, pv2, duel_with_counter().public_view()):
        assert 0.0 <= tension(pv) <= 1.0
    assert tension(pv2) > tension(pv1)


def test_tension_rises_with_day_alone() -> None:
    pv = fresh_day1()
    later = pv.model_copy(update={'day': 3})
    much_later = pv.model_copy(update={'day': 9})
    assert tension(pv) < tension(later) < tension(much_later) <= 1.0


def test_tension_rises_with_counter_claim() -> None:
    base = duel_scenario().public_view()
    assert not counter_claim(base)
    pv = duel_with_counter().public_view()
    assert counter_claim(pv)
    assert tension(pv) > tension(base) + 0.25


def test_counter_claim_needs_both_alive_and_latest_claim() -> None:
    # 8 号质疑 3 号但没自称预言家，且已出局 → 不算
    sc = duel_scenario()
    assert not counter_claim(sc.public_view())
    # 改口：5 号先跳预言家再改口平民 → 不算
    sc2 = duel_with_counter()
    sc2.speech(5, '算了我是平民。', [act('claim_role', role=Role.VILLAGER)])
    assert not counter_claim(sc2.public_view())
    # 平民对跳不算（不是神职）
    sc3 = duel_scenario()
    sc3.speech(1, '我是平民。', [act('claim_role', role=Role.VILLAGER)])
    assert not counter_claim(sc3.public_view())


def test_vote_closeness() -> None:
    sc = Scenario()
    sc.night(1, kill=4)
    sc.dawn([4])
    assert vote_closeness(sc.public_view()) == 0.0
    sc.vote({1: 8, 2: 6, 3: 8, 5: 6, 6: 8, 7: 8, 8: 6, 9: None}, exiled=8)  # 4 比 3，8 人投
    assert vote_closeness(sc.public_view()) == pytest.approx(1 - 1 / 8)
    sc2 = Scenario()
    sc2.night(1, kill=4)
    sc2.dawn([4])
    sc2.vote({1: 8, 2: 8, 3: 8, 5: 8, 6: 8, 7: 8, 8: 6, 9: 8}, exiled=8)  # 7 比 1
    close, wide = tension(sc.public_view()), tension(sc2.public_view())
    assert vote_closeness(sc2.public_view()) == pytest.approx(1 - 6 / 8)
    assert close > wide


def test_tension_rises_as_players_die() -> None:
    pv = fresh_day1(deaths=[])
    more = fresh_day1(deaths=[4, 6])
    assert tension(more) > tension(pv)


def test_tension_ignores_hidden_info() -> None:
    # 同一串公开动作，换一套身份分配（夜里的私有事件也不同），紧张度与节奏相同
    other = {1: Role.WEREWOLF, 2: Role.VILLAGER, 3: Role.HUNTER, 4: Role.VILLAGER, 5: Role.WITCH,
             6: Role.SEER, 7: Role.WEREWOLF, 8: Role.VILLAGER, 9: Role.WEREWOLF}

    def play(roles: dict[int, Role] | None, check: tuple[int, int, str]) -> PublicView:
        sc = Scenario(roles=roles)
        sc.night(1, kill=4, check=check)
        sc.dawn([4])
        sc.speech(5, '我是预言家。', [act('claim_role', role=Role.SEER)])
        sc.speech(6, '我才是预言家。', [act('claim_role', role=Role.SEER)])
        sc.vote({1: 5, 2: 6, 3: 5, 5: 6, 6: 5, 7: 6, 8: 5, 9: 6})
        return sc.public_view()

    a, b = play(None, (3, 7, 'good')), play(other, (6, 1, 'wolf'))
    assert tension(a) == tension(b)
    assert pace(a) == pace(b)


# ---------------------------------------------------------------- pace


def test_pace_fresh_day1_is_short() -> None:
    p = pace(fresh_day1())
    assert isinstance(p, Pace)
    assert p.length_bias == 'short'
    assert set(p.max_chars) == {'short', 'medium', 'long'}
    assert p.max_chars['short'] < p.max_chars['medium'] < p.max_chars['long']
    assert p.max_chars['long'] <= 180


def test_pace_duel() -> None:
    p = pace(duel_scenario().public_view())
    assert p.length_bias != 'short'
    hot = pace(duel_with_counter().public_view())
    assert hot.length_bias == 'long'
    # 人少了，字数上限不比满员时小
    assert hot.max_chars['medium'] >= pace(fresh_day1(deaths=[])).max_chars['medium']


def test_pace_late_game_is_medium() -> None:
    sc = duel_with_counter()
    sc.vote({1: 2, 2: 7, 3: 2, 5: 7, 7: 2, 9: 2}, exiled=2)
    sc.night(3, kill=7)
    sc.dawn([7, 9])
    pv = sc.public_view()
    assert len(pv.living()) <= 4
    assert pace(pv).length_bias == 'medium'
    assert pace(pv).max_chars == {'short': 60, 'medium': 120, 'long': 180}


def test_pace_shrinks_with_full_table() -> None:
    full = pace(fresh_day1(deaths=[]))
    assert full.max_chars['medium'] < 120
    assert full.max_chars['short'] >= 45


# ---------------------------------------------------------------- hold_ms


def test_hold_instant_is_zero() -> None:
    rng = Rng(1)
    for k in KINDS + ['unknown_kind']:
        assert hold_ms(k, 'instant', rng) == 0


def test_hold_known_kinds_and_default() -> None:
    assert set(KINDS) <= set(hold_kinds())
    rng = Rng(2)
    for k in KINDS + ['whatever']:
        v = hold_ms(k, 'normal', rng)
        assert 0 < v < 10_000


def test_hold_scales_with_speed() -> None:
    for k in KINDS:
        vals = {s: hold_ms(k, s, Rng(5, k)) for s in ('slow', 'normal', 'fast')}
        assert vals['slow'] == pytest.approx(vals['normal'] * 1.5, abs=1)
        assert vals['fast'] == pytest.approx(vals['normal'] * 0.5, abs=1)
    assert SPEED_FACTOR == {'slow': 1.5, 'normal': 1.0, 'fast': 0.5, 'instant': 0.0}


def test_hold_consumes_rng_the_same_for_every_speed() -> None:
    a, b = Rng(9), Rng(9)
    hold_ms('night_step', 'instant', a)
    hold_ms('night_step', 'slow', b)
    assert a.random() == b.random()


@pytest.mark.parametrize('kind', ['night_step', 'skill'])
def test_hold_night_and_skill_have_jitter_and_fixed_distribution(kind: str) -> None:
    rng = Rng(11)
    vals = [hold_ms(kind, 'normal', rng) for _ in range(400)]
    lo, hi = HOLD_RANGES[kind]
    assert all(lo <= v <= hi for v in vals)
    assert len(set(vals)) > 100  # 有抖动
    assert mean(vals) == pytest.approx((lo + hi) / 2, rel=0.05)
    # 只取决于 (kind, speed, rng)：同一条流重放，结果逐个相同
    again = Rng(11)
    assert [hold_ms(kind, 'normal', again) for _ in range(400)] == vals


def test_hold_signature_takes_no_game_state() -> None:
    import inspect

    assert list(inspect.signature(hold_ms).parameters) == ['kind', 'speed', 'rng']
