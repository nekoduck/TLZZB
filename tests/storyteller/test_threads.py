"""牵挂（方案 8.4）的生成与了结。"""

from __future__ import annotations

from tianlong.kernel import actions, resolve
from tianlong.kernel.state import Speech
from tianlong.storyteller import common, threads
from tianlong.storyteller.threads import add_wish, remove_wish, render_threads


def test_opening_thread_kept(w):
    threads.update(w.s, w.defs, [])
    assert not w.s.threads["T_escort_home"].closed
    assert "随东家做完客" in render_threads(w.s, w.defs)


def test_poison_deadline_generated_and_closed_when_cured(w):
    w.fx([{"add_status": ["player", "poisoned_duanchang", {"ticks": 672, "source": "sikong_xuan"}]}])
    threads.update(w.s, w.defs, [])
    t = w.s.threads["dl:player:poisoned_duanchang"]
    assert t.kind == "deadline" and not t.closed
    assert t.weight == 3 and t.expires == w.s.tick + 672
    assert "sikong_xuan" in t.refs
    shown = render_threads(w.s, w.defs)
    assert "就要发作" in shown and "还剩七日" in shown
    assert "司空玄" not in shown  # 玩家还不知道他的名字
    w.fx([{"remove_status": ["player", "poisoned_duanchang"]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["dl:player:poisoned_duanchang"].closed


def test_deadline_weight_rises_in_last_two_days(w):
    w.fx([{"add_status": ["player", "poisoned_diao", {"ticks": 100}]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["dl:player:poisoned_diao"].weight == 5


def test_deadline_thread_lists_only_known_cures(w):
    w.fx([{"add_status": ["player", "poisoned_duanchang", {"ticks": 672}]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["dl:player:poisoned_duanchang"].refs == ["player"]
    # 听说「解药在某人身上」：那人成了这条牵挂的相关者
    w.fx([{"learn": ["player", "F_duanchang_antidote_sikong", 1.0, "told:sn_chief_a"]}])
    threads.update(w.s, w.defs, [])
    assert "sikong_xuan" in w.s.threads["dl:player:poisoned_duanchang"].refs
    assert "duanchang_san" not in w.s.threads["dl:player:poisoned_duanchang"].refs
    # 知道了解药的名字
    w.fx([{"know_term": "duanchang_antidote"}])
    threads.update(w.s, w.defs, [])
    assert "duanchang_antidote" in w.s.threads["dl:player:poisoned_duanchang"].refs


def test_others_deadline_only_when_player_knows(w):
    # 段公子在眼前中了毒：玩家亲眼见到
    w.fx([{"add_status": ["duan_yu", "poisoned_duanchang", {"ticks": 672}]}])
    # 司空玄在山下中了貂毒：玩家不在场
    w.fx([{"add_status": ["sikong_xuan", "poisoned_diao", {"ticks": 672}]}])
    threads.update(w.s, w.defs, [])
    assert "dl:duan_yu:poisoned_duanchang" in w.s.threads
    assert "dl:sikong_xuan:poisoned_diao" not in w.s.threads
    w.fx([{"kill": "duan_yu"}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["dl:duan_yu:poisoned_duanchang"].closed


def test_event_thread_follows_the_situation(w):
    w.open_bout()
    threads.update(w.s, w.defs, [])
    sid = next(iter(w.s.situations))
    t = w.s.threads[f"ev:{sid}"]
    assert t.kind == "event" and t.weight == 4 and set(t.refs) == {"gong_guangjie", "duan_yu"}
    assert "段公子" in common.pv(w.s, w.defs, t.text)
    w.fx([{"close": sid}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads[f"ev:{sid}"].closed


def test_promise_thread_until_kept(w):
    wait = actions.build(w.s, w.defs, w.s.player, "wait", {"ticks": 1})
    sp = Speech(act="promise", to=["ma_wude"], promise_do={"verb": "follow", "target": "ma_wude"})
    evs = resolve.apply(w.s, w.defs, wait, sp, source="player")
    assert evs and w.s.promises
    pid = next(iter(w.s.promises))
    threads.update(w.s, w.defs, evs)
    t = w.s.threads[f"pr:{pid}"]
    assert t.kind == "promise" and t.weight == 4 and t.refs == ["ma_wude"]
    # 跟着东家走就算守诺；这一步也算推进这条牵挂
    follow = actions.build(w.s, w.defs, w.s.player, "follow", {"target": "ma_wude"})
    assert threads.Matcher(w.s, w.defs).advances(follow, t)
    resolve.apply(w.s, w.defs, follow, source="player")
    assert w.s.promises[pid].status == "kept"
    threads.update(w.s, w.defs, [])
    assert w.s.threads[f"pr:{pid}"].closed


def test_lead_from_new_fact_and_closed_on_arrival(w):
    threads.update(w.s, w.defs, [])
    w.fx([{"learn": ["player", "F_route_wanjie", 1.0, "told:jin_xier"]}])
    threads.update(w.s, w.defs, [])
    leads = [t for t in threads.open_threads(w.s) if t.kind == "lead"]
    assert {t.refs[0] for t in leads} == {"ferry_bridge", "pine_wood"}
    assert all(t.weight == 2 for t in leads)
    # 往那边去的第一步就算推进这条线索
    m = threads.Matcher(w.s, w.defs)
    go = actions.build(w.s, w.defs, w.s.player, "go", {"exit": "jianhu_back"})
    assert any(m.advances(go, t) for t in leads)
    w.fx([{"move": ["player", "ferry_bridge"]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["lead:F_route_wanjie:ferry_bridge"].closed
    assert not w.s.threads["lead:F_route_wanjie:pine_wood"].closed
    # 同一件事不会再生线索
    threads.update(w.s, w.defs, [])
    assert len([t for t in w.s.threads.values() if t.kind == "lead"]) == 2


def test_lead_cap(w):
    facts = ["F_route_wanjie", "F_gate_knock", "F_diao_antidote_cabinet", "F_elope_plan", "F_duanchang_antidote_sikong",
             "F_shennong_orders", "F_diao_antidote_father", "F_duan_family"]
    threads.update(w.s, w.defs, [])  # 开局知道的事不算新得知
    w.fx([{"learn": ["player", f, 1.0, "inferred"]} for f in facts])
    threads.update(w.s, w.defs, [])
    leads = [t for t in threads.open_threads(w.s) if t.kind == "lead"]
    assert 0 < len(leads) <= threads.LEAD_CAP


def test_opening_knowledge_is_not_a_lead(w):
    w.fx([{"move": ["player", "jianhu_gate"]}])
    threads.update(w.s, w.defs, [])
    assert not [t for t in w.s.threads.values() if t.kind == "lead"]


def test_fact_tied_to_a_deadline_is_not_a_lead(w):
    threads.update(w.s, w.defs, [])
    w.fx([{"move": ["player", "jianhu_gate"]}])
    w.fx([{"add_status": ["player", "poisoned_duanchang", {"ticks": 672}]}])
    threads.update(w.s, w.defs, [])
    w.fx([{"learn": ["player", "F_duanchang_antidote_sikong", 1.0, "told:sn_chief_a"]}])
    threads.update(w.s, w.defs, [])
    assert not [t for t in w.s.threads.values() if t.kind == "lead"]
    assert "sikong_xuan" in w.s.threads["dl:player:poisoned_duanchang"].refs


def test_wish_added_achieved_and_revoked(w):
    add_wish(w.s, w.defs, {"text": "弄一把趁手的家伙", "refs": ["chopper"]})
    t = w.s.threads["wish:1"]
    assert t.kind == "wish" and t.weight == 3 and not t.closed
    w.fx([{"spawn": ["chopper", "player"]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["wish:1"].closed
    add_wish(w.s, w.defs, {"text": "见一见那位掌门", "refs": ["zuo_zimu"],
                           "done_when": {"rel": ["zuo_zimu", "player", "affinity", ">=", 30]}})
    threads.update(w.s, w.defs, [])
    assert not w.s.threads["wish:2"].closed
    w.fx([{"rel": ["zuo_zimu", "player", "affinity", 40]}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["wish:2"].closed
    add_wish(w.s, w.defs, {"text": "学会识字", "refs": []})
    remove_wish(w.s, w.defs, "wish:3")
    assert w.s.threads["wish:3"].closed


def test_opening_promise_closes_when_master_dies(w):
    w.fx([{"kill": "ma_wude"}])
    threads.update(w.s, w.defs, [])
    assert w.s.threads["T_escort_home"].closed
    assert "近来了结的" in render_threads(w.s, w.defs)


def test_update_is_idempotent_and_rollback_safe(w):
    w.open_bout()
    threads.update(w.s, w.defs, [])
    before = {k: v.model_dump() for k, v in w.s.threads.items()}
    threads.update(w.s, w.defs, [])
    assert {k: v.model_dump() for k, v in w.s.threads.items()} == before
    assert not w.s.violations
