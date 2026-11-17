"""年谱（方案 8.6、8.7）与分岔的显示（分岔由 world.baseline 认定，这里只读 state.divergences）。"""

from __future__ import annotations

from tianlong.kernel import actions, resolve, timeflow
from tianlong.storyteller import chronicle, common

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


NOISE = "⟦wl_disciple_1⟧一剑刺伤了⟦wl_disciple_2⟧"


def _loud_next_door(w) -> list:
    """隔壁（声音传得过来的地方）出了一桩大事：玩家只听见响动。"""
    here = w.p.loc
    there = next(lid for lid, loc in w.s.locations.items() if here in loc.loud_to and lid != here)
    w.fx([{"move": ["wl_disciple_1", there]}, {"move": ["wl_disciple_2", there]}], source="agenda")
    effs = [
        {"injure": ["wl_disciple_2", 2, "wl_disciple_1"]},
        {"emit": {"actor": "wl_disciple_1", "verb": "attack", "loud": True, "salience": 9, "text": NOISE}},
    ]
    evs = w.fx(effs, source="agenda")
    assert evs[-1].witnesses.get(w.s.player) == "heard"
    return evs


def test_a_noise_next_door_is_not_a_known_event(w):
    """只听见隔壁的响动：年谱里不算知道那件事（方案 6.8、8.6，D-053）；后来听人说起才挪到「听说的」。"""
    chronicle.record(w.s, w.defs, [])
    evs = _loud_next_door(w)
    chronicle.record(w.s, w.defs, evs)
    idx = next(i for i, e in enumerate(w.s.chronicle) if e.text == NOISE)
    assert w.s.chronicle[idx].known_to_player is False
    cols = chronicle.columns(w.s)
    assert idx in cols["unknown"] and idx not in cols["heard"] and idx not in cols["saw"]
    shown = chronicle.render(w.s, w.defs)
    assert "刺伤" not in shown
    w.fx([{"learn": ["player", f"E{evs[-1].id}", 0.8, "told:ma_wude"]}])
    assert idx in chronicle.columns(w.s)["heard"]
    assert "刺伤" in chronicle.render(w.s, w.defs)


def test_death_heard_as_a_noise_is_not_known(w):
    here = w.p.loc
    there = next(lid for lid, loc in w.s.locations.items() if here in loc.loud_to and lid != here)
    w.fx([{"move": ["wl_disciple_1", there]}, {"move": ["wl_disciple_2", there]}], source="agenda")
    effs = [
        {"kill": ["wl_disciple_2", "wl_disciple_1"]},
        {
            "emit": {
                "actor": "wl_disciple_1",
                "verb": "attack",
                "loud": True,
                "salience": 10,
                "text": "⟦wl_disciple_1⟧一剑刺死了⟦wl_disciple_2⟧",
                "kinds": [{"k": "death", "who": "wl_disciple_2", "by": "wl_disciple_1"}],
            }
        },
    ]
    evs = w.fx(effs, source="agenda")
    ev = next(e for e in evs if any(k.get("k") == "death" for k in e.kinds) and e.witnesses.get(w.s.player))
    assert ev.witnesses[w.s.player] == "heard"
    assert chronicle._player_knows_death(w.s, "wl_disciple_2") is False
    w.fx([{"learn": ["player", f"E{ev.id}", 0.8, "told:ma_wude"]}])
    assert chronicle._player_knows_death(w.s, "wl_disciple_2") is True


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


def _plain_defs(w, *, title: bool = False):
    """去掉情境自带的年谱句（chronicle_open / chronicle_close；title=False 时连标题也去掉），看退路。"""
    d = w.defs.situations["bout_laugh"].model_copy(deep=True)
    for k in ("chronicle_open", "chronicle_close") + (() if title else ("title",)):
        (d.__pydantic_extra__ or {}).pop(k, None)
    return w.defs.model_copy(update={"situations": {**w.defs.situations, "bout_laugh": d}})


def test_situation_open_and_close_entries(w):
    """什么都没写的情境：一句通顺的通用句（T1：不再是「甲、乙在某地起了一场事」）。"""
    defs = _plain_defs(w)
    chronicle.record(w.s, defs, [])
    w.open_bout()
    chronicle.record(w.s, defs, [])
    sid = next(iter(w.s.situations))
    assert w.s.chronicle[-1].known_to_player
    assert w.s.chronicle[-1].text == "在⟦jianhu_hall⟧，⟦gong_guangjie⟧和⟦duan_yu⟧之间出了一件事"
    w.fx([{"close": sid}])
    chronicle.record(w.s, defs, [])
    assert w.s.chronicle[-1].text == "在⟦jianhu_hall⟧，⟦gong_guangjie⟧和⟦duan_yu⟧之间的那件事有了结果"
    assert not any("起了一场事" in e.text for e in w.s.chronicle)


def test_situation_entries_fall_back_to_the_title(w):
    """没写年谱句、写了标题的情境：年谱借标题那一句。"""
    defs = _plain_defs(w, title=True)
    chronicle.record(w.s, defs, [])
    w.open_bout()
    chronicle.record(w.s, defs, [])
    sid = next(iter(w.s.situations))
    title = w.s.chronicle[-1].text
    assert title == "⟦gong_guangjie⟧在场中比剑，⟦duan_yu⟧在一旁看着"
    w.fx([{"close": sid}])
    chronicle.record(w.s, defs, [])
    assert w.s.chronicle[-1].text == title + "——这件事有了结果"


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
    # 条目里的专名写成 ⟦id⟧：显示时才按玩家所知换成叫法或真名
    assert sum("⟦zhong_ling⟧在梁上出声替⟦duan_yu⟧出头" in t for t in texts) == 1
    assert sum("⟦gong_guangjie⟧当众掴了⟦duan_yu⟧一记耳光" in t for t in texts) == 1
    known = {e.text: (e.known_to_player, e.event) for e in w.s.chronicle}
    assert [v for t, v in known.items() if "出头" in t] == [(True, cause.id)]
    assert [v for t, v in known.items() if "耳光" in t] == [(False, None)]
    # 重复调用不重复记，也不往 state.divergences 里添东西
    chronicle.record(w.s, w.defs, [])
    assert [e.text for e in w.s.chronicle] == texts
    assert [d.mark for d in w.s.divergences] == ["m_beam_girl", "m_slap"]
    # 游戏中：只列玩家知道的那条，用他眼里的叫法，不露真名
    shown = chronicle.render(w.s, w.defs)
    assert "梁上那少女在梁上出声替段公子出头" in shown
    assert "钟灵" not in shown and "段誉" not in shown and "龚光杰" not in shown
    assert "一记耳光" not in shown and "起因" not in shown
    # 收场后：全部列出，用真名，附起因
    final = chronicle.render(w.s, w.defs, final=True)
    assert "钟灵在梁上出声替段誉出头" in final
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
    assert "咬伤" not in chronicle.render(w.s, w.defs)
    assert len(chronicle.columns(w.s)["unknown"]) == 1
    w.fx([{"learn": ["player", f"E{cause.id}", 0.8, "told:sn_guard_2"]}])
    assert chronicle.divergence_known(w.s, w.s.divergences[0])
    shown = chronicle.render(w.s, w.defs)
    assert "一只灰白色的小兽咬伤" in shown and "闪电貂" not in shown  # 那只小兽的名字他还不知道
    assert "闪电貂咬伤司空玄" in chronicle.render(w.s, w.defs, final=True)
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


def test_mark_text_is_linked_before_display(w):
    """参照点那句话照原著写着真名：先换成 ⟦id⟧，游戏中才不会把玩家不知道的名字露出来。"""
    assert common.linked(w.defs, "龚光杰当众掴了段誉一记耳光") == "⟦gong_guangjie⟧当众掴了⟦duan_yu⟧一记耳光"
    assert common.linked(w.defs, "⟦duan_yu⟧见到了钟灵") == "⟦duan_yu⟧见到了⟦zhong_ling⟧"  # 已经是 ⟦id⟧ 的不动
    assert common.linked(w.defs, "") == "" and chronicle.mark_text(w.defs, "no_such_mark") == "no_such_mark"
    unknown = common.unknown_names(w.s, w.defs)
    for m in w.defs.baseline:
        shown = common.pv(w.s, w.defs, chronicle.mark_text(w.defs, m.id))
        assert "⟦" not in shown and not any(n in shown for n in unknown), shown
        assert common.omni(w.s, w.defs, chronicle.mark_text(w.defs, m.id)) == m.text


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
