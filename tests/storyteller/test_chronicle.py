"""年谱（方案 8.6、8.7）与分岔的显示（分岔由 world.baseline 认定，这里只读 state.divergences）。"""

from __future__ import annotations

from tianlong.kernel import actions, resolve, timeflow
from tianlong.storyteller import chronicle

SLAP = [
    {"injure": ["duan_yu", 1, "gong_guangjie"]},
    {"emit": {"actor": "gong_guangjie", "verb": "slap", "text": "⟦gong_guangjie⟧一掌掴在⟦duan_yu⟧脸上"}},
]
FAR = [
    {"injure": ["sn_guard_1", 2, "sikong_xuan"]},
    {"emit": {"actor": "sikong_xuan", "verb": "beat", "text": "⟦sikong_xuan⟧把⟦sn_guard_1⟧打伤了"}},
]


def test_columns_seen_heard_unknown(w):
    chronicle.record(w.s, w.defs, [])  # 第一次只记下快照
    near = w.fx(SLAP)
    far = resolve.run_effects(w.s, w.defs, FAR, source="agenda", offscreen=True)
    chronicle.record(w.s, w.defs, near + far)
    texts = [e.text for e in w.s.chronicle]
    assert SLAP[1]["emit"]["text"] in texts and FAR[1]["emit"]["text"] in texts
    cols = chronicle.columns(w.s)
    assert len(cols["saw"]) == 1 and len(cols["unknown"]) == 1 and not cols["heard"]
    shown = chronicle.render(w.s, w.defs)
    assert "亲见的" in shown and "当时不知道的" not in shown
    assert "场中舞剑的青年一掌掴在段公子脸上" in shown  # 玩家视角的叫法
    assert "龚光杰" not in shown and "司空玄" not in shown
    assert timeflow.clock_text(w.s.tick) in shown
    final = chronicle.render(w.s, w.defs, final=True)
    assert "当时不知道的" in final and "司空玄把神农帮帮众打伤了" in final
    assert "龚光杰一掌掴在段誉脸上" in final
    # 后来听人说起山下那件事：挪到「听说的」
    fid = f"E{far[-1].id}"
    w.fx([{"learn": ["player", fid, 0.8, "told:sn_guard_2"]}])
    cols = chronicle.columns(w.s)
    assert len(cols["heard"]) == 1 and not cols["unknown"]


def test_record_is_idempotent(w):
    chronicle.record(w.s, w.defs, [])
    evs = w.fx(SLAP)
    chronicle.record(w.s, w.defs, evs)
    n = len(w.s.chronicle)
    chronicle.record(w.s, w.defs, evs)
    assert len(w.s.chronicle) == n


def test_low_salience_not_recorded(w):
    chronicle.record(w.s, w.defs, [])
    look = actions.build(w.s, w.defs, w.s.player, "look", {})
    evs = resolve.apply(w.s, w.defs, look, source="player")
    chronicle.record(w.s, w.defs, evs)
    assert not w.s.chronicle


def _plain_defs(w):
    """去掉情境自带的年谱句（chronicle_open / chronicle_close），看通用句。"""
    d = w.defs.situations["bout_laugh"].model_copy(deep=True)
    for k in ("chronicle_open", "chronicle_close"):
        (d.__pydantic_extra__ or {}).pop(k, None)
    return w.defs.model_copy(update={"situations": {**w.defs.situations, "bout_laugh": d}})


def test_situation_open_and_close_entries(w):
    defs = _plain_defs(w)
    chronicle.record(w.s, defs, [])
    w.open_bout()
    chronicle.record(w.s, defs, [])
    sid = next(iter(w.s.situations))
    assert w.s.chronicle[-1].known_to_player and "起了一场事" in w.s.chronicle[-1].text
    w.fx([{"close": sid}])
    chronicle.record(w.s, defs, [])
    assert "了结了" in w.s.chronicle[-1].text


def test_situation_entries_use_the_situations_own_lines(w):
    """情境定义里写了 chronicle_open / chronicle_close 的，用它（角色位换成人）。"""
    d = w.defs.situations["bout_laugh"].model_copy(deep=True)
    d.__pydantic_extra__ = {**(d.__pydantic_extra__ or {}), "chronicle_open": "{victor}与{guest}起了争执",
                            "chronicle_close": "{victor}与{guest}的争执收了场"}
    defs = w.defs.model_copy(update={"situations": {**w.defs.situations, "bout_laugh": d}})
    chronicle.record(w.s, defs, [])
    w.open_bout()
    chronicle.record(w.s, defs, [])
    sid = next(iter(w.s.situations))
    assert w.s.chronicle[-1].text == "⟦gong_guangjie⟧与⟦duan_yu⟧起了争执"
    w.fx([{"close": sid}])
    chronicle.record(w.s, defs, [])
    assert w.s.chronicle[-1].text == "⟦gong_guangjie⟧与⟦duan_yu⟧的争执收了场"


def test_player_growth_entries(w):
    chronicle.record(w.s, w.defs, [])
    w.fx([{"art": ["player", "trade", 2]}, {"role": ["player", "wuliang_east", "add"]}])
    chronicle.record(w.s, w.defs, [])
    texts = [chronicle.common.pv(w.s, w.defs, e.text) for e in w.s.chronicle]
    assert any("行商的本事有了长进" in t for t in texts)
    assert any(t.startswith("你成了") for t in texts)
    assert len(chronicle.columns(w.s)["saw"]) == 2


def _diverge(w, mark: str, *, cause: int | None = None, known: bool = False, tick: int | None = None) -> None:
    """照 world.baseline.check 的写法记一条分岔（内核的 diverge 效果）。"""
    w.fx([{"diverge": {"mark": mark, "tick": w.s.tick if tick is None else tick,
                       "known_to_player": known, "cause_event": cause}}])


def test_divergences_shown_from_state_only(w):
    """分岔由 world.baseline.check 认定；年谱只读 state.divergences 来记条目、列清单。"""
    chronicle.record(w.s, w.defs, [])
    block = actions.build(w.s, w.defs, w.s.player, "block", {"target": "duan_yu"})
    cause = resolve.apply(w.s, w.defs, block, source="player")[0]
    _diverge(w, "m_beam_girl", cause=cause.id, known=True)
    _diverge(w, "m_slap")  # 追不到起因：玩家不知道
    chronicle.record(w.s, w.defs, [])
    texts = [e.text for e in w.s.chronicle]
    assert sum("钟灵在梁上出声替段誉出头" in t for t in texts) == 1
    assert sum("龚光杰当众掴了段誉一记耳光" in t for t in texts) == 1
    known = {e.text: e.known_to_player for e in w.s.chronicle}
    assert [v for t, v in known.items() if "出头" in t] == [True]
    assert [v for t, v in known.items() if "耳光" in t] == [False]
    # 重复调用不重复记
    chronicle.record(w.s, w.defs, [])
    assert [e.text for e in w.s.chronicle] == texts
    shown = chronicle.render(w.s, w.defs)
    assert "钟灵在梁上出声替段誉出头" in shown
    assert "龚光杰当众掴了段誉一记耳光" not in shown and "起因" not in shown
    final = chronicle.render(w.s, w.defs, final=True)
    assert "龚光杰当众掴了段誉一记耳光" in final and "起因：你挡在段誉面前" in final
    assert [d.mark for d in chronicle.divergences(w.s)] == ["m_beam_girl"]
    assert {d.mark for d in chronicle.divergences(w.s, final=True)} == {"m_beam_girl", "m_slap"}


def test_no_divergence_detection_in_storyteller(w):
    """讲述者不对照参照点：过了所有期限，年谱照记照显，也不会自己生出分岔。"""
    assert not hasattr(chronicle, "check_divergences")
    chronicle.record(w.s, w.defs, [])
    timeflow.advance_time(w.s, w.defs, 96 * 12)
    evs = w.fx([{"injure": ["duan_yu", 1, "gong_guangjie"]}])
    chronicle.record(w.s, w.defs, evs)
    assert w.s.divergences == []
    assert "marks_hit" not in w.s.storyteller
    assert "（你还不知道有什么不同）" in chronicle.render(w.s, w.defs)
    assert "（没有记下）" in chronicle.render(w.s, w.defs, final=True)
    assert not any("分岔" in e.text for e in w.s.chronicle)


def test_divergence_becomes_known_when_cause_is_heard_of(w):
    """认定时玩家不知道起因；后来听说了那件事（相信了 E<号>），游戏中的清单里就有它。"""
    chronicle.record(w.s, w.defs, [])
    far = resolve.run_effects(w.s, w.defs, FAR, source="agenda", offscreen=True)
    cause = far[-1]
    assert f"E{cause.id}" in w.s.facts and w.s.player not in cause.witnesses
    _diverge(w, "m_diao_bite", cause=cause.id, known=False)
    chronicle.record(w.s, w.defs, [])
    assert "闪电貂咬伤司空玄" not in chronicle.render(w.s, w.defs)
    assert len(chronicle.columns(w.s)["unknown"]) == 1
    w.fx([{"learn": ["player", f"E{cause.id}", 0.8, "told:sn_guard_2"]}])
    assert chronicle.divergence_known(w.s, w.s.divergences[0])
    assert "闪电貂咬伤司空玄" in chronicle.render(w.s, w.defs)
    assert len(chronicle.columns(w.s)["heard"]) == 1 and not chronicle.columns(w.s)["unknown"]


def test_divergence_cause_survives_event_tail(w):
    """起因事件被挤出日志尾巴之后，清单用它留下的事实 E<号> 写起因；连事实也没有就不写。"""
    evs = w.fx(SLAP, source="player")
    cause = evs[-1]
    assert f"E{cause.id}" in w.s.facts
    _diverge(w, "m_beam_girl", cause=cause.id, known=True)
    _diverge(w, "m_scroll", cause=10**6, known=False)
    w.s.events_tail.clear()
    lines = chronicle.divergence_lines(w.s, w.defs, final=True)
    assert len(lines) == 2
    assert "起因：龚光杰一掌掴在段誉脸上" in lines[0]
    assert "起因" not in lines[1]


def test_divergences_listed_in_order(w):
    _diverge(w, "m_scroll", tick=300)
    _diverge(w, "m_slap", tick=150)
    assert [d.mark for d in chronicle.divergences(w.s, final=True)] == ["m_slap", "m_scroll"]


def test_attitudes_listed_in_bands(w):
    shown = chronicle.render(w.s, w.defs)
    assert "马五德：相熟" in shown
    w.fx([{"rel": ["gong_guangjie", "player", "affinity", -60]}, {"rel": ["gong_guangjie", "player", "trust", -50]}])
    shown = chronicle.render(w.s, w.defs)
    assert "场中舞剑的青年：敌视" in shown
