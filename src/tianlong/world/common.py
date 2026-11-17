"""world/ 的公用小件：在场与离场、处理次序、记账旗标、日志。

world 只经内核改世界（效果、动作、离场结算）。这里的记账也走效果：``set_flag`` 写以 ``__`` 开头的旗标，
内容里的谓词不会读它们。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from tianlong.kernel import perception, predicates, resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Event, WorldState

LOG = logging.getLogger("tianlong.world")

# 记账旗标（值都是可以存进 JSON 的字典，每次整份替换）
REPLAN_FLAG = "__replan_at__"  # 人 → 上次重新打算的时刻（Character.last_replan 没有效果可写，见交付说明）
GOAL_FLAG = "__goal_at__"  # 人 → {规则 id: 上次选中它的时刻}（goals.yaml 的 cooldown 用）
BASELINE_FLAG = "__baseline__"  # 参照点的记账：reached / cause / scan

Decide = Callable[[WorldState, Defs, str], Awaitable[Any]]


def player_loc(state: WorldState) -> str | None:
    """玩家此刻所在的地点；玩家不在图内时为 None（那时人人都算离场）。"""
    p = state.chars.get(state.player)
    if p is None or not perception.on_map(p):
        return None
    return p.loc


def in_scene(state: WorldState, c: Character) -> bool:
    """「在场」：与玩家在同一地点（8.1）。玩家本人不算。"""
    if c.id == state.player:
        return False
    loc = player_loc(state)
    return loc is not None and c.loc == loc and perception.on_map(c)


def ordered_chars(state: WorldState) -> list[Character]:
    """推进的次序：先按 initiative 从高到低，再按 id。确定的次序让整局可以重放。"""
    return sorted(
        (c for c in state.chars.values() if c.id != state.player),
        key=lambda c: (-c.initiative, c.id),
    )


def flag_dict(state: WorldState, name: str) -> dict[str, Any]:
    """读一个记账旗标（拷一份，免得原地改写）。"""
    v = state.flags.get(name)
    return _deepcopy_jsonish(v) if isinstance(v, dict) else {}


def set_flag_dict(state: WorldState, defs: Defs, name: str, value: dict[str, Any]) -> None:
    resolve.run_effects(state, defs, [{"set_flag": [name, value]}], source="rule")


def _deepcopy_jsonish(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _deepcopy_jsonish(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_deepcopy_jsonish(x) for x in v]
    return v


def mark_offscreen(events: list[Event]) -> list[Event]:
    """离场结算出来的事件标 offscreen（8.2）。

    ``resolve.apply`` 没有 offscreen 参数（内核缺口，见交付说明），所以对它返回的事件补一笔标记。
    这个标记只是日志上的注记，没有任何谓词或规则读它。
    """
    for ev in events:
        ev.offscreen = True
    return events


def loc_of(state: WorldState, x: Any) -> str | None:
    """地点 id 原样；人取他此刻所在的地点（不在图内为 None）；物取它所在的地点。"""
    if not isinstance(x, str):
        return None
    if x in state.locations:
        return x
    c = state.chars.get(x)
    if c is not None:
        return c.loc if perception.on_map(c) else None
    if x in state.items:
        return predicates.item_location(state, x)
    return None
