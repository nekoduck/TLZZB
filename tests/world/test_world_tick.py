"""8.1 的五步：一个时辰推进离场者的打算；在场的人不由 world 推进；离场事件标 offscreen。"""

from __future__ import annotations

from tianlong.kernel import timeflow
from tianlong.world import clock

from .conftest import fx, hours, player_off


async def test_one_hour_advances_offscreen_agendas(llm, world):
    defs, state = world
    assert state.tick == 32 and state.chars[state.player].loc == "jianhu_hall"
    sk = state.chars["sikong_xuan"]
    chief = state.chars["sn_chief_a"]
    dy = state.chars["duan_yu"]
    assert sk.agenda.progress == 0 and chief.loc == "shennong_camp"
    timeflow.advance_time(state, defs, 8)
    events = await clock.tick(llm, state, defs)
    # 司空玄在营里「围山」：离场，等的一步记了一个时辰
    assert sk.agenda.steps[sk.agenda.cursor].id == "sk_besiege"
    assert sk.agenda.progress == 8
    # 黑须头目 start 34 已到：动身往山门，走完第一段到了下山石径，第二段记了余下的刻数
    assert chief.loc == "mountain_path"
    assert chief.agenda.steps[chief.agenda.cursor].id == "sa_ultimatum"
    assert chief.agenda.progress == 2
    # 段誉与玩家同在练武厅：在场，world 不替他推进
    assert dy.agenda.progress == 0
    # 离场结算出来的事件都标了 offscreen
    agenda_events = [e for e in events if e.source == "agenda"]
    assert agenda_events and all(e.offscreen for e in agenda_events)
    # 玩家就在厅上：比剑的情境开场，在微轮里演下去（不直接离场结算）
    assert any(s.kind == "bout_laugh" for s in state.situations.values())
    assert not state.flags.get("slapped")


async def test_offscreen_bout_settles_without_player(llm, world):
    defs, state = world
    player_off(state, defs)
    await hours(llm, state, defs, 1)
    assert state.flags.get("slapped") is True
    assert not state.situations


async def test_tick_does_nothing_after_the_end(llm, world):
    defs, state = world
    fx(state, defs, [{"end": "closed"}])
    timeflow.advance_time(state, defs, 8)
    assert await clock.tick(llm, state, defs) == []


async def test_deadline_voids_even_in_scene(llm, world):
    defs, state = world
    dy = state.chars["duan_yu"]
    fx(state, defs, [{"step": ["duan_yu", {"id": "t_late", "text": "测试", "do": {"verb": "wait"}, "deadline": 33, "takes": 1}]}])
    assert dy.agenda.steps[dy.agenda.cursor].id == "t_late"
    timeflow.advance_time(state, defs, 8)
    await clock.tick(llm, state, defs)
    st = next(s for s in dy.agenda.steps if s.id == "t_late") if any(s.id == "t_late" for s in dy.agenda.steps) else None
    # 过期作废；他重新打算过（或者照旧原来的打算），总之那一步不再是 pending
    assert st is None or st.state == "void"


async def test_done_when_finishes_the_agenda(llm, world):
    defs, state = world
    ag = state.chars["mu_wanqing"].agenda.model_dump()
    ag["done_when"] = {"flag": "mw_safe"}
    fx(state, defs, [{"agenda": ["mu_wanqing", ag]}, {"set_flag": ["mw_safe", True]}])
    timeflow.advance_time(state, defs, 8)
    await clock.tick(llm, state, defs)
    new = state.chars["mu_wanqing"].agenda
    assert new.steps and all(s.origin == "replan" for s in new.steps)


async def test_waiting_people_replan_at_dawn(llm, world):
    from tianlong.world import goals

    defs, state = world
    fx(state, defs, [{"agenda": ["rong_ziju", goals.waiting_agenda("守门", "等掌门回来")]}])
    timeflow.advance_time(state, defs, clock.DAWN + 96 - state.tick - 8)
    await clock.tick(llm, state, defs)
    assert state.chars["rong_ziju"].agenda.status == "waiting"  # 还没到卯时
    timeflow.advance_time(state, defs, 8)
    assert state.tick % 96 == clock.DAWN
    await clock.tick(llm, state, defs)
    assert state.chars["rong_ziju"].agenda.status == "active"
