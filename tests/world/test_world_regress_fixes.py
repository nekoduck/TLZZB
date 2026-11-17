"""复查 D-347 到 D-366 后的回归修补（world 一侧）：动身前的前提检查只在动身前、头一次不成立先等一个时辰；
玩家只是站着不动时，原著的那条线照旧走下去。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tianlong.kernel import predicates, rng, timeflow
from tianlong.kernel.state import Agenda, Ctx, Step
from tianlong.testing.driver import Driver, make_settings
from tianlong.world import offscreen

from .conftest import fx, player_off


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把 d100 换成给定的点数序列（用完以后一律 50）。"""
    seq: list[int] = []

    def roll(state):  # type: ignore[no-untyped-def]
        state.rng_counter += 1
        return seq.pop(0) if seq else 50

    monkeypatch.setattr(rng, "roll", roll)

    def set_(*vals: int) -> None:
        seq[:] = list(vals)

    return set_


@pytest.fixture
def voids(caplog: pytest.LogCaptureFixture) -> list[str]:
    """这一局里作废的步骤（world 的日志）：``<人> <步骤> (<原因>) at tick <刻>``。"""
    caplog.set_level(logging.INFO, logger="tianlong.world")

    class _Voids(list):
        def refresh(self) -> list[str]:
            self[:] = [
                r.getMessage().replace("step void: ", "")
                for r in caplog.records
                if r.getMessage().startswith("step void")
            ]
            return self

    return _Voids()


def _step(c, sid: str):  # noqa: ANN001, ANN202
    return next(s for s in c.agenda.steps if s.id == sid)


async def _wait_to(d: Driver, until: int) -> dict[str, int]:
    """玩家原地一个时辰一个时辰地等到 ``until``；返回这期间成立过的参照点（点 → 头一次成立的时刻）。"""
    reached: dict[str, int] = {}
    while d.tick < until and d.state.ended is None:
        waits = [a for a in d.legal("wait") if a.params.get("ticks") == 8] or d.legal("wait")
        assert waits, f"cannot wait at tick {d.tick}"
        await d.act(waits[0])
        for m in d.defs.baseline:
            if m.id not in reached and predicates.holds(d.state, d.defs, m.holds, Ctx()):
                reached[m.id] = d.tick
    return reached


# ---------------------------------------------------------------- 5. 玩家只在大厅里等：钟灵照旧被扣


@pytest.mark.parametrize("seed", [0, 2])
async def test_waiting_in_the_hall_leaves_the_hostage_line_intact(tmp_path: Path, voids, seed: int) -> None:  # noqa: ANN001
    """玩家什么也不做：钟灵跟着段誉进营、被扣、等人来救这一步做完（不是没等到被扣就作废），段誉的差事照走。"""
    d = await Driver.new(make_settings(tmp_path), seed=seed)
    reached = await _wait_to(d, 216)
    assert d.me.loc == "jianhu_hall"
    zl = d.char("zhong_ling")
    canon = [s.id for s in zl.agenda.steps]
    assert "zl_wait_rescue" in canon, f"zhong_ling dropped her opening agenda: {canon}"
    assert _step(zl, "zl_wait_rescue").state == "done"
    bad = [v for v in voids.refresh() if v.split()[1] in ("zl_wait_rescue", "dy_leave_camp", "dy_errand_wanjie")]
    assert not bad, bad
    due = [m.id for m in d.defs.baseline if (m.by or 0) <= 216]
    assert [m for m in due if m not in reached] == []
    await d.close()


# ---------------------------------------------------------------- 6. 途中不再查前提


async def test_premise_is_not_rechecked_on_the_road(llm, world) -> None:  # noqa: ANN001
    """D-362：前提在动身前和动手时各查一次。路上某一个时辰不成立，到了又成立的，这一步照样做成。"""
    defs, state = world
    player_off(state, defs)
    c = state.chars["sn_chief_b"]
    step = {"id": "t_far", "text": "去渡口办事", "at": "ferry_bridge", "do": {"verb": "wait"}, "takes": 1,
            "pre": {"flag": "errand_wanted"}}  # fmt: skip
    fx(state, defs, [{"set_flag": ["errand_wanted", True]}, {"step": ["sn_chief_b", step]}])
    st = _step(c, "t_far")
    start = c.loc
    seen: list[tuple[int, str, str]] = []
    for hour in range(1, 12):
        timeflow.advance_time(state, defs, 8)
        if hour == 2:  # 他已经在路上：前提只在这一个时辰里不成立
            fx(state, defs, [{"set_flag": ["errand_wanted", False]}])
        if hour == 3:
            fx(state, defs, [{"set_flag": ["errand_wanted", True]}])
        await offscreen.advance(state, defs, "sn_chief_b", 8)
        seen.append((hour, c.loc, st.state))
        if st.state != "pending":
            break
    assert st.state == "done", seen
    assert c.loc == "ferry_bridge" != start


async def test_premise_false_on_arrival_still_voids(llm, world) -> None:  # noqa: ANN001
    """路上不查，动手时照查：到了地方前提不成立，这一步作废。"""
    defs, state = world
    player_off(state, defs)
    c = state.chars["sn_chief_b"]
    step = {"id": "t_far", "text": "去渡口办事", "at": "ferry_bridge", "do": {"verb": "wait"}, "takes": 1,
            "pre": {"flag": "errand_wanted"}}  # fmt: skip
    fx(state, defs, [{"set_flag": ["errand_wanted", True]}, {"step": ["sn_chief_b", step]}])
    st = _step(c, "t_far")
    for hour in range(1, 12):
        timeflow.advance_time(state, defs, 8)
        if hour == 2:
            fx(state, defs, [{"set_flag": ["errand_wanted", False]}])
        await offscreen.advance(state, defs, "sn_chief_b", 8)
        if st.state != "pending":
            break
    assert st.state == "void" and c.loc == "ferry_bridge"


# ---------------------------------------------------------------- 7. 动身前头一次不成立：先等一个时辰


async def test_false_premise_waits_one_hour_before_voiding(llm, world) -> None:  # noqa: ANN001
    """动身前查到前提不成立：这个时辰原地等，下个时辰仍不成立才作废（不白走一趟，也不为一时的空档丢掉打算）。"""
    defs, state = world
    player_off(state, defs)
    chief = state.chars["sn_chief_b"]
    step = {"id": "t_far", "text": "去剑湖宫", "at": "jianhu_hall", "do": {"verb": "wait"}, "takes": 1,
            "pre": {"flag": "wanted"}}  # fmt: skip
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    st = _step(chief, "t_far")
    before = chief.loc
    evs = await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert st.state == "pending" and chief.loc == before and chief.agenda.progress == 0  # 没作废，也没动身
    assert not any(e.verb in ("go", "arrive") for e in evs)
    # 同一个时辰里再问一遍不算「下个时辰」
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert st.state == "pending"
    timeflow.advance_time(state, defs, 8)
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert st.state == "void" and chief.loc == before
    assert "sn_chief_b" in state.replan_queue


async def test_premise_that_comes_true_within_the_hour_is_kept(llm, world) -> None:  # noqa: ANN001
    defs, state = world
    player_off(state, defs)
    chief = state.chars["sn_chief_b"]
    step = {"id": "t_far", "text": "去剑湖宫", "at": "jianhu_hall", "do": {"verb": "wait"}, "takes": 1,
            "pre": {"flag": "wanted"}}  # fmt: skip
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    st = _step(chief, "t_far")
    before = chief.loc
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert st.state == "pending" and chief.loc == before
    fx(state, defs, [{"set_flag": ["wanted", True]}])  # 这个时辰里前提成立了（例如眼前的情境这才把旗标立起来）
    timeflow.advance_time(state, defs, 8)
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert st.state == "pending" and (chief.loc != before or chief.agenda.progress > 0)  # 动身了
    # 之前那一次不成立不留底：以后再不成立，仍从头一次算起
    assert chief.agenda.doubt is None


async def test_siege_opened_in_front_of_the_player_does_not_void_the_defenders(tmp_path: Path, voids) -> None:  # noqa: ANN001
    """玩家站在山门：围山的情境在他眼前开场，旗标要到在场的人动手才立起来。大厅里的人不该在这之前就把
    「去守山门」作废。"""
    d = await Driver.new(make_settings(tmp_path), seed=0)
    go = d.legal("go", exit="jianhu_gate")
    assert go
    await d.act(go[0])
    reached = await _wait_to(d, 120)
    gone = [v for v in voids.refresh() if "before setting out" in v and "at tick 56" in v]
    assert not gone, gone
    zz = d.char("zuo_zimu")
    assert _step(zz, "zz_defend").state != "void"
    assert d.state.flags.get("siege_on")
    for m in ("m_captured_camp", "m_diao_bite", "m_poison_errand"):
        assert m in reached, (m, sorted(reached))
    await d.close()


# ---------------------------------------------------------------- 2. 离场的人闯出口没闯成


async def test_offscene_walker_stopped_at_the_door_keeps_his_leg(llm, world, dice) -> None:  # noqa: ANN001
    """离场的人走完一段路的工夫才掷那一下：被拦下来的，这段路已经花的工夫不清零，下个时辰接着闯，
    不必原地再站满一整段路的时间。"""
    defs, state = world
    player_off(state, defs)
    c = state.chars["sn_chief_b"]
    guard = state.chars["sn_guard_3"]
    assert guard.loc == c.loc == "mountain_path"
    step = {"id": "t_far", "text": "去崖下", "at": "crag_foot", "do": {"verb": "wait"}, "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert c.loc == "mountain_path" and c.agenda.progress == 16  # 这段路 24 刻，走了 16
    # 有人要拿他（眼下这一步就是冲他来的）：最后一个时辰走到头时要闯
    guard.base_power = 9
    guard.agenda = Agenda(
        goal="拿人",
        steps=[Step(id="g1", text="拿下", do={"verb": "restrain", "target": "sn_chief_b"}, start=10**6)],
    )
    dice(100)  # 没闯过去：被追上、捆了
    evs = await offscreen.advance(state, defs, "sn_chief_b", 8)
    assert c.loc == "mountain_path" and any(e.verb == "go" and e.outcome == "fail" for e in evs)
    assert c.agenda.progress >= 16, "the leg he had already walked was thrown away"
