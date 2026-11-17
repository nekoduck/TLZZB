"""时间成段过去的动作、打断、动弹不得、夜间反思、预算、调试命令（方案 6.9、7.10、8.7、10.3、10.6）。"""

from __future__ import annotations

import pytest

from tianlong import minds, narrator
from tianlong.config import Settings
from tianlong.kernel import growth
from tianlong.kernel.state import has_status

from .conftest import assert_full_turn, ended_of, header_of, kinds, new_session, notices, options_of, text_of

QUIET_YARD = ("一", "二", "我去前院看看")  # 把开场那场事演完，再走到没人的前院


async def test_wait_passes_world_hours(settings: Settings) -> None:
    """6.9、8.1：等一个时辰，时间成段过去，每跨一个时辰做一次 world.tick。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    ticks: list[int] = []
    orig = s.game.__class__._decide

    import tianlong.world as world

    real_tick = world.tick

    async def spy(llm, state, defs, **kw):  # noqa: ANN001, ANN003, ANN202
        ticks.append(state.tick)
        return await real_tick(llm, state, defs, **kw)

    world.tick = spy  # type: ignore[assignment]
    try:
        t0 = st.tick
        evs = await s.send("我在这里等上两个时辰")
    finally:
        world.tick = real_tick  # type: ignore[assignment]
    assert orig is not None
    assert_full_turn(evs)
    assert st.tick == t0 + 16
    assert ticks == [t for t in range(t0 + 1, t0 + 17) if t % 8 == 0]  # 只在时辰边界上
    h = header_of(evs)
    assert h is not None and h.clock == "第一日 午时"
    await s.close()


async def test_long_wait_is_interrupted_when_someone_arrives(settings: Settings) -> None:
    """6.9、8.7：等待期间有动静传到玩家耳目，就在那一刻打断。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    t0 = st.tick
    # 十刻之后有人走进院子
    s.fx([{"schedule": [10, [{"move": ["guest", "yard"]}]]}])
    evs = await s.send("我在这里等上一天")
    assert_full_turn(evs)
    assert s.game.last_turn is not None and s.game.last_turn.interrupted
    assert st.tick == t0 + 10  # 就在看见来人的那一刻停下，没有等满一天
    assert "白公子来到" in text_of(evs)
    # 没有人来，就等满
    t1 = st.tick
    await s.send("我在这里等上两个时辰")
    assert st.tick == t1 + 16 and not s.game.last_turn.interrupted
    await s.close()


async def test_sleeper_wakes_only_for_loud_or_direct_things(settings: Settings) -> None:
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    t0 = st.tick
    # 睡着的人看不见有人悄悄走进来
    s.fx([{"schedule": [3, [{"move": ["guest", "yard"]}]]}])
    evs = await s.send("我歇息两个时辰")
    assert st.tick == t0 + 16 and not s.game.last_turn.interrupted
    assert not has_status(st.chars[pid], "asleep")
    assert "白公子来到" not in text_of(evs)
    # 近旁一声巨响：惊醒
    t1 = st.tick
    shout = {"emit": {"actor": "guest", "verb": "shout", "text": "⟦guest⟧大喊了一声", "loud": True}}
    s.fx([{"schedule": [10, [{"move": ["guest", "yard"]}, shout]]}])
    evs = await s.send("我歇息四个时辰")
    assert s.game.last_turn.interrupted and st.tick == t1 + 10  # 就在那一声响起的时候
    assert not has_status(st.chars[pid], "asleep")
    assert "惊醒" in text_of(evs)
    assert_full_turn(evs)
    await s.close()


async def test_helpless_player_lets_time_pass(settings: Settings) -> None:
    """玩家昏着：什么也做不了，输入只让时间过去；能动了就恢复正常的回合。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    t0 = st.tick
    s.fx([{"add_status": [pid, "unconscious", {"ticks": 10}]}])
    evs = await s.send("一")
    assert s.game.last_turn is not None and s.game.last_turn.kind == "helpless"
    assert st.tick == t0 + 8 and options_of(evs) is None
    assert "动弹不得" in notices(evs)[-1] and "昏昏沉沉" in text_of(evs)
    evs = await s.send("随便什么")
    assert st.tick == t0 + 10 and not has_status(st.chars[pid], "unconscious")  # 一能动就停，不必等满一个时辰
    assert_full_turn(evs)
    evs = await s.send("一")
    assert s.game.last_turn.kind == "turn"
    await s.close()


async def test_travel_is_summarised_in_one_line(settings: Settings) -> None:
    """8.7：赶路是一次 go，用一段叙述带过。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    t0 = st.tick
    evs = await s.send("我去崖边看看")
    assert st.chars[st.player].loc == "cliff" and st.tick == t0 + 4
    body = text_of(evs)
    assert "你一路走了小半个时辰，来到一处崖边" in body and "往一处崖边去了" not in body
    await s.close()


async def test_nightly_reflection_triggers(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """7.10：玩家歇息满 16 刻，或跨日时，做一次夜间反思。"""
    calls: list[int] = []
    real = minds.reflect.nightly

    async def spy(llm, state, defs):  # noqa: ANN001, ANN202
        calls.append(state.tick)
        return await real(llm, state, defs)

    monkeypatch.setattr(minds.reflect, "nightly", spy)
    s = await new_session(settings, debug=True)
    await s.play(*QUIET_YARD)
    st = s.state
    await s.send("我在这里等上两个时辰")
    await s.send("我歇息一个时辰")
    assert calls == []  # 等待不算；歇息不满 16 刻也不算
    await s.send("我歇息两个时辰")
    assert calls == [st.tick]
    await s.send("/debug tick 96")  # 跨日
    assert len(calls) == 2 and calls[-1] == st.tick
    assert s.game.last_turn is not None and "reflect" in s.game.last_turn.notes
    await s.close()


async def test_turn_failure_marks_options_stale(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-199：一回合中途出错，界面不倒；下一次选选项时先把选项重排一遍。"""
    s = await new_session(settings)
    real = narrator.narrate

    def boom(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(narrator, "narrate", boom)
    with pytest.raises(RuntimeError):
        await s.send("一")
    monkeypatch.setattr(narrator, "narrate", real)
    assert s.game.last_turn is not None and "aborted" in s.game.last_turn.notes
    evs = await s.send("一")
    assert kinds(evs) == ["Notice", "Options"] and evs[0].level == "warn"  # type: ignore[union-attr]
    assert options_of(evs) == s.game.option_texts and len(s.game.options) == 3
    evs = await s.send("一")
    assert_full_turn(evs)
    await s.close()


# ---------------------------------------------------------------- 预算（10.3）


async def test_budget_warns_then_pauses_and_asks(settings: Settings) -> None:
    settings.route.classify = "jev_first"  # 本测试看的是 J1 的调用
    s = await new_session(settings)
    g = s.game
    led = g.llm.ledger
    assert led.budget_usd == 5.0
    led.add("test", 4.2)  # 过了八成
    evs = await s.send("一")
    warn = [e for e in evs if type(e).__name__ == "Notice"]
    assert len(warn) == 1 and warn[0].level == "warn" and "八成" in warn[0].text  # type: ignore[union-attr]
    evs = await s.send("二")
    assert not notices(evs)  # 只提醒一次
    led.add("test", 1.0)  # 到顶
    evs = await s.send("一")
    assert "预算上限" in notices(evs)[-1] and "/继续" in notices(evs)[-1]
    turn = s.state.turn
    # /继续：追加预算，接着用模型
    evs = await s.send("/继续")
    assert "追加" in notices(evs)[0] and led.budget_usd == 10.0 and s.state.turn == turn
    assert g.llm.mode("classify") == "jev_first"
    assert "没有什么要继续" in notices(await s.send("/继续"))[0]
    # 再到顶：输入别的，就改用规则与模板，这一句照常当作输入
    led.add("test", 5.0)
    evs = await s.send("一")
    assert "预算上限" in notices(evs)[-1]
    evs = await s.send("二")
    assert "规则与模板" in notices(evs)[0]
    assert_full_turn(evs[1:])
    assert s.state.turn == turn + 2
    for cat in ("classify", "decide_soul", "decide_named", "replan", "options", "check", "cards", "narrate", "phrase"):
        assert g.llm.mode(cat) == "rules_only", cat
    n = g.llm.recorder.count
    evs = await s.send("我四下看看")
    assert_full_turn(evs)
    assert g.llm.recorder.count == n  # 此后一次模型调用也没有
    assert not notices(evs)  # 不再询问
    await s.close()


# ---------------------------------------------------------------- /debug（10.6）


async def test_debug_summary_and_cheats(settings: Settings) -> None:
    settings.route.classify = "jev_first"  # 本测试看的是 J1 的调用
    s = await new_session(settings, debug=True)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    out = notices(await s.send("/debug"))[0]
    assert out.startswith("【调试】") and "种子 1" in out and "模型调用" in out and "J1" in out
    turn, tick = st.turn, st.tick
    # 给东西：经效果，物件确实换了手
    evs = await s.send("/debug give sword")
    assert "已生效" in notices(evs)[0] and options_of(evs) is not None
    assert "sword" in me.inventory and st.items["sword"].holder == pid and "sword" not in st.chars["fighter"].inventory
    await s.send("/debug give token girl")
    assert st.items["token"].holder == "girl"
    # 本事与内力（方案 11.4「玩家变强」）
    p0 = growth.power(st, s.game.defs, me)
    await s.send("/debug art cloud_steps 2")
    await s.send("/debug stat neili 60")
    assert me.arts["cloud_steps"] == 2 and me.neili == 60
    assert growth.power(st, s.game.defs, me) > p0
    await s.send("/debug stat neili 20")
    assert me.neili == 20
    # 身份
    await s.send("/debug role hall_sect add")
    assert "hall_sect" in me.roles
    await s.send("/debug role hall_sect remove")
    assert "hall_sect" not in me.roles
    # 挪地方
    evs = await s.send("/debug goto hall")
    assert me.loc == "hall" and st.tick == tick and st.turn == turn
    # 作弊都记进日志
    cheats = [v for v in st.violations if "debug cheat" in v]
    assert len(cheats) == 8 and "/debug give sword" in cheats[0]
    # 写错了给用法；不存在的东西不生效，也不出错
    assert "用法" in notices(await s.send("/debug stat 魅力 3"))[0]
    assert "用法" in notices(await s.send("/debug kill"))[0]
    evs = await s.send("/debug give 不存在的东西")
    assert "没有生效" in notices(evs)[0]
    # 要人身死（方案 11.4）：世界继续，死者的打算全部作废
    evs = await s.send("/debug kill fighter")
    assert has_status(st.chars["fighter"], "dead") and st.ended is None
    assert "死了" in notices(evs)[0]
    ag = st.chars["fighter"].agenda
    assert ag is None or all(step.state != "pending" for step in ag.steps)
    evs = await s.send("一")
    assert_full_turn(evs)
    assert not any(ev.actor == "fighter" for ev in s.game.last_turn.events)
    await s.close()


async def test_debug_tick_and_player_death(settings: Settings) -> None:
    s = await new_session(settings, debug=True)
    await s.play(*QUIET_YARD)
    st = s.state
    t0 = st.tick
    evs = await s.send("/debug tick 20")
    assert st.tick == t0 + 20 and header_of(evs) is not None and options_of(evs) is not None
    # 玩家：同一来源的第一次致命结果降级为重伤（6.10），再来一次才是死
    evs = await s.send(f"/debug kill {st.player}")
    assert st.ended is None and has_status(st.chars[st.player], "injured")
    evs = await s.send(f"/debug kill {st.player}")
    end = ended_of(evs)
    assert end is not None and end.reason == "dead" and st.ended == "dead"
    assert "到这里就断了" in text_of(evs)
    await s.close()


async def test_travel_always_costs_the_whole_edge(real_settings: Settings) -> None:
    """2.5：赶路按地图边上标的耗时走完；到的地方有情境开场，是到了才碰上的，不是半路提前到。"""
    from tianlong.kernel import actions

    s = await new_session(real_settings)
    st, df = s.state, s.game.defs
    pid = st.player
    s.fx([{"move": [pid, "mountain_path"]}])
    act = actions.build(st, df, pid, "go", {"exit": "crag_foot"})
    assert act is not None and act.cost == 24
    t0 = st.tick
    evs = [ev async for ev in s.game._logged("go", "turn", s.game._play(act, None))]
    assert st.chars[pid].loc == "crag_foot"
    assert st.tick == t0 + 24  # 三个时辰的路，不因为那头有事就只算一个时辰
    assert s.game.last_turn is not None and not s.game.last_turn.interrupted
    assert any(x.kind == "zhuha" and x.loc == "crag_foot" for x in st.situations.values())  # 到了才碰上
    assert text_of(evs).strip()
    await s.close()


async def test_interrupt_on_the_hour_still_runs_world_tick(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """8.1：恰好在时辰边界上被打断，这个时辰的 world.tick 也照做，不漏掉一个时辰。"""
    import tianlong.world as world

    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    ran: list[int] = []
    real_tick = world.tick

    async def spy(llm, state, defs, **kw):  # noqa: ANN001, ANN003, ANN202
        ran.append(state.tick)
        return await real_tick(llm, state, defs, **kw)

    monkeypatch.setattr(world, "tick", spy)
    t0 = st.tick
    to_boundary = 8 - t0 % 8
    s.fx([{"schedule": [to_boundary, [{"move": ["guest", "yard"]}]]}])  # 恰好在时辰边界上有人走进来
    await s.send("我在这里等上一天")
    assert s.game.last_turn is not None and s.game.last_turn.interrupted
    assert st.tick == t0 + to_boundary and st.tick % 8 == 0
    assert ran == [st.tick]
    await s.send("我在这里等上一个时辰")
    assert ran == [t for t in range(t0 + 1, st.tick + 1) if t % 8 == 0]
    await s.close()


async def _train(s, verb: str, **params):  # noqa: ANN001, ANN003, ANN202
    """直接走一个读、练、教的回合（不经输入归类）。"""
    from tianlong.kernel import actions

    st = s.state
    act = actions.build(st, s.game.defs, st.player, verb, params)
    assert act is not None and act.cost == params["hours"] * 8
    return [ev async for ev in s.game._logged(verb, "turn", s.game._play(act, None))]


async def test_interrupted_practice_keeps_only_the_time_actually_spent(settings: Settings) -> None:
    """6.12：进度按做了多久记。吩咐练十二个时辰、两刻就被打断，不能白得十二个时辰的长进。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    s.fx([{"art": [pid, "cloud_steps", 1]}])
    hour = growth.progress_for(me, 1, teacher=False)
    assert 0 < hour < growth.stage_need(s.game.defs, "cloud_steps", 1)
    s.fx([{"schedule": [2, [{"move": ["guest", "yard"]}]]}])  # 两刻之后有人走进来
    t0 = st.tick
    await _train(s, "practice", art="cloud_steps", hours=12)
    assert s.game.last_turn is not None and s.game.last_turn.interrupted
    assert st.tick == t0 + 2
    assert me.arts["cloud_steps"] == 1  # 没有凭空升一层
    assert me.art_progress.get("cloud_steps", 0) == hour * 2 // 8 > 0  # 只有这两刻的份，没有预付的头一个时辰
    await s.close()


async def test_one_hour_order_interrupted_gets_no_prepaid_hour(settings: Settings) -> None:
    """只吩咐一个时辰的也一样：两刻被打断，记两刻的份（D-360：一刻也不预付）。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    s.fx([{"spawn": ["old_manual", pid]}])
    hour = growth.progress_for(me, 1, teacher=False)
    s.fx([{"schedule": [2, [{"move": ["guest", "yard"]}]]}])
    t0 = st.tick
    evs = await _train(s, "study", item="old_manual", art="cloud_steps", hours=1)
    assert s.game.last_turn is not None and s.game.last_turn.interrupted and st.tick == t0 + 2
    assert me.arts.get("cloud_steps", 0) == 0
    assert me.art_progress.get("cloud_steps", 0) == hour * 2 // 8 > 0
    assert text_of(evs).strip() and "cloud_steps" in st.known_terms
    await s.close()


@pytest.mark.parametrize("hours", [1, 2, 3])
async def test_uninterrupted_orders_credit_what_the_kernel_credits_in_one_go(settings: Settings, hours: int) -> None:
    """没被打断：读、练、教记的总数与内核一次记满（角色自己做时）的数一刻不差。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    s.fx([{"art": [pid, "cloud_steps", 1]}])
    t0 = st.tick
    await _train(s, "practice", art="cloud_steps", hours=hours)
    assert s.game.last_turn is not None and not s.game.last_turn.interrupted and st.tick == t0 + hours * 8
    want = growth.progress_for(me, hours, teacher=False)
    assert want < growth.stage_need(s.game.defs, "cloud_steps", 1)
    assert me.arts["cloud_steps"] == 1 and me.art_progress.get("cloud_steps", 0) == want

    student = _student_in_yard(s)
    t1 = st.tick
    await _train(s, "teach", target="fighter", art="literacy", hours=hours)
    assert not s.game.last_turn.interrupted and st.tick == t1 + hours * 8
    assert student.literacy == 0
    assert student.art_progress.get("literacy", 0) == growth.progress_for(student, hours, teacher=True)
    await s.close()


async def test_practice_progress_follows_elapsed_time(settings: Settings) -> None:
    """进度随实际过去的刻数记：二十刻被打断，记到二十刻为止。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    s.fx([{"art": [pid, "cloud_steps", 1]}])
    hour = growth.progress_for(me, 1, teacher=False)
    s.fx([{"schedule": [20, [{"move": ["guest", "yard"]}]]}])
    t0 = st.tick
    await _train(s, "practice", art="cloud_steps", hours=12)
    assert s.game.last_turn is not None and s.game.last_turn.interrupted
    assert st.tick == t0 + 20
    assert me.arts["cloud_steps"] == 1
    assert me.art_progress.get("cloud_steps", 0) == hour * 20 // 8
    await s.close()


async def test_uninterrupted_practice_gets_all_of_it_and_the_gain_is_told(settings: Settings) -> None:
    """没被打断：十二个时辰的进度一刻不少；半路升的那一层有一条事实句，叙述看得见。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    me = st.chars[pid]
    s.fx([{"art": [pid, "cloud_steps", 1]}, {"train": [pid, "cloud_steps", 10]}])  # 先有十刻的底子
    t0 = st.tick
    evs = await _train(s, "practice", art="cloud_steps", hours=3)
    assert s.game.last_turn is not None and not s.game.last_turn.interrupted
    assert st.tick == t0 + 24
    total = 10 + growth.progress_for(me, 3, teacher=False)
    need = growth.stage_need(s.game.defs, "cloud_steps", 1)
    assert total >= need > 10 + growth.progress_for(me, 1, teacher=False)
    assert me.arts["cloud_steps"] == 2  # 三个时辰够升一层：与一次记满的结果相同
    gained = [e for e in st.events_tail if e.tick > t0 and e.actor == pid and "长进" in e.fact_text]
    assert len(gained) == 1 and gained[0].tick > t0 + 8  # 是练到后来才升的，不是一开头
    assert text_of(evs).strip()
    await s.close()


def _student_in_yard(s):  # noqa: ANN001, ANN202
    """把雷猛挪到前院、认玩家作上司，并让他在这里等着（不然他转身就回演武堂去了）。"""
    pid = s.state.player
    stay = {"id": "w1", "text": "在这里等着", "at": "yard", "do": {"verb": "wait"}, "takes": 400}
    s.fx([
        {"move": ["fighter", "yard"]},
        {"superior": ["fighter", pid]},
        {"agenda": ["fighter", {"goal": "听讲", "steps": [stay]}]},
    ])
    return s.state.chars["fighter"]


async def test_interrupted_teaching_credits_the_student_for_time_spent(settings: Settings) -> None:
    """教人也一样：进度记在徒弟身上，按实际教了多久算。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    student = _student_in_yard(s)
    assert student.literacy == 0
    hour = growth.progress_for(student, 1, teacher=True)
    s.fx([{"schedule": [12, [{"move": ["guest", "yard"]}]]}])
    t0 = st.tick
    await _train(s, "teach", target="fighter", art="literacy", hours=6)
    assert st.tick == t0 + 12 and s.game.last_turn is not None and s.game.last_turn.interrupted
    assert student.literacy == 0
    assert student.art_progress.get("literacy", 0) == hour * 12 // 8
    await s.close()


async def test_lesson_stops_when_the_student_walks_off(settings: Settings) -> None:
    """徒弟在头一轮反应里就走开了：这一课上不成，时间不白过，人不在跟前也不记进度。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    s.fx([{"move": ["fighter", "yard"]}, {"superior": ["fighter", pid]}])  # 他自己的打算是回演武堂
    student = st.chars["fighter"]
    t0 = st.tick
    evs = await _train(s, "teach", target="fighter", art="literacy", hours=6)
    assert student.loc == "hall" and st.chars[pid].loc == "yard"
    assert st.tick == t0 and s.game.last_turn is not None and s.game.last_turn.interrupted
    assert student.literacy == 0 and student.art_progress.get("literacy", 0) == 0
    assert_full_turn(evs)
    await s.close()
