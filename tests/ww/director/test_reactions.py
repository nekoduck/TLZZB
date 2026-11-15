from werewolf.director.reactions import BYPASS_INTENSITY, COOLDOWN_EVENTS, ReactionGate, label, pick
from werewolf.kernel.types import EMOTES
from werewolf.mind.types import Reaction


def r(seat: int, intensity: float = 0.5, event_id: int = 10, emote: str = 'frown') -> Reaction:
    return Reaction(seat=seat, emote=emote, intensity=intensity, event_id=event_id)


def test_limit_and_intensity_order() -> None:
    rs = [r(1, 0.2), r(2, 0.9), r(3, 0.5), r(4, 0.7), r(5, 0.6)]
    got = pick(rs, {})
    assert [x.seat for x in got] == [2, 4, 5]
    assert [x.seat for x in pick(rs, {}, limit=1)] == [2]
    assert pick(rs, {}, limit=0) == []
    assert len(pick(rs, {}, limit=10)) == 5


def test_tie_break_by_seat() -> None:
    rs = [r(7, 0.5), r(3, 0.5), r(5, 0.5), r(1, 0.5)]
    assert [x.seat for x in pick(rs, {})] == [1, 3, 5]
    assert [x.seat for x in pick(list(reversed(rs)), {})] == [1, 3, 5]


def test_one_per_seat_keeps_strongest() -> None:
    rs = [r(2, 0.3, emote='nod'), r(2, 0.9, emote='shock'), r(4, 0.4)]
    got = pick(rs, {})
    assert [(x.seat, x.emote) for x in got] == [(2, 'shock'), (4, 'frown')]


def test_cooldown() -> None:
    assert COOLDOWN_EVENTS >= 2
    recent = {2: 10 - (COOLDOWN_EVENTS - 1), 3: 10 - COOLDOWN_EVENTS}
    snapshot = dict(recent)
    rs = [r(2, 0.6), r(3, 0.5), r(4, 0.4)]
    got = pick(rs, recent)
    assert [x.seat for x in got] == [3, 4]  # 2 号还在冷却；3 号刚好满了
    assert recent == snapshot  # 不改调用方的表


def test_strong_reaction_bypasses_cooldown() -> None:
    recent = {2: 9}
    assert [x.seat for x in pick([r(2, BYPASS_INTENSITY)], recent)] == [2]
    assert pick([r(2, BYPASS_INTENSITY - 0.01)], recent) == []


def test_cooldown_skips_without_eating_the_limit() -> None:
    recent = {1: 10, 2: 10}
    rs = [r(1, 0.7), r(2, 0.7), r(3, 0.3), r(4, 0.2), r(5, 0.1)]
    assert [x.seat for x in pick(rs, recent, limit=2)] == [3, 4]


def test_gate_tracks_recent() -> None:
    gate = ReactionGate(limit=2)
    assert [x.seat for x in gate.pick([r(1, 0.5, 10), r(2, 0.4, 10)])] == [1, 2]
    assert gate.recent == {1: 10, 2: 10}
    assert [x.seat for x in gate.pick([r(1, 0.5, 11), r(3, 0.3, 11)])] == [3]
    assert [x.seat for x in gate.pick([r(1, 0.5, 10 + COOLDOWN_EVENTS)])] == [1]
    gate.reset()
    assert gate.recent == {}


def test_label() -> None:
    for key, zh in EMOTES.items():
        assert label(key) == zh
    assert label('nod') == '点头'
    assert label('dance') == ''
