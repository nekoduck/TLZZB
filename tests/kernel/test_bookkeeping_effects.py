"""D-078：上层模块用的记账类效果。"""

from __future__ import annotations

from tianlong.kernel import resolve


def test_reflect_and_impression(w):
    before = w.c("guest").rel["player"].trust if "player" in w.c("guest").rel else 0
    w.fx([{"reflect": ["guest", "那伙计倒还老实。", ["player"], 7]}, {"impression": ["guest", "player", "老实"]}])
    m = w.c("guest").memory[-1]
    assert m.kind == "reflection" and m.entities == ["player"] and m.salience == 7
    assert w.c("guest").rel["player"].impression == "老实"
    assert w.c("guest").rel["player"].trust == before


def test_agenda_replace_and_progress(w):
    ag = {"goal": "回家", "steps": [{"id": "x1", "text": "回去", "do": {"verb": "wait"}, "origin": "replan"}]}
    w.fx([{"agenda": ["guest", ag]}, {"progress": ["guest", 3]}, {"progress": ["guest", 2]}])
    a = w.c("guest").agenda
    assert a.goal == "回家" and a.steps[0].origin == "replan" and a.progress == 5
    w.fx([{"progress": ["guest", 0]}])
    assert w.c("guest").agenda.progress == 0
    w.fx([{"agenda": ["guest", None]}])
    assert w.c("guest").agenda is None


def test_chronicle_diverge_teller(w):
    w.fx(
        [
            {"chronicle": {"text": "比剑散了", "known_to_player": True}},
            {"diverge": {"mark": "m_x", "cause_event": None}},
            {"diverge": {"mark": "m_x"}},  # "diverge" 同一参照点只记一次
            {"teller": ["tension", 30]},
        ]
    )
    assert w.s.chronicle[-1].text == "比剑散了" and w.s.chronicle[-1].tick == w.s.tick
    assert [d.mark for d in w.s.divergences].count("m_x") == 1
    assert w.s.storyteller["tension"] == 30


def test_thread_close_rolls_back(w):
    w.fx([{"thread": {"id": "T9", "kind": "lead", "text": "一条线索"}}])
    snap = resolve.Snapshot(w.s)
    w.fx([{"close_thread": "T9"}])
    assert w.s.threads["T9"].closed
    snap.restore(w.s)
    assert not w.s.threads["T9"].closed
