"""世界时钟（方案 8.1）：``tick`` 每个时辰做一次，五步按顺序。

game 推进时间（``kernel.timeflow.advance_time``）时每跨过一个时辰边界调用一次；这里不推进时间本身。

1. 执行到点的 ``scheduled``（advance_time 已经逐刻执行过；这里补上本时辰里刚排进来的 delay 0 的条目）。
2. 检查所有情境定义的 ``when``：在玩家眼前就开场，不在就直接离场结算（``world/situations.py``）。
3. 推进每个离场者当前这一步打算（``world/offscreen.py``）。在场的人在微轮里自己抉择，这里只替他们
   作废过期的步骤。
4. 活性检查：无事可做的人进重新打算的队列；每日卯时把在等的人也放进去；然后消费队列（``world/replan.py``）。
5. 对照参照点（``world/baseline.py``）。牵挂与年谱由 storyteller 更新（game 接线）。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import invariants, perception, predicates, resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, TICKS_PER_HOUR, Ctx, Event, WorldState
from tianlong.world import baseline, offscreen, replan, situations
from tianlong.world.common import LOG, Decide, in_scene, ordered_chars

DAWN = 3 * TICKS_PER_HOUR  # 卯时初：每日清晨的活性检查


def run_due_scheduled(state: WorldState, defs: Defs) -> list[Event]:
    """到点的 scheduled。内核没有公开的入口（advance_time 只在推进时间时执行），这里借用它的内部函数。"""
    return timeflow._run_scheduled(state, defs)  # noqa: SLF001


def finished_by_done_when(state: WorldState, defs: Defs) -> list[str]:
    """打算的 done_when 成立：这份打算算成了（8.3「打算做完了，或 done_when 成立」）。"""
    out: list[str] = []
    for c in state.chars.values():
        ag = c.agenda
        if ag is None or ag.status == "done" or not ag.done_when or not perception.on_map(c):
            continue
        try:
            done = predicates.holds(state, defs, ag.done_when, Ctx(self_id=c.id))
        except predicates.KernelError as e:
            LOG.warning("done_when of %s: %s", c.id, e)
            continue
        if done:
            data = ag.model_dump()
            data["status"] = "done"
            resolve.run_effects(state, defs, [{"agenda": [c.id, data]}, {"replan": c.id}], Ctx(self_id=c.id), source="rule")
            out.append(c.id)
    return out


def liveness(state: WorldState, defs: Defs, *, dawn: bool = False) -> list[str]:
    """6.11 的活性约束：不满足的人记日志并放进重新打算的队列。清晨另把在等的人也放进去。"""
    finished_by_done_when(state, defs)
    stalled = invariants.liveness(state, defs)
    for cid in stalled:
        LOG.info("liveness: %s has nothing to do at tick %s", cid, state.tick)
    extra: list[str] = []
    if dawn:
        for c in state.chars.values():
            ag = c.agenda
            if (
                c.tier in ("soul", "named")
                and perception.on_map(c)
                and ag is not None
                and ag.status == "waiting"
                and c.id not in stalled
            ):
                extra.append(c.id)
    replan.queue(state, defs, stalled + extra)
    return stalled


async def tick(llm: Any, state: WorldState, defs: Defs, *, decide: Decide | None = None, rules_only: bool = False) -> list[Event]:
    """一个时辰的世界推演。``decide``：离场的 say 要对方答复时问它（game 传 minds.decide_one）。"""
    out: list[Event] = []
    if state.ended is not None:
        return out
    # 1 到点的 scheduled
    out += run_due_scheduled(state, defs)
    # 2 情境
    out += situations.check(state, defs)
    out += run_due_scheduled(state, defs)
    # 3 离场者的打算
    chars = ordered_chars(state)
    out += offscreen.expire_deadlines(state, defs, [c for c in chars if in_scene(state, c)])
    for c in chars:
        if in_scene(state, c):
            continue
        if c.agenda is None:
            continue
        out += await offscreen.advance(state, defs, c.id, TICKS_PER_HOUR, decide=decide)
    out += run_due_scheduled(state, defs)
    # 4 活性检查与重新打算
    liveness(state, defs, dawn=(state.tick % TICKS_PER_DAY == DAWN))
    out += await replan.process_queue(llm, state, defs, rules_only=rules_only)
    # 5 参照点
    baseline.check(state, defs)
    return out


__all__ = ["tick", "liveness", "run_due_scheduled"]
