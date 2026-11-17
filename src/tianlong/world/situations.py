"""情境（方案 5.7、8.1 第 2 步、9.5）：条件成立就开场。

每个时辰：

- 进行中的情境，玩家已经不在那里的，按离场结算了结（``resolve.settle_offscreen``）。
- 逐个检查情境定义的 ``when``，并在有人的地点逐个尝试绑定角色位（写死的人，或 ``{pick: {where}}``，
  谓词里 ``$it`` 指候选人）。绑不上就当没开场，不记违规（D-063）。
- 同种、同一批角色的情境正在进行的，不再开场。
- 玩家就在那里：开场（效果 ``open``），在微轮里演下去。不在：直接离场结算。

``end_of_turn``：game 每回合调用一次，情境的 turns 加一，到了 max_turns 或 exit_when 成立的散场。
"""

from __future__ import annotations

from tianlong.kernel import effects, perception, predicates, resolve
from tianlong.kernel.defs import Defs, SituationDef
from tianlong.kernel.state import Ctx, Event, WorldState
from tianlong.world.common import LOG, player_loc


def ordered_defs(defs: Defs) -> list[SituationDef]:
    """canon 在前，generic 在后；同类按 id。次序确定，整局可重放。"""
    return sorted(defs.situations.values(), key=lambda d: (d.origin != "canon", d.id))


def populated(state: WorldState) -> list[str]:
    return sorted({c.loc for c in state.chars.values() if perception.on_map(c) and c.loc in state.locations})


def _uses_roles(pred: object) -> bool:
    return "$" in repr(pred)


def _has_fixed_role(d: SituationDef) -> bool:
    return any(isinstance(spec, str) for spec in d.roles.values())


def candidate_places(state: WorldState, d: SituationDef) -> list[str | None]:
    """在哪些地点尝试绑定。None 表示让 bind_roles 按定义（at 或写死的角色）自己定地点。"""
    if d.at and not d.at.startswith("$"):
        return [d.at]
    if _has_fixed_role(d):
        return [None]
    return list(populated(state))


def running(state: WorldState, kind: str, roles: dict[str, str]) -> bool:
    return any(s.kind == kind and s.roles == roles for s in state.situations.values())


def _holds(state: WorldState, defs: Defs, pred: object, ctx: Ctx, where: str) -> bool:
    try:
        return predicates.holds(state, defs, pred, ctx)
    except predicates.KernelError as e:
        LOG.warning("situation %s: bad predicate: %s", where, e)
        return False


def check(state: WorldState, defs: Defs) -> list[Event]:
    """8.1 第 2 步。返回开场与离场结算产生的事件。"""
    out: list[Event] = []
    ploc = player_loc(state)
    for s in list(state.situations.values()):
        if s.id in state.situations and s.loc != ploc:
            out += resolve.settle_offscreen(state, defs, s.id)
    for d in ordered_defs(defs):
        if d.when is False or (isinstance(d.when, str) and d.when.lower() == "false"):
            continue
        late_when = _uses_roles(d.when)
        if not late_when and not _holds(state, defs, d.when, Ctx(), d.id):
            continue
        for place in candidate_places(state, d):
            try:
                bound = effects.bind_roles(state, defs, d, Ctx(), place)
            except predicates.KernelError as e:
                LOG.warning("situation %s: cannot bind: %s", d.id, e)
                bound = None
            if bound is None:
                continue
            loc, roles = bound
            if late_when and not _holds(state, defs, d.when, Ctx(roles=dict(roles)), d.id):
                continue
            if not late_when and not _holds(state, defs, d.when, Ctx(), d.id):
                break  # 前一处的结算已经让条件不成立了
            if running(state, d.id, roles):
                continue
            if loc == player_loc(state):
                out += resolve.run_effects(state, defs, [{"open": [d.id, loc, dict(roles)]}], source="situation")
            else:
                out += resolve.settle_offscreen(state, defs, d.id, loc=loc, roles=dict(roles))
    return out


def end_of_turn(state: WorldState, defs: Defs) -> list[str]:
    """game 每回合调用一次：turns 加一；exit_when 成立或到了 max_turns 的散场。返回散掉的情境 id。"""
    return effects.tick_situations(state, defs)


__all__ = ["check", "end_of_turn", "ordered_defs", "candidate_places", "running"]
