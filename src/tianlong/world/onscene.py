"""在场者的「等」（I6）：玩家的一个动作过去了几刻，在场的人原地等着的那一步也该跟着走几刻。

在场的人不由 ``world.tick`` 推进（8.1 第 3 步只管离场者），他们在微轮里自己抉择。可微轮里等一次只记 1 刻
（D-041），玩家一歇就是几个时辰时，「在这里等到某时」的一步会远远落在后面。``onscene_elapsed`` 把这段
时间补上：只补「等」这一种步骤，别的步骤仍要他自己在微轮里动手。全部经效果 ``progress`` 与 ``step_state``。
"""

from __future__ import annotations

from collections.abc import Mapping

from tianlong.kernel import actions, danger, perception, resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.predicates import KernelError
from tianlong.kernel.state import Character, Ctx, Event, Step, WorldState
from tianlong.world.common import LOG, in_scene, ordered_chars

WAIT_VERBS = ("wait",)
MAX_STEPS = 8  # 一段时间里一个人至多连着等完几步（防内容写出零耗时的死循环）


def waiting_step(state: WorldState, defs: Defs, c: Character) -> Step | None:
    """此人眼下这一步是不是「就在这里等」：是就返回这一步。前提不成立、没到时候、要去别处的都不算。"""
    st = danger.current_step(c)
    if st is None or st.do.get("verb") not in WAIT_VERBS:
        return None
    if st.at is not None and st.at != c.loc:
        return None
    try:
        if not actions.step_ready(state, defs, c, st):
            return None
    except KernelError as e:
        LOG.warning("onscene: step %s of %s: %s", st.id, c.id, e)
        return None
    return st


def onscene_elapsed(
    state: WorldState,
    defs: Defs,
    ticks: int,
    *,
    credited: Mapping[str, int] | None = None,
) -> list[Event]:
    """时间过去了 ``ticks`` 刻：在场、清醒、当前这一步是「在这里等」的人，各记上这段时间；等够了就算做完。

    ``credited``：这一回合的微轮里已经给谁记过几刻（人 → 刻），从这段时间里扣掉，免得一刻记两回。
    返回效果产生的事件（通常为空：记账不出事件）。
    """
    out: list[Event] = []
    if ticks <= 0 or state.ended is not None:
        return out
    for c in ordered_chars(state):
        if not in_scene(state, c) or not perception.conscious(c):
            continue
        left = int(ticks) - int((credited or {}).get(c.id, 0))
        for _ in range(MAX_STEPS):
            if left <= 0:
                break
            st = waiting_step(state, defs, c)
            ag = c.agenda
            if st is None or ag is None:
                break
            need = max(0, int(st.takes) - int(ag.progress))
            spend = min(left, need)
            effs: list[dict] = []
            if spend > 0:
                effs.append({"progress": [c.id, spend]})
            done = spend >= need
            if done:
                effs.append({"step_state": [c.id, st.id, "done"]})
            if effs:
                out += resolve.run_effects(state, defs, effs, Ctx(self_id=c.id), source="agenda", step=st.id)
            left -= spend
            if not done:
                break
    return out


__all__ = ["onscene_elapsed", "waiting_step"]
