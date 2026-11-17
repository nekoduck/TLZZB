"""机会牌（方案 8.5）：``content/opportunities.yaml`` 的读法、条件与出牌。

牌只带来人、物和消息，效果都是普通的效果（5.3），经 ``resolve.run_effects`` 执行（带回滚）。
格式（D-011：由本模块定）::

    <牌 id>:
      name: 牌名                    # 日志与 /debug 用
      text: 一句说明                # J6 选牌时给 Jev 看
      pre: 谓词                     # self 指玩家；不引用角色位（角色位的条件写在 pick 的 where 里）
      roles:                        # 可选；按写的顺序绑定，可以引用前面已绑定的角色位
        名字: <id>                  # 写死一个 id（玩家写 player）
        名字: {pick: {where, near, at, optional}}
            # 挑一个清醒的活人：默认在玩家所在地点；near 为真时在相邻地点；at 写人或地点时在那里。
            # where 里 $it 指候选人。optional 为真时绑不上也行，引用它的效果整条略去。
        名字: {fact: {where}}       # 挑一条玩家还不知道、可以传开的事实（新近的事件在前）；$it 指候选事实；
                                    # 文字里的 {名字} 换成事实原文，$名字 换成事实 id
        名字: {loc_of: <人或地点>}  # 绑定那人此刻所在的地点
      effects: [效果]               # self 指玩家，$名字 指角色位；字符串里的 $名字（如 told:$teller）也会替换
      variants: [{pre, roles, effects, weight}]   # 可选：出牌时在成立的几种里按 weight 取一种（用 kernel.rng）
      cooldown: 96                  # 出过以后多少刻内不再出
      once: false                   # 一局只出一次
      weight: 1                     # 排序与规则模式下的取舍权重
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from tianlong.kernel import perception, predicates, resolve, rng
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, Event, WorldState
from tianlong.storyteller import common

ROLE_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
FACT_SKIP_TAGS = ("secret",)


@dataclass
class Bound:
    """一张此刻条件成立的牌：绑好的角色位，以及成立的几种变体（下标与绑定）。"""

    card_id: str
    card: dict[str, Any]
    roles: dict[str, str]
    variants: list[tuple[int, dict[str, str]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return str(self.card.get("text") or self.card.get("name") or self.card_id)

    @property
    def weight(self) -> float:
        return float(self.card.get("weight", 1) or 1)


def cards(defs: Defs) -> dict[str, dict[str, Any]]:
    raw = defs.opportunities
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


# ---------------------------------------------------------------- 绑定


def _holds(state: WorldState, defs: Defs, pred: Any, roles: dict[str, str]) -> bool:
    try:
        return predicates.holds(state, defs, pred, Ctx(self_id=state.player, roles=dict(roles)))
    except predicates.KernelError:
        return False


def _near_locs(state: WorldState) -> list[str]:
    p = state.chars[state.player]
    loc = state.locations.get(p.loc)
    if loc is None:
        return []
    return [e.to for e in loc.exits if e.to in state.locations and not e.edge and e.to != p.loc]


def _rumor_facts(state: WorldState) -> list[str]:
    """可以传开的事实：玩家还不信的、不是秘密的；新近的事件事实在前，再是会被人主动说起的事实。"""
    p = state.chars[state.player]
    events: list[tuple[int, str]] = []
    gossip: list[str] = []
    for fid, f in state.facts.items():
        if common.believes(p, fid) or fid.startswith("exit:") or set(f.tags) & set(FACT_SKIP_TAGS):
            continue
        if "event" in f.tags and fid[1:].isdigit():
            events.append((int(fid[1:]), fid))
        elif f.gossip:
            gossip.append(fid)
    return [fid for _, fid in sorted(events, reverse=True)] + gossip


def _candidates(state: WorldState, defs: Defs, spec: Any, roles: dict[str, str]) -> list[str] | None:
    """一个角色位的候选（按先后）。None 表示写法不认得。"""
    ctx = Ctx(self_id=state.player, roles=dict(roles))
    if isinstance(spec, str):
        v = predicates.resolve_id(spec, state, ctx)
        ok = isinstance(v, str) and (v in state.chars or v in state.items or v in state.locations or v in state.facts)
        return [v] if ok else []
    if not isinstance(spec, dict):
        return None
    if "loc_of" in spec:
        v = predicates.loc_of(spec["loc_of"], state, ctx)
        return [v] if isinstance(v, str) and v in state.locations else []
    if "pick" in spec:
        pk = spec["pick"] or {}
        if pk.get("at") is not None:
            here = predicates.loc_of(pk["at"], state, ctx)
            locs = [here] if here else []
        elif pk.get("near"):
            locs = _near_locs(state)
        else:
            locs = [state.chars[state.player].loc]
        out: list[str] = []
        bound = set(roles.values())
        for loc in locs:
            for c in perception.present(state, loc):
                if c.id == state.player or c.id in bound or not perception.conscious(c):
                    continue
                if _holds(state, defs, pk.get("where"), {**roles, "it": c.id}):
                    out.append(c.id)
        return out
    if "fact" in spec:
        fk = spec["fact"] or {}
        return [fid for fid in _rumor_facts(state) if _holds(state, defs, fk.get("where"), {**roles, "it": fid})]
    return None


def _optional(spec: Any) -> bool:
    return isinstance(spec, dict) and isinstance(spec.get("pick"), dict) and bool(spec["pick"].get("optional"))


def bind(state: WorldState, defs: Defs, specs: dict[str, Any] | None, roles: dict[str, str]) -> dict[str, str] | None:
    """按写的顺序绑定角色位（回溯：前面的人选不同，后面的才绑得上时会换人）。绑不上返回 None。"""
    items = list((specs or {}).items())

    def go(i: int, acc: dict[str, str]) -> dict[str, str] | None:
        if i >= len(items):
            return acc
        name, spec = items[i]
        cands = _candidates(state, defs, spec, acc)
        if cands is None:
            return None
        for v in cands:
            got = go(i + 1, {**acc, name: v})
            if got is not None:
                return got
        if _optional(spec):
            return go(i + 1, acc)
        return None

    return go(0, dict(roles))


# ---------------------------------------------------------------- 条件


def _cooling(state: WorldState, cid: str, card: dict[str, Any]) -> bool:
    if card.get("once") and cid in common.teller(state, "cards_used", []):
        return True
    last = common.teller(state, "card_last", {}).get(cid)
    cd = int(card.get("cooldown", 0) or 0)
    return last is not None and state.tick < int(last) + cd


def check(state: WorldState, defs: Defs, cid: str, card: dict[str, Any]) -> Bound | None:
    """一张牌此刻成立吗：不在冷却、pre 成立、角色位绑得上、（有变体时）至少一种变体成立。"""
    if _cooling(state, cid, card):
        return None
    if not _holds(state, defs, card.get("pre"), {}):
        return None
    roles = bind(state, defs, card.get("roles"), {})
    if roles is None:
        return None
    b = Bound(cid, card, roles)
    variants = card.get("variants") or []
    for i, v in enumerate(variants):
        if not isinstance(v, dict) or not _holds(state, defs, v.get("pre"), roles):
            continue
        vroles = bind(state, defs, v.get("roles"), roles)
        if vroles is not None:
            b.variants.append((i, vroles))
    if variants and not b.variants:
        return None
    return b


def eligible(state: WorldState, defs: Defs) -> list[Bound]:
    """此刻条件成立的牌，按 weight 从高到低（同权重按文件里的顺序）。不改状态。"""
    p = state.chars.get(state.player)
    if p is None or not perception.on_map(p):
        return []
    out = [b for cid, card in cards(defs).items() if (b := check(state, defs, cid, card)) is not None]
    return sorted(out, key=lambda b: -b.weight)


# ---------------------------------------------------------------- 出牌


def _unbound_optional(card_roles: dict[str, Any], roles: dict[str, str]) -> set[str]:
    return {n for n, spec in card_roles.items() if _optional(spec) and n not in roles}


def _mentions(eff: Any, names: set[str]) -> bool:
    if not names:
        return False
    s = json.dumps(eff, ensure_ascii=False)
    return any(f"${n}" in s or "{" + n + "}" in s for n in names)


def _subst(value: Any, state: WorldState, roles: dict[str, str], fact_roles: set[str]) -> Any:
    """把字符串里的 {事实角色位} 换成事实原文，把夹在字符串中间的 $角色位 换成 id。"""
    if isinstance(value, dict):
        return {k: _subst(v, state, roles, fact_roles) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, state, roles, fact_roles) for v in value]
    if not isinstance(value, str):
        return value
    out = value
    for name in fact_roles:
        fid = roles.get(name)
        if fid and "{" + name + "}" in out:
            out = out.replace("{" + name + "}", state.facts[fid].text)
    if ROLE_REF.fullmatch(out):
        return out  # 整个参数就是 $角色位：交给内核按上下文解析
    return ROLE_REF.sub(lambda m: roles.get(m.group(1), m.group(0)), out)


def _effects_for(state: WorldState, card: dict[str, Any], roles: dict[str, str], extra: list[dict], specs: dict) -> list[dict]:
    effs = [e for e in list(card.get("effects") or []) + list(extra) if isinstance(e, dict)]
    skip = _unbound_optional(specs, roles)
    effs = [e for e in effs if not _mentions(e, skip)]
    fact_roles = {n for n, spec in specs.items() if isinstance(spec, dict) and "fact" in spec}
    return [_subst(e, state, roles, fact_roles) for e in effs]


def fire(state: WorldState, defs: Defs, bound: Bound) -> tuple[bool, list[Event]]:
    """出这张牌：有变体时按 weight 取一种（kernel.rng，可重放）。返回（是否生效，事件）。"""
    card = bound.card
    roles = dict(bound.roles)
    specs: dict[str, Any] = dict(card.get("roles") or {})
    extra: list[dict] = []
    if bound.variants:
        if len(bound.variants) == 1:
            idx, vroles = bound.variants[0]
        else:
            weights = {i: float((card["variants"][i] or {}).get("weight", 1) or 1) for i, _ in bound.variants}
            idx = rng.pick_weighted(state, weights)
            vroles = dict(bound.variants)[idx]
        v = card["variants"][idx]
        roles = vroles
        specs.update(v.get("roles") or {})
        extra = list(v.get("effects") or [])
        card = {**card, "effects": list(card.get("effects") or [])}
    effs = _effects_for(state, card, roles, extra, specs)
    marker = state.violations[-1] if state.violations else None
    evs = resolve.run_effects(state, defs, effs, Ctx(self_id=state.player, roles=roles), source=common.SOURCE)
    last = state.violations[-1] if state.violations else None
    failed = last is not marker and isinstance(last, str) and f"rollback effects[{common.SOURCE}]" in last
    return (not failed), evs
