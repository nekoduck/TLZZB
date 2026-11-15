"""记忆槽：只由事件产生、淘汰、升入长期、检索、衰减与模糊、私有标记、存取。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from werewolf.mind.memory import LONG_SLOTS, SHORT_SLOTS, MemoryStore
from werewolf.mind.types import Memory
from werewolf.testing import Scenario, act, duel_scenario

from .helpers import traits, visible


def store_for(sc: Scenario, seat: int, **kw: float) -> MemoryStore:
    ms = MemoryStore(seat, traits(**kw))
    for ev in visible(sc, seat):
        ms.observe(ev)
    return ms


def mem(eid: int, day: int, intensity: float, about: list[int] | None = None, kind: str = 'x',
        private: bool = False) -> Memory:
    return Memory(event_id=eid, day=day, kind=kind, about=about or [], text=f'e{eid}', intensity=intensity,
                  private=private)


def test_event_id_mandatory() -> None:
    with pytest.raises(ValidationError):
        Memory(day=1, kind='x', text='t', intensity=1)  # type: ignore[call-arg]


def test_memories_from_events_and_private_flag() -> None:
    sc = duel_scenario()
    seer = store_for(sc, 3)
    kinds = {m.kind for m in seer.all()}
    assert 'my_check' in kinds and 'accused_me' in kinds
    my = [m for m in seer.all() if m.kind == 'my_check']
    assert my and all(m.private for m in my)
    assert all(not m.private for m in seer.all() if m.kind == 'accused_me')
    ids = {e.id for e in visible(sc, 3)}
    assert all(m.event_id in ids for m in seer.all())
    wolf = store_for(sc, 2)
    assert any(m.kind == 'teammates' and m.private and m.about == [5, 8] for m in wolf.all())
    assert all(m.kind != 'my_check' for m in wolf.all())


def test_voted_me_memory_and_vengeful_intensity() -> None:
    sc = Scenario()
    sc.night(1, kill=4)
    sc.dawn([4])
    sc.vote({1: 7, 2: 7, 3: 9, 5: 7, 6: 9, 7: 9, 8: 7, 9: 7}, exiled=7)
    hi = store_for(sc, 9, vengeful=1.0)
    lo = store_for(sc, 9, vengeful=0.0)
    vh = [m for m in hi.all() if m.kind == 'voted_me']
    vl = [m for m in lo.all() if m.kind == 'voted_me']
    assert sorted(a for m in vh for a in m.about) == [3, 6, 7]
    assert vh[0].intensity > vl[0].intensity


def test_short_eviction_by_intensity() -> None:
    ms = MemoryStore(1)
    for i in range(SHORT_SLOTS):
        assert ms.add(mem(i + 1, 1, 2.0 + i))
    assert not ms.add(mem(100, 1, 1.0))  # 比谁都弱：自己被挤掉
    assert not ms.has(100)
    assert ms.add(mem(101, 1, 9.0))
    assert not ms.has(1) and ms.has(101)  # 最弱的 1 号被挤掉
    assert len(ms.all()) == SHORT_SLOTS
    assert not ms.add(mem(101, 1, 9.0))  # 同一（事件，种类）不重复


def test_eviction_prefers_faded_old_memories() -> None:
    ms = MemoryStore(1)
    ms.add(mem(1, 1, 6.0))
    for i in range(SHORT_SLOTS - 1):
        ms.add(mem(10 + i, 5, 5.0))
    ms.add(mem(50, 5, 5.5))
    assert not ms.has(1)  # 第 1 天的 6 分到第 5 天只剩 1.5


def test_consolidate_promotes_strongest_of_day() -> None:
    ms = MemoryStore(1)
    ms.add(mem(1, 1, 9.0))
    ms.add(mem(2, 1, 7.0))
    ms.add(mem(3, 1, 5.0))
    ms.add(mem(4, 1, 2.0))
    ms.add(mem(5, 2, 10.0))
    up = ms.consolidate(1)
    assert [m.event_id for m in up] == [1, 2] and all(m.slot == 'long' for m in up)
    assert [m.event_id for m in ms.all() if m.slot == 'long'] == [1, 2]
    assert ms.has(3) and ms.has(5)


def test_long_slots_eviction() -> None:
    ms = MemoryStore(1)
    for d in range(1, LONG_SLOTS + 1):
        ms.add(mem(d * 10, d, 4.0 + d * 0.1))
        ms.consolidate(d)
    assert len([m for m in ms.all() if m.slot == 'long']) == LONG_SLOTS
    ms.add(mem(999, 9, 9.0))
    ms.consolidate(9)
    longs = [m.event_id for m in ms.all() if m.slot == 'long']
    assert 999 in longs and 10 not in longs and len(longs) == LONG_SLOTS


def test_recall_ordering() -> None:
    ms = MemoryStore(1)
    ms.add(mem(1, 1, 8.0, [5]))
    ms.add(mem(2, 2, 4.0, [3]))
    ms.add(mem(3, 2, 8.0, [7]))
    ms.add(mem(4, 2, 2.0, [9], private=True))
    assert [m.event_id for m in ms.recall(None, 2, k=2)] == [3, 2]
    assert ms.recall(5, 2, k=1)[0].event_id == 1  # 相关度盖过新近度
    assert [m.event_id for m in ms.recall([9, 3], 2, k=2)] == [2, 4]
    assert 4 not in [m.event_id for m in ms.recall([9], 2, include_private=False)]
    assert len(ms.recall(None, 2)) == 4


def test_strength_decay_and_fuzzy() -> None:
    ms = MemoryStore(1)
    ms.add(mem(1, 1, 8.0))
    assert not ms.is_fuzzy(1, 1) and not ms.is_fuzzy(1, 2)
    assert ms.strength(ms.get(1)[0], 3) == pytest.approx(4.0)
    assert ms.is_fuzzy(1, 4)
    ms.consolidate(1)  # 长期记忆衰减得慢
    assert not ms.is_fuzzy(1, 4) and ms.is_fuzzy(1, 7)
    assert ms.is_fuzzy(12345, 1) and not ms.has(12345)


def test_dump_load_round_trip() -> None:
    sc = duel_scenario()
    ms = store_for(sc, 7)
    ms.consolidate(1)
    other = MemoryStore(7, traits())
    other.load(ms.dump())
    assert other.all() == ms.all() and other.dump() == ms.dump()
    assert other.recall(2, 2) == ms.recall(2, 2)


def test_observe_idempotent_and_never_reads_text() -> None:
    sc = Scenario()
    sc.night(1, kill=4)
    sc.dawn([4])
    sc.speech(5, '我觉得7号是狼', [])
    sc.speech(8, '无所谓', [act('accuse', 7)])
    ms = MemoryStore(7)
    evs = visible(sc, 7)
    for ev in evs:
        ms.observe(ev)
        assert ms.observe(ev) == []
    assert [m.about for m in ms.all() if m.kind == 'accused_me'] == [[8]]
