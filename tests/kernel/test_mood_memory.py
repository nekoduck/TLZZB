"""方案 5.5 的情绪与关系表、档位文字；5.6 的记忆写入、salience 表与淘汰；可转述的事实；重新打算的队列。"""

from __future__ import annotations

from tianlong.kernel import events, memory, mood
from tianlong.kernel.state import MemoryItem

from .conftest import World


def emit(w: World, *kinds: dict, actor: str | None = "fighter", loc: str = "hall") -> object:
    return events.emit(
        w.s,
        w.defs,
        actor=actor,
        verb="x",
        text="⟦fighter⟧做了点什么",
        loc=loc,
        source="rule",
        kinds=list(kinds),
    )


def test_table_rows(w: World) -> None:
    g, f, p = w.c("guest"), w.c("fighter"), w.c("player")
    emit(w, events.kind("insult", who="guest", by="fighter"))
    assert g.mood["anger"] == 20 and g.rel["fighter"].affinity == -10
    w.fx([{"rel": ["guest", "fighter", "regard", 2]}])
    emit(w, events.kind("threaten", who="guest", by="fighter"))
    assert g.mood["fear"] == 20 and g.rel["fighter"].fear == 20
    emit(w, events.kind("threaten", who="fighter", by="guest"))
    assert f.mood["anger"] == 15
    emit(w, events.kind("helped", who="player", by="guest"))
    assert (p.rel["guest"].trust, p.rel["guest"].affinity) == (20, 25)
    emit(w, events.kind("rescue", who="player", by="guest"))
    assert (p.rel["guest"].trust, p.rel["guest"].affinity) == (50, 50) and p.owes["guest"] == 1
    emit(w, events.kind("taught", who="girl", by="guest"))
    assert (w.c("girl").rel["guest"].trust, w.c("girl").rel["guest"].affinity) == (15, 25)
    emit(w, events.kind("deceived", who="fighter", by="player"))
    assert f.mood["anger"] == 35 and f.mood["suspicion"] == 20 and f.rel["player"].trust == -25
    emit(w, events.kind("promise_kept", who="fighter", by="player"))
    assert f.rel["player"].trust == -10
    emit(w, events.kind("promise_broken", who="fighter", by="player"))
    assert f.rel["player"].trust == -30
    emit(w, events.kind("nag", who="guest", by="player"))
    assert g.mood["anger"] == 30


def test_friend_in_danger_and_death(w: World) -> None:
    w.c("player").rel["guest"].affinity = 30
    emit(w, events.kind("harmed", who="guest", by="fighter"))
    assert w.c("player").mood["fear"] == 20
    emit(w, events.kind("death", who="girl", by="fighter"))
    assert w.c("player").mood["fear"] == 45 and w.c("fighter").mood["fear"] == 25


def test_comfort_once_per_hour(w: World) -> None:
    g = w.c("guest")
    g.mood["fear"] = 40
    emit(w, events.kind("comfort", who="guest", by="player"))
    emit(w, events.kind("comfort", who="guest", by="player"))
    assert g.mood["fear"] == 30 and g.rel["player"].affinity == 25


def test_show_power_updates_regard_and_fear(w: World) -> None:
    w.c("player").rel["fighter"] = w.c("player").rel["guest"].model_copy(update={"regard": 0, "fear": 0})
    emit(w, events.kind("show_power", who="fighter"))
    assert w.c("player").rel["fighter"].regard == 2 and w.c("player").rel["fighter"].fear == 15
    assert w.c("girl").rel["fighter"].fear == 0  # 只高出 1


def test_band_texts() -> None:
    assert [mood.trust_text(v) for v in (-41, -40, 9, 10, 49, 50)] == [
        "信不过",
        "不熟",
        "不熟",
        "有几分信",
        "有几分信",
        "信得过",
    ]
    assert [mood.regard_text(v) for v in (0, 1, 2, 3, 4, 5)] == [
        "不会武功",
        "有些身手",
        "有些身手",
        "是个好手",
        "是个好手",
        "深不可测",
    ]
    assert mood.fear_text(10) == "不怕" and mood.fear_text(85) == "怕极了"
    assert mood.affinity_text(-40) == "厌恶" and mood.affinity_text(35) == "很有好感"
    assert mood.relative_regard_text(4, 1) == "身手远比你强"


def test_mood_text(w: World) -> None:
    g = w.c("guest")
    assert mood.mood_text(g) == "心境平和"
    g.mood.update({"anger": 30, "fear": 90})
    assert mood.mood_text(g) == "有些恼、惊恐万状"
    assert mood.rel_text(g, "player") == "有几分信；有几分好感；不怕"


def test_memory_written_per_witness_with_salience(w: World) -> None:
    ev = emit(w, events.kind("harmed", who="guest", by="fighter"))
    sal = {cid: w.c(cid).memory[-1].salience for cid in ("guest", "fighter", "player", "girl")}
    assert sal == {"guest": 9, "fighter": 9, "player": 6, "girl": 6}
    assert w.c("fighter").memory[-1].kind == "did" and w.c("guest").memory[-1].kind == "saw"
    assert w.c("guest").memory[-1].event == ev.id  # type: ignore[attr-defined]
    emit(w, events.kind("death", who="girl"))
    assert w.c("player").memory[-1].salience == 10
    emit(w, events.kind("pass"))
    assert w.c("player").memory[-1].salience == 1
    w.say("player", "greet", ["guest"])
    assert w.c("guest").memory[-1].kind == "told" and w.c("guest").memory[-1].salience == 3


def test_salience_table_from_world_yaml(w: World) -> None:
    t = memory.table(w.defs)
    assert t == {
        "death": 10,
        "harmed": 9,
        "rescue": 8,
        "promise": 7,
        "exposed": 7,
        "insult": 6,
        "learn_key": 6,
        "gift": 5,
        "talk": 3,
        "pass": 1,
    }


def test_memory_cap_and_eviction(w: World) -> None:
    c = w.c("guest")
    for i in range(150):
        c.memory.append(MemoryItem(tick=0, kind="saw", text=f"old{i}", entities=[], salience=1))
    c.memory.append(MemoryItem(tick=0, kind="reflection", text="想通了一件事", entities=[], salience=1))
    for i in range(49):
        c.memory.append(MemoryItem(tick=500, kind="saw", text=f"new{i}", entities=[], salience=5))
    w.s.tick = 600
    memory.write(w.s, c, MemoryItem(tick=600, kind="saw", text="latest", entities=[], salience=2))
    assert len(c.memory) == 200
    assert c.memory[0].text == "old1"  # 最旧的低分记忆先丢
    memory.write(w.s, c, MemoryItem(tick=600, kind="saw", text="x", entities=[], salience=2))
    assert any(m.kind == "reflection" for m in c.memory)
    # 都还新：按分量从低到旧丢；反思不丢
    d = w.c("girl")
    d.memory = [MemoryItem(tick=599, kind="reflection", text="r", entities=[], salience=1)]
    d.memory += [
        MemoryItem(tick=599, kind="saw", text=f"m{i}", entities=[], salience=2 + i % 3) for i in range(199)
    ]
    memory.write(w.s, d, MemoryItem(tick=600, kind="saw", text="z", entities=[], salience=9))
    assert len(d.memory) == 200 and d.memory[0].kind == "reflection" and d.memory[-1].text == "z"
    assert "m0" not in [m.text for m in d.memory]


def test_retellable_fact_for_salient_events(w: World) -> None:
    evs = w.act("fighter", "attack", target="guest", intent="injure")
    fid = f"E{evs[0].id}"
    assert fid in w.s.facts and w.s.facts[fid].text == evs[0].fact_text
    assert w.c("player").beliefs[fid].confidence == 1.0
    evs = w.act("player", "look")
    assert f"E{evs[0].id}" not in w.s.facts


def test_major_events_queue_replans(w: World) -> None:
    w.c("girl").rel["guest"].affinity = 40
    w.act("fighter", "restrain", target="guest")
    assert "guest" in w.s.replan_queue and "girl" in w.s.replan_queue
    assert "player" not in w.s.replan_queue  # 玩家不由世界重新打算
    w.s.replan_queue.clear()
    w.act("player", "give", item="rope", target="fighter")
    assert w.s.replan_queue == []  # 麻绳不是要紧的东西
    w.fx([{"give": ["guest", "fighter", "token"]}])
    assert "guest" in w.s.replan_queue and "fighter" in w.s.replan_queue


def test_events_tail_cap_and_contiguous_ids(w: World) -> None:
    for _ in range(230):
        emit(w, events.kind("pass"))
    assert len(w.s.events_tail) == 200
    ids = [e.id for e in w.s.events_tail]
    assert ids == list(range(31, 231)) and w.s.next_event_id == 231
