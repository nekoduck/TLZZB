"""声明账本：查询与每一种矛盾。"""

from __future__ import annotations

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import Event, EventType, Phase, Role
from werewolf.testing import Scenario, act, duel_scenario


def kinds(ledger: ClaimLedger, seat: int) -> list[str]:
    return [c.kind for c in ledger.contradictions(seat)]


def day1() -> Scenario:
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 7, 'good'))
    sc.dawn([4])
    return sc


def test_duel_queries() -> None:
    sc = duel_scenario()
    lg = ClaimLedger.from_events(sc.events)
    assert lg.claimants(Role.SEER) == [3]
    assert lg.current_role(4) == Role.VILLAGER
    assert lg.current_role(5) == Role.VILLAGER
    assert lg.current_role(1) is None
    checks = lg.checks_by(3)
    assert [(c.target, c.result, c.night, c.day) for c in checks] == [(7, 'good', 1, 1)]
    assert lg.checks_on(7) == checks
    assert lg.checks_on(2) == []
    assert lg.vote_intents(1) == {8: 7}
    assert lg.vote_intents(2) == {}
    assert [c.seat for c in lg.role_claims(role=Role.VILLAGER)] == [4, 5]
    assert all(lg.contradictions(s) == [] for s in range(1, 10))
    assert lg.dawn_deaths(1) == [4] and lg.dawn_deaths(2) == [6] and lg.dawn_deaths(3) is None
    assert lg.death_of(8) == (1, 'vote')


def test_incremental_equals_batch_and_idempotent() -> None:
    sc = duel_scenario()
    a = ClaimLedger.from_events(sc.events)
    b = ClaimLedger()
    for ev in sc.events:
        b.add(ev)
        b.add(ev)  # 重复喂无副作用
    b.add(sc.events[3])  # 旧事件忽略
    assert a.role_claims() == b.role_claims()
    assert a.checks_by(3) == b.checks_by(3)
    assert a.last_event_id == b.last_event_id == sc.events[-1].id


def test_ignores_non_public_speech() -> None:
    sc = day1()
    ev = sc.speech(5, 'x', [act('claim_role', role=Role.SEER)])
    hidden = Event(id=ev.id + 1, day=1, phase=Phase.DAY_SPEECH, type=EventType.SPEECH, actor=6,
                   data=ev.data, vis='seats', vis_seats=[6])
    lg = ClaimLedger.from_events([*sc.events, hidden])
    assert lg.claimants(Role.SEER) == [5]


def test_repeated_claims_not_duplicated_and_claimants_order() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 9, result='wolf', night=1)])
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 7, result='good', night=1)])
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 9, result='wolf', night=1)])
    lg = ClaimLedger.from_events(sc.events)
    assert lg.claimants(Role.SEER) == [5, 3]
    assert len(lg.role_claims(5)) == 1 and len(lg.checks_by(5)) == 1
    assert lg.contradictions(5) == []
    sc.speech(5, 'x', [act('claim_role', role=Role.VILLAGER)])
    lg = ClaimLedger.from_events(sc.events)
    assert lg.claimants(Role.SEER) == [3]
    assert kinds(lg, 5) == ['role_change']


def test_vote_intents_last_one_wins() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('vote_intent', 7)])
    sc.speech(5, 'x', [act('vote_intent', 9)])
    sc.speech(2, 'x', [act('vote_intent', 3)])
    lg = ClaimLedger.from_events(sc.events)
    assert lg.vote_intents(1) == {5: 9, 2: 3}


def test_check_conflict() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 9, result='good', night=1)])
    sc.speech(5, 'x', [act('claim_check', 9, result='wolf', night=1)])
    assert kinds(ClaimLedger.from_events(sc.events), 5) == ['check_conflict']


def test_double_check_same_night() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_check', 9, result='good', night=1), act('claim_check', 7, result='good', night=1)])
    assert kinds(ClaimLedger.from_events(sc.events), 5) == ['double_check']


def test_future_night() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_check', 9, result='good', night=2)])
    sc.speech(6, 'x', [act('claim_check', 9, result='good', night=0)])
    lg = ClaimLedger.from_events(sc.events)
    assert kinds(lg, 5) == ['future_night']
    assert kinds(lg, 6) == ['future_night']


def test_missing_night_defaults_to_latest() -> None:
    sc = day1()
    sc.speech(5, 'x', [act('claim_check', 9, result='good')])
    lg = ClaimLedger.from_events(sc.events)
    assert lg.checks_by(5)[0].night == 1 and lg.contradictions(5) == []


def test_check_dead_and_self() -> None:
    sc = day1()
    sc.speech(3, 'x', [act('claim_check', 3, result='good', night=1)])
    sc.vote({1: 9, 2: 9, 3: 9, 5: 9, 6: 9, 7: 9, 8: 9, 9: 1}, exiled=9)
    sc.night(2, kill=None)
    sc.dawn([])
    # 4 号第 1 夜死、9 号第 1 天被放逐：第 2 夜开始时都已出局
    sc.speech(5, 'x', [act('claim_check', 4, result='good', night=2)])
    sc.speech(6, 'x', [act('claim_check', 9, result='wolf', night=2)])
    # 第 1 夜验当夜被刀的 4 号是可以的（死亡在天亮才结算）
    sc.speech(7, 'x', [act('claim_check', 4, result='good', night=1)])
    lg = ClaimLedger.from_events(sc.events)
    assert kinds(lg, 3) == ['check_self']
    assert kinds(lg, 5) == ['check_dead']
    assert kinds(lg, 6) == ['check_dead']
    assert kinds(lg, 7) == []


def test_save_died_and_poison_alive() -> None:
    sc = day1()
    sc.speech(6, 'x', [act('claim_role', role=Role.WITCH), act('claim_save', 4, night=1)])
    sc.speech(9, 'x', [act('claim_poison', 7, night=1)])
    sc.speech(2, 'x', [act('claim_poison', 4, night=1)])  # 4 号确实死了：不矛盾
    lg = ClaimLedger.from_events(sc.events)
    assert kinds(lg, 6) == ['save_died']
    assert kinds(lg, 9) == ['poison_alive']
    assert kinds(lg, 2) == []
    assert [(p.kind, p.target, p.night) for p in lg.potions_by(6)] == [('save', 4, 1)]


def test_save_on_peaceful_night_ok_and_potion_extras() -> None:
    sc = Scenario()
    sc.night(1, kill=4, save=True)
    sc.dawn([])
    sc.speech(6, 'x', [act('claim_role', role=Role.WITCH), act('claim_save', 4, night=1)])
    lg = ClaimLedger.from_events(sc.events)
    assert lg.contradictions(6) == []
    sc.speech(6, 'x', [act('claim_save', 7, night=1)])
    lg = ClaimLedger.from_events(sc.events)
    assert set(kinds(lg, 6)) == {'double_save'}


def test_contradiction_text_is_neutral() -> None:
    sc = day1()
    sc.speech(5, '我是狼哈哈', [act('claim_role', role=Role.VILLAGER)])
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER)])
    c = ClaimLedger.from_events(sc.events).contradictions(5)[0]
    assert '5 号' in c.text and '哈哈' not in c.text and c.event_id == sc.events[-1].id
