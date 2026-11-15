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
