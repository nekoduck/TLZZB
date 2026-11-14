"""谓词（方案 5.2）：``holds(state, defs, pred, ctx) -> bool``。

参数里 ``self`` 指 ``ctx.self_id``，``player`` 指 ``state.player``，``$名字`` 指 ``ctx.roles[名字]``；
其余字符串一律当作 id。旗标名以 ``.`` 开头时是当前情境的局部旗标。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import growth
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Ctx, WorldState, has_status, is_alive


class KernelError(Exception):
    """内核拒绝执行：内容写错了，或状态与动作对不上。resolve 会整体回滚。"""


class PredicateError(KernelError):
    pass


# 每个谓词键的参数形状，给 content/validate.py 做语法检查。
# 'list:<类型>…' 表示定长列表；'pred' / 'preds' 是子谓词；其余是单个参数的类型。
PREDICATE_SHAPES: dict[str, Any] = {
    "all": "preds",
    "any": "preds",
    "not": "pred",
    "at": ["char", "loc"],
    "same_loc": ["char", "char"],
    "onstage": "char",
    "has": ["char", "item"],
    "item_at": ["item", "loc"],
    "knows": ["char", "fact"],
    "rel": ["char", "char", "relfield", "op", "int"],
    "friend_here": ["char", "int"],
    "others_here": ["char", "int"],
    "flag": "flag",
    "status": ["char", "str"],
    "alive": "char",
    "role": ["char", "str"],
    "art": ["char", "art", "int"],
    "stat": ["char", "statfield", "op", "int"],
    "time_ge": "int",
    "time_le": "int",
    "step": ["char", "str", "stepstate"],
    "situation": "str",
    "request": ["str", "reqstatus"],
}

OPS = (">=", "<=", ">", "<", "==", "!=")
REL_FIELDS = ("trust", "affinity", "fear", "regard")
STAT_FIELDS = ("neili", "literacy", "power", "luck", "aptitude", "base_power")
STEP_STATES = ("pending", "done", "void")
REQ_STATUSES = ("open", "agreed", "refused", "countered", "expired")

BELIEVE = 0.6  # {knows} 的门槛


def resolve_id(arg: Any, state: WorldState, ctx: Ctx | None) -> Any:
    """把 ``self`` / ``player`` / ``$角色位`` 换成 id；其余原样返回。未绑定的角色位为 None。"""
    if not isinstance(arg, str):
        return arg
    if arg == "self":
        return ctx.self_id if ctx else None
    if arg == "player":
        return state.player
    if arg.startswith("$"):
        return ctx.roles.get(arg[1:]) if ctx else None
    return arg


def flag_key(name: str, ctx: Ctx | None) -> str:
    """``.名字`` → ``<情境id>.名字``；其余原样。"""
    if name.startswith(".") and ctx is not None and ctx.situation:
        return f"{ctx.situation}{name}"
    return name


def loc_of(arg: Any, state: WorldState, ctx: Ctx | None) -> str | None:
    """地点参数：地点 id 原样；若是人（含角色位），取他所在的地点。"""
    v = resolve_id(arg, state, ctx)
    if v is None:
        return None
    if v in state.locations:
        return v
    c = state.chars.get(v)
    if c is not None:
        return c.loc
    return v


def compare(a: float, op: str, b: float) -> bool:
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    raise PredicateError(f"bad op {op!r}")


def _char(state: WorldState, ctx: Ctx | None, arg: Any) -> Character | None:
    cid = resolve_id(arg, state, ctx)
    if cid is None:
        return None
    return state.chars.get(cid)


def _onmap(c: Character | None) -> bool:
    return c is not None and c.onstage and is_alive(c)


def _args(arg: Any, n: int, key: str) -> list[Any]:
    if not isinstance(arg, list) or len(arg) != n:
        raise PredicateError(f"{key}: expects a list of {n}, got {arg!r}")
    return arg


def item_location(state: WorldState, item_id: str) -> str | None:
    """物品此刻所在的地点（被人拿着时取那人的地点）。"""
    it = state.items.get(item_id)
    if it is None or not it.in_world:
        return None
    if it.holder:
        h = state.chars.get(it.holder)
        return h.loc if h else None
    return it.loc


def stat_value(state: WorldState, defs: Defs, c: Character, fieldname: str) -> int:
    if fieldname == "power":
        return growth.power(state, defs, c)
    if fieldname not in STAT_FIELDS:
        raise PredicateError(f"stat: unknown field {fieldname!r}")
    return int(getattr(c, fieldname))


def holds(state: WorldState, defs: Defs, pred: Any, ctx: Ctx | None = None) -> bool:
    """谓词是否成立。``None`` 与 ``True`` / ``'true'`` 恒真。"""
    if pred is None or pred is True:
        return True
    if pred is False:
        return False
    if isinstance(pred, str):
        if pred.lower() == "true":
            return True
        if pred.lower() == "false":
            return False
        raise PredicateError(f"bad predicate {pred!r}")
    if isinstance(pred, list):
        return all(holds(state, defs, p, ctx) for p in pred)
    if not isinstance(pred, dict) or len(pred) != 1:
        raise PredicateError(f"bad predicate {pred!r}")
    key, arg = next(iter(pred.items()))
    fn = _PREDICATES.get(key)
    if fn is None:
        raise PredicateError(f"unknown predicate {key!r}")
    return fn(state, defs, arg, ctx)


def _p_all(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    if not isinstance(arg, list):
        raise PredicateError("all: expects a list")
    return all(holds(state, defs, p, ctx) for p in arg)


def _p_any(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    if not isinstance(arg, list):
        raise PredicateError("any: expects a list")
    return any(holds(state, defs, p, ctx) for p in arg)


def _p_not(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    return not holds(state, defs, arg, ctx)


def _p_at(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, loc = _args(arg, 2, "at")
    c = _char(state, ctx, a)
    return _onmap(c) and c is not None and c.loc == loc_of(loc, state, ctx)


def _p_same_loc(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, b = _args(arg, 2, "same_loc")
    ca, cb = _char(state, ctx, a), _char(state, ctx, b)
    return _onmap(ca) and _onmap(cb) and ca is not None and cb is not None and ca.loc == cb.loc


def _p_onstage(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    c = _char(state, ctx, arg)
    return c is not None and c.onstage


def _p_has(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, item = _args(arg, 2, "has")
    cid = resolve_id(a, state, ctx)
    it = state.items.get(resolve_id(item, state, ctx))
    return it is not None and it.in_world and cid is not None and it.holder == cid


def _p_item_at(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    item, loc = _args(arg, 2, "item_at")
    it = state.items.get(resolve_id(item, state, ctx))
    return it is not None and it.in_world and it.holder is None and it.loc == loc_of(loc, state, ctx)


def _p_knows(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, fact = _args(arg, 2, "knows")
    c = _char(state, ctx, a)
    if c is None:
        return False
    b = c.beliefs.get(resolve_id(fact, state, ctx))
    return b is not None and b.confidence >= BELIEVE


def _p_rel(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, b, fieldname, op, n = _args(arg, 5, "rel")
    ca = _char(state, ctx, a)
    bid = resolve_id(b, state, ctx)
    if ca is None or bid is None:
        return False
    if fieldname not in REL_FIELDS:
        raise PredicateError(f"rel: bad field {fieldname!r}")
    r = ca.rel.get(bid)
    v = getattr(r, fieldname) if r is not None else 0
    return compare(v, op, n)


def _present_others(state: WorldState, c: Character) -> list[Character]:
    return [o for o in state.chars.values() if o.id != c.id and _onmap(o) and o.loc == c.loc]


def _p_friend_here(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, n = _args(arg, 2, "friend_here")
    c = _char(state, ctx, a)
    if not _onmap(c) or c is None:
        return False
    for o in _present_others(state, c):
        r = c.rel.get(o.id)
        if r is not None and r.affinity >= n:
            return True
    return False


def _p_others_here(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, n = _args(arg, 2, "others_here")
    c = _char(state, ctx, a)
    if not _onmap(c) or c is None:
        return False
    return len(_present_others(state, c)) >= n


def _p_flag(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    if isinstance(arg, list):
        name, value = _args(arg, 2, "flag")
        return state.flags.get(flag_key(name, ctx)) == value
    if not isinstance(arg, str):
        raise PredicateError(f"flag: bad arg {arg!r}")
    return bool(state.flags.get(flag_key(arg, ctx)))


def _p_status(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, kind = _args(arg, 2, "status")
    c = _char(state, ctx, a)
    return c is not None and has_status(c, kind)


def _p_alive(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    c = _char(state, ctx, arg)
    return c is not None and is_alive(c)


def _p_role(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, role = _args(arg, 2, "role")
    c = _char(state, ctx, a)
    return c is not None and role in c.roles


def _p_art(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, art, n = _args(arg, 3, "art")
    c = _char(state, ctx, a)
    return c is not None and c.arts.get(art, 0) >= n


def _p_stat(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, fieldname, op, n = _args(arg, 4, "stat")
    c = _char(state, ctx, a)
    if c is None:
        return False
    return compare(stat_value(state, defs, c, fieldname), op, n)


def _p_time_ge(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    return state.tick >= int(arg)


def _p_time_le(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    return state.tick <= int(arg)


def _p_step(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    a, step_id, st = _args(arg, 3, "step")
    c = _char(state, ctx, a)
    if c is None or c.agenda is None:
        return False
    for s in c.agenda.steps:
        if s.id == step_id:
            return s.state == st
    return False


def _p_situation(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    return any(s.kind == arg for s in state.situations.values())


def _p_request(state: WorldState, defs: Defs, arg: Any, ctx: Ctx | None) -> bool:
    rid, status = _args(arg, 2, "request")
    r = state.requests.get(rid)
    return r is not None and r.status == status


_PREDICATES = {
    "all": _p_all,
    "any": _p_any,
    "not": _p_not,
    "at": _p_at,
    "same_loc": _p_same_loc,
    "onstage": _p_onstage,
    "has": _p_has,
    "item_at": _p_item_at,
    "knows": _p_knows,
    "rel": _p_rel,
    "friend_here": _p_friend_here,
    "others_here": _p_others_here,
    "flag": _p_flag,
    "status": _p_status,
    "alive": _p_alive,
    "role": _p_role,
    "art": _p_art,
    "stat": _p_stat,
    "time_ge": _p_time_ge,
    "time_le": _p_time_le,
    "step": _p_step,
    "situation": _p_situation,
    "request": _p_request,
}

assert set(_PREDICATES) == set(PREDICATE_SHAPES)
