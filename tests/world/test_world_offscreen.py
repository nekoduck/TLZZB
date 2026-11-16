"""8.2 离场结算：赶路、动手、通用结算、say 的答复、图外来人按 start 入场。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tianlong.kernel import actions, timeflow
from tianlong.kernel.state import Speech
from tianlong.world import offscreen

from .conftest import fx, hours, player_off


async def test_offmap_arrivals_happen_at_their_start(llm, world):
    defs, state = world
    player_off(state, defs)
    seen: dict[str, int] = {}
    while state.tick < 232:
        await hours(llm, state, defs, 1)
        for cid in ("rui_popo", "ping_popo", "envoy_lingjiu"):
            if state.chars[cid].onstage and cid not in seen:
                seen[cid] = state.tick
    assert seen["rui_popo"] == 168
    assert seen["ping_popo"] == 176  # 比瑞婆婆晚一个时辰入场：她先到，这一位才找得到人跟
    assert seen["envoy_lingjiu"] == 224
    for cid in ("nanhai_eshen", "dali_guard_chief"):
        assert not state.chars[cid].onstage  # 还没到他们的 start


async def test_travel_and_generic_wait_steps(llm, world):
    defs, state = world
    player_off(state, defs)
    await hours(llm, state, defs, 3)  # 到 56
    chief = state.chars["sn_chief_a"]
    st = next(s for s in chief.agenda.steps if s.id == "sa_ultimatum")
    assert st.state == "done"
    assert state.flags.get("siege_began") is True  # 他在山门下，围山的情境离场结算了


async def test_generic_take_resolves_through_the_kernel(llm, world):
    defs, state = world
    player_off(state, defs)
    fx(state, defs, [{"spawn": ["chopper", "mountain_path"]}])
    fx(state, defs, [{"step": ["sn_chief_b", {"id": "t_take", "text": "捡起柴刀", "at": "mountain_path", "do": {"verb": "take", "item": "chopper"}, "takes": 1}]}])
    events = await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert state.items["chopper"].holder == "sn_chief_b"
    st = next(s for s in state.chars["sn_chief_b"].agenda.steps if s.id == "t_take")
    assert st.state == "done"
    assert events and all(e.offscreen for e in events)
    assert any(e.step == "t_take" for e in events)


async def test_step_with_failing_pre_is_void_and_never_executes(llm, world):
    defs, state = world
    player_off(state, defs)
    fx(state, defs, [{"spawn": ["chopper", "mountain_path"]}])
    fx(state, defs, [{"step": ["sn_chief_b", {"id": "t_take", "text": "捡起柴刀", "at": "mountain_path", "do": {"verb": "take", "item": "chopper"}, "pre": {"flag": "never"}, "takes": 1}]}])
    events = await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert state.items["chopper"].holder is None
    assert not any(e.step == "t_take" for e in events)
    st = next(s for s in state.chars["sn_chief_b"].agenda.steps if s.id == "t_take")
    assert st.state == "void"
    assert "sn_chief_b" in state.replan_queue


async def test_unreachable_place_is_void(llm, world):
    defs, state = world
    player_off(state, defs)
    # 谷底石洞的入口是隐藏的：不知道这条路的人去不了
    fx(state, defs, [{"step": ["sn_chief_b", {"id": "t_cave", "text": "去石洞", "at": "valley_cave", "do": {"verb": "wait"}, "takes": 1}]}])
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    st = next(s for s in state.chars["sn_chief_b"].agenda.steps if s.id == "t_cave")
    assert st.state == "void"


async def test_say_request_gets_a_rule_answer(llm, world):
    defs, state = world
    player_off(state, defs)
    step = {"id": "t_ask", "text": "请帮众把守路卡", "at": "mountain_path", "do": {"verb": "say", "act": "request", "to": "sn_guard_3", "ask": {"verb": "wait", "params": {}}}, "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    events = await offscreen.advance(state, defs, "sn_chief_b", 8)
    reqs = [r for r in state.requests.values() if r.frm == "sn_chief_b" and r.to == "sn_guard_3"]
    assert len(reqs) == 1
    assert reqs[0].status in ("agreed", "refused", "countered")
    says = [e for e in events if e.verb == "say"]
    assert {e.actor for e in says} == {"sn_chief_b", "sn_guard_3"}


async def test_say_request_asks_decide_for_the_answer(llm, world):
    defs, state = world
    player_off(state, defs)
    asked: list[str] = []

    async def decide(st: Any, df: Any, actor: str) -> Any:
        asked.append(actor)
        rid = next(r.id for r in st.requests.values() if r.to == actor)
        return SimpleNamespace(
            action=actions.build(st, df, actor, "wait", {"ticks": 1}),
            speech=Speech(act="refuse", to=["sn_chief_b"], request=rid),
        )

    step = {"id": "t_ask", "text": "请帮众把守路卡", "at": "mountain_path", "do": {"verb": "say", "act": "request", "to": "sn_guard_3"}, "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    await offscreen.advance(state, defs, "sn_chief_b", 8, decide=decide)
    assert asked == ["sn_guard_3"]
    r = next(r for r in state.requests.values() if r.frm == "sn_chief_b")
    assert r.status == "refused"


async def test_decide_failure_falls_back_to_rule_answer(llm, world):
    defs, state = world
    player_off(state, defs)

    async def decide(*a: Any) -> Any:
        raise RuntimeError("model down")

    step = {"id": "t_ask", "text": "请帮众把守路卡", "at": "mountain_path", "do": {"verb": "say", "act": "request", "to": "sn_guard_3"}, "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    await offscreen.advance(state, defs, "sn_chief_b", 8, decide=decide)
    r = next(r for r in state.requests.values() if r.frm == "sn_chief_b")
    assert r.status != "open"


async def test_follow_step_moves_with_the_leader(llm, world):
    defs, state = world
    player_off(state, defs)
    fx(state, defs, [{"step": ["sn_guard_3", {"id": "t_follow", "text": "跟着头目", "do": {"verb": "follow", "target": "sn_chief_b"}, "takes": 40}]}])
    await offscreen.advance(state, defs, "sn_guard_3", 8)
    assert state.chars["sn_guard_3"].following == "sn_chief_b"
    fx(state, defs, [{"move": ["sn_chief_b", "shennong_camp"]}])
    assert state.chars["sn_guard_3"].loc == "shennong_camp"


async def test_in_scene_people_are_not_advanced(llm, world):
    defs, state = world
    dy = state.chars["duan_yu"]  # 与玩家同在练武厅
    events = await offscreen.advance(state, defs, "duan_yu", 8)
    assert events == [] and dy.agenda.progress == 0


async def test_offscreen_effects_of_a_step(llm, world):
    """一步写了 offscreen：动手时执行它（这里是图外的人入场）。"""
    defs, state = world
    player_off(state, defs)
    timeflow.advance_time(state, defs, 168 - state.tick)
    assert not state.chars["rui_popo"].onstage
    await offscreen.advance(state, defs, "rui_popo", 8)
    rp = state.chars["rui_popo"]
    assert rp.onstage and rp.loc == "highroad"
    assert next(s for s in rp.agenda.steps if s.id == "rp_enter").state == "done"


async def test_say_to_someone_who_is_not_there_voids_the_step_quietly(llm, world):
    """T2：指名说给某人的话，他不在跟前——这一步作废，不让内核记一条「speech dropped: … not here」。"""
    defs, state = world
    player_off(state, defs)
    step = {"id": "t_taunt", "text": "讥讽几句", "at": "mountain_path", "do": {"verb": "say", "act": "taunt", "to": "zuo_zimu"}, "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    assert state.chars["zuo_zimu"].loc != "mountain_path"
    n = len(state.violations)
    events = await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert not [e for e in events if e.verb == "say"]
    st = next(s for s in state.chars["sn_chief_b"].agenda.steps if s.id == "t_taunt")
    assert st.state == "void"
    assert not [v for v in state.violations[n:] if "not here" in v], state.violations[n:]


async def test_reply_that_agrees_and_acts_is_not_rolled_back(llm, world):
    """对方的抉择若是「答应 + 当场照做」：先话后事分两次结算，不因同一次结算里新插的那一步而整体回滚。"""
    defs, state = world
    player_off(state, defs)

    async def decide(st: Any, df: Any, actor: str) -> Any:
        r = next(r for r in st.requests.values() if r.to == actor and r.status == "open")
        r.verdict = "may"
        do = actions.build(st, df, actor, "follow", {"target": r.frm})
        assert do is not None
        return SimpleNamespace(action=do, speech=Speech(act="agree", to=[r.frm], request=r.id))

    step = {"id": "t_ask", "text": "叫帮众跟上", "at": "mountain_path",
            "do": {"verb": "say", "act": "request", "to": "sn_guard_3", "ask": {"verb": "follow", "params": {"target": "self"}}},
            "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    n = len(state.violations)
    events = await offscreen.advance(state, defs, "sn_chief_b", 8, decide=decide)
    r = next(r for r in state.requests.values() if r.frm == "sn_chief_b" and r.to == "sn_guard_3")
    assert r.status == "agreed"
    assert not [v for v in state.violations[n:] if "rollback" in v], state.violations[n:]
    assert state.chars["sn_guard_3"].following == "sn_chief_b"
    assert all(e.offscreen for e in events if e.actor == "sn_guard_3")
    # 答应「跟着走」是持续的一步：当场跟上了，这一步还在（跟满了才算完；跟的人走了、自己被拿住就作废）
    ag = state.chars["sn_guard_3"].agenda
    cur = ag.steps[ag.cursor]
    assert cur.id == f"req_{r.id}" and cur.state == "pending" and ag.progress >= 1
    assert actions.following_step(state, state.chars["sn_guard_3"], cur)
