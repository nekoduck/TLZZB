"""检查点与反复重来（方案 2.6、8.7）：大险之前的检查点不把每日之始的挤掉；从检查点重来之后，放弃了的那条路上的检查点不再留着。"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.game import MAX_DANGER_CHECKPOINTS

from .conftest import ended_of, new_session, notices

QUIET_YARD = ("一", "二", "我去前院看看")


async def test_retry_loop_keeps_day_start_checkpoints(settings: Settings) -> None:
    s = await new_session(settings, debug=True)
    g = s.game
    await s.play(*QUIET_YARD)
    for _ in range(3):
        await s.send("/debug tick 96")
    days = [c.label for c in g.checkpoints.list() if c.label.startswith("day")]
    assert days == ["day4", "day3", "day2", "day1"]
    # 中了毒，眼看要死：死了读档，换个做法再来，又死……十几回
    st = s.state
    s.fx([{"add_status": [st.player, "poisoned_slow", {"ticks": 40}]}])
    g._save()
    deaths = 0
    ticks: set[int] = set()
    for i in range(14):
        if i % 2:
            await s.send("我四下看看")  # 每次重来先做点别的：死前那一刻各不相同
        evs = await s.send("我在这里等上一天")
        if ended_of(evs) is None:
            continue
        deaths += 1
        cps = g.checkpoints.list()
        assert cps[0].label == "danger"
        ticks.add(cps[0].tick)
        assert sum(c.label == "danger" for c in cps) <= MAX_DANGER_CHECKPOINTS
        assert [c.label for c in cps if c.label.startswith("day")] == days  # 每日之始的一个也没丢
        evs = await s.send("/读档 检查点")
        assert "已读档" in notices(evs)[0] and s.state.ended is None
    assert deaths >= 10 and len(ticks) >= 3
    # 退回三天前另寻出路：那时还没中毒
    day2 = next(c for c in g.checkpoints.list() if c.label == "day2")
    await s.send(f"/读档 检查点 {day2.seq}")
    assert s.state.tick == day2.tick and s.state.ended is None
    assert not any(x.kind == "poisoned_slow" for x in s.state.chars[s.state.player].statuses)
    await s.close()


async def test_restoring_a_checkpoint_drops_the_abandoned_future(settings: Settings) -> None:
    s = await new_session(settings, debug=True)
    g = s.game
    await s.play(*QUIET_YARD)
    for _ in range(3):
        await s.send("/debug tick 96")
    assert [c.label for c in g.checkpoints.list()] == ["day4", "day3", "day2", "day1"]
    day2 = next(c for c in g.checkpoints.list() if c.label == "day2")
    await s.send(f"/读档 检查点 {day2.seq}")
    # 第三、四日是放弃了的那条路：它们的检查点不再留着
    assert [c.label for c in g.checkpoints.list()] == ["day2", "day1"]
    # 新的路上，每日之始照常另存
    await s.send("/debug tick 96")
    cps = g.checkpoints.list()
    assert [c.label for c in cps] == ["day3", "day2", "day1"]
    assert cps[0].tick == s.state.tick
    # 读一个具名的存档也一样：检查点回到存档那时的样子（存档连同检查点一起复制）
    await s.send("/存档 备份")
    await s.send("/debug tick 96")
    assert [c.label for c in g.checkpoints.list()] == ["day4", "day3", "day2", "day1"]
    await s.send("/读档 备份")
    assert [c.label for c in g.checkpoints.list()] == ["day3", "day2", "day1"]
    await s.send("/debug tick 96")
    assert [c.label for c in g.checkpoints.list()] == ["day4", "day3", "day2", "day1"]
    await s.close()
