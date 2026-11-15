"""讲述者各部分共用的小工具：记账、玩家视角的渲染、专名检查、看得见的东西。

讲述者自己的状态全在 ``state.storyteller``（经 ``teller`` 效果写入）、``state.threads``（``thread`` /
``close_thread``）与 ``state.chronicle``（``chronicle``）。这里的 ``book`` 是唯一的写入口，走
``resolve.run_effects``，出错整体回滚并记进 ``violations``，不会拖垮游戏。
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from tianlong.kernel import perception, predicates, refs, resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Ctx, Event, WorldState

SOURCE = "storyteller"


# ---------------------------------------------------------------- 记账


def teller(state: WorldState, key: str, default: Any = None) -> Any:
    """读讲述者的一项记账。返回深拷贝：调用方改了也不会绕过效果改到状态。"""
    if key not in state.storyteller:
        return copy.deepcopy(default)
    return copy.deepcopy(state.storyteller[key])


def book(state: WorldState, defs: Defs, effs: list[dict], ctx: Ctx | None = None) -> list[Event]:
    """执行一串记账类效果（teller / thread / close_thread / chronicle / diverge）。带回滚。"""
    if not effs:
        return []
    return resolve.run_effects(state, defs, effs, ctx or Ctx(self_id=state.player), source=SOURCE)


def teller_effects(values: dict[str, Any]) -> list[dict]:
    return [{"teller": [k, v]} for k, v in values.items()]


# ---------------------------------------------------------------- 渲染


def player(state: WorldState) -> Character | None:
    return state.chars.get(state.player)


def pv(state: WorldState, defs: Defs, text: str) -> str:
    """玩家视角的渲染：玩家写成「你」，不知名的人、地、物用叫法。"""
    return refs.render(text, state, defs, viewer=state.player)


def omni(state: WorldState, defs: Defs, text: str) -> str:
    """收场后的全知视角：玩家仍写成「你」，其余一律用真名。"""
    return refs.render((text or "").replace(f"⟦{state.player}⟧", refs.YOU), state, defs, viewer=None)


def clock(tick: int) -> str:
    return timeflow.clock_text(tick)


def days_left_text(state: WorldState, until: int) -> str:
    left = until - state.tick
    if left <= 0:
        return "已到期"
    days, rest = divmod(left, 96)
    hours = rest // 8
    if days == 0:
        return f"还剩{timeflow.cn_num(max(1, hours))}个时辰" if hours else "就在眼前"
    return f"还剩{timeflow.cn_num(days)}日" + (f"{timeflow.cn_num(hours)}个时辰" if hours else "")


# ---------------------------------------------------------------- 专名


def knows_entity(state: WorldState, eid: str) -> bool:
    """玩家知不知道这个实体的真名（只知其姓、有个特别叫法的不算）。"""
    return eid == state.player or eid in state.known_terms


def unknown_names(state: WorldState, defs: Defs) -> list[str]:
    """玩家还不知道的专名（真名）。真名本身就是叫法的一部分的（叫法只是在真名前加了个量词）不算。"""
    out: list[str] = []
    table = {**defs.lexicon.names, **defs.lexicon.terms}
    for eid, term in table.items():
        if knows_entity(state, eid):
            continue
        name = term.name or ""
        if len(name) < 2 or name in (term.alias or ""):
            continue
        out.append(name)
    return out


def unresolved_refs(state: WorldState, defs: Defs, text: str) -> list[str]:
    """文字里认不出的 ⟦id⟧（渲染时会原样露出 id）。"""
    return [e for e in refs.refs_in(text) if refs.lookup(state, defs, e) is None and e != state.player]


def reveals_unknown_name(state: WorldState, defs: Defs, text: str, names: Iterable[str] | None = None) -> bool:
    """按玩家视角渲染后，文字里是否露出了玩家还不知道的专名（叙述者那张黑名单的同一口径）。"""
    shown = pv(state, defs, text)
    for eid in refs.refs_in(text):
        disp = refs.display_name(state, defs, eid, state.player)
        if disp:
            shown = shown.replace(disp, "　")
    pool = list(names) if names is not None else unknown_names(state, defs)
    return any(n in shown for n in pool)


# ---------------------------------------------------------------- 看得见的东西


def believes(c: Character, fid: str) -> bool:
    b = c.beliefs.get(fid)
    return b is not None and b.confidence >= predicates.BELIEVE


def saw_in_events(state: WorldState, eid: str) -> bool:
    """玩家亲眼见过的事件里提到过这个实体。"""
    for ev in state.events_tail:
        if ev.witnesses.get(state.player) != "saw":
            continue
        if eid in refs.refs_in(ev.fact_text) or eid in ev.params.values():
            return True
    return False


def item_visible(state: WorldState, defs: Defs, iid: str) -> bool:
    """玩家知不知道这件东西此刻在哪：自己身上的、地上明处的；别人身上的，要见过、细看过那人，
    或者相信一件同时提到那人与这件东西的事。"""
    p = player(state)
    it = state.items.get(iid)
    if p is None or it is None or not it.in_world:
        return False
    if it.holder == p.id:
        return True
    if it.holder is None:
        return it.loc == p.loc and it.feature is None
    h = state.chars.get(it.holder)
    if h is None or h.loc != p.loc or not perception.visible_to(p, h):
        return False
    if it.props.get("visible") or "mount" in it.tags:
        return True
    if f"char:{h.id}" in state.examined or f"item:{iid}" in state.examined:
        return True
    for fid, f in state.facts.items():
        if believes(p, fid):
            ids = refs.refs_in(f.text)
            if iid in ids and h.id in ids:
                return True
    return saw_in_events(state, iid)


def visible_people_ids(state: WorldState) -> list[str]:
    p = player(state)
    if p is None or not perception.on_map(p):
        return []
    return [o.id for o in perception.visible_people(state, p)]
