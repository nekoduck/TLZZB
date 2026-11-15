"""年谱（方案 8.6、8.7）与分岔。"""

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


def test_situation_open_and_close_entries(w):
    chronicle.record(w.s, w.defs, [])
    w.open_bout()
    chronicle.record(w.s, w.defs, [])
    sid = next(iter(w.s.situations))
    assert w.s.chronicle[-1].known_to_player and "起了一场事" in w.s.chronicle[-1].text
    w.fx([{"close": sid}])
    chronicle.record(w.s, w.defs, [])
    assert "了结了" in w.s.chronicle[-1].text


def test_player_growth_entries(w):
    chronicle.record(w.s, w.defs, [])
    w.fx([{"art": ["player", "trade", 2]}, {"role": ["player", "wuliang_east", "add"]}])
    chronicle.record(w.s, w.defs, [])
    texts = [chronicle.common.pv(w.s, w.defs, e.text) for e in w.s.chronicle]
    assert any("行商的本事有了长进" in t for t in texts)
    assert any(t.startswith("你成了") for t in texts)
    assert len(chronicle.columns(w.s)["saw"]) == 2


def test_divergence_known_and_unknown(w):
    chronicle.record(w.s, w.defs, [])
    # 玩家挡在段公子面前：与「钟灵在梁上出声替段誉出头」涉及同一个人
    block = actions.build(w.s, w.defs, w.s.player, "block", {"target": "duan_yu"})
    resolve.apply(w.s, w.defs, block, source="player")
    w.fx([{"set_flag": ["siege_began", True]}])  # 围山如期发生
    timeflow.advance_time(w.s, w.defs, 64 + 96 + 8 - w.s.tick)  # 过了 m_beam_girl 的 by 再加一日
    assert w.s.tick > 64 + 96
    chronicle.check_divergences(w.s, w.defs)
    marks = {d.mark: d for d in w.s.divergences}
    assert "m_siege" not in marks and "m_siege" in w.s.storyteller["marks_hit"]
    assert marks["m_beam_girl"].known_to_player and marks["m_beam_girl"].cause_event is not None
    assert not marks["m_slap"].known_to_player  # 旗标类的参照点追不到玩家的事
    chronicle.record(w.s, w.defs, [])
    shown = chronicle.render(w.s, w.defs)
    assert "钟灵在梁上出声替段誉出头" in shown
    assert "龚光杰当众掴了段誉一记耳光" not in shown
    final = chronicle.render(w.s, w.defs, final=True)
    assert "龚光杰当众掴了段誉一记耳光" in final and "起因：你挡在段誉面前" in final
    # 同一参照点只记一次
    chronicle.check_divergences(w.s, w.defs)
    assert [d.mark for d in w.s.divergences].count("m_slap") == 1


def test_mark_hit_once_never_diverges(w):
    w.fx([{"add_status": ["duan_yu", "captive"]}])
    chronicle.check_divergences(w.s, w.defs)
    assert "m_captured_camp" in w.s.storyteller["marks_hit"]
    w.fx([{"remove_status": ["duan_yu", "captive"]}])
    timeflow.advance_time(w.s, w.defs, 200)
    chronicle.check_divergences(w.s, w.defs)
    assert "m_captured_camp" not in {d.mark for d in w.s.divergences}


def test_attitudes_listed_in_bands(w):
    shown = chronicle.render(w.s, w.defs)
    assert "马五德：相熟" in shown
    w.fx([{"rel": ["gong_guangjie", "player", "affinity", -60]}, {"rel": ["gong_guangjie", "player", "trust", -50]}])
    shown = chronicle.render(w.s, w.defs)
    assert "场中舞剑的青年：敌视" in shown
