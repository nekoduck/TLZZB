"""危险与死亡（方案 6.10）：danger 0 到 3；敌意的定义；致命动作；对玩家的缓冲。"""

from __future__ import annotations

from tianlong.kernel import growth, perception, predicates
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Ctx, Step, WorldState, has_status

HOSTILE_AFFINITY = -30
HOSTILE_VERBS = ("attack", "restrain")
LETHAL_CHANCE = 50  # 判定失败即坠落、成功率低于它的出口算致命
LETHAL_POWER_GAP = 2


def current_step(c: Character) -> Step | None:
    ag = c.agenda
    if ag is None or ag.status != "active" or ag.cursor >= len(ag.steps):
        return None
    st = ag.steps[ag.cursor]
    return st if st.state == "pending" else None


def step_target(state: WorldState, c: Character, st: Step) -> str | None:
    t = st.do.get("target")
    if t is None:
        return None
    return predicates.resolve_id(t, state, Ctx(self_id=c.id))


def intends(state: WorldState, a: Character, b: str, verbs: tuple[str, ...] = HOSTILE_VERBS) -> bool:
    """a 眼下的打算里有冲着 b 来的捉拿、攻击一类的事。"""
    st = current_step(a)
    if st is None or st.do.get("verb") not in verbs:
        return False
    return step_target(state, a, st) == b


def intends_kill(state: WorldState, a: Character, b: str) -> bool:
    st = current_step(a)
    return (
        st is not None
        and st.do.get("verb") == "attack"
        and st.do.get("intent") == "kill"
        and step_target(state, a, st) == b
    )


def is_hostile(state: WorldState, a: Character, b: str) -> bool:
    """a 对 b 有敌意：好感低于 -30，或眼下的打算里有捉拿、攻击 b 这一类事。"""
    if a.id == b:
        return False
    r = a.rel.get(b)
    if r is not None and r.affinity < HOSTILE_AFFINITY:
        return True
    return intends(state, a, b)


def hostiles_toward(state: WorldState, cid: str) -> list[Character]:
    c = state.chars[cid]
    return [
        o
        for o in perception.present(state, c.loc, exclude=cid)
        if perception.conscious(o) and is_hostile(state, o, cid)
    ]


def any_hostility_at(state: WorldState, loc: str) -> bool:
    people = [c for c in perception.present(state, loc) if perception.conscious(c)]
    ids = [c.id for c in people]
    return any(is_hostile(state, a, b) for a in people for b in ids)


def fighting_at(state: WorldState, loc: str) -> bool:
    """身边正在动手：这一刻（或上一刻）这里有过 attack / restrain。"""
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - 1:
            break
        if ev.loc == loc and ev.verb in ("attack", "restrain") and ev.outcome != "blocked":
            return True
    return False


def danger(state: WorldState, defs: Defs) -> int:
    p = state.chars.get(state.player)
    if p is None or not perception.on_map(p):
        return 0
    toward = hostiles_toward(state, p.id)
    if any(intends_kill(state, h, p.id) for h in toward):
        return 3
    mine = growth.power(state, defs, p)
    if any(growth.power(state, defs, h) >= mine + 2 for h in toward) or fighting_at(state, p.loc):
        return 2
    if toward or any_hostility_at(state, p.loc):
        return 1
    return 0


def restrained(c: Character) -> bool:
    return any(has_status(c, k) for k in ("bound", "unconscious", "asleep"))


def attack_lethal(state: WorldState, defs: Defs, actor: Character, target: Character) -> bool:
    """打一个高出自己 2 以上、又没被制住的人。"""
    if restrained(target):
        return False
    return growth.power(state, defs, target) >= growth.power(state, defs, actor) + LETHAL_POWER_GAP


def item_lethal(state: WorldState, item_id: str) -> bool:
    it = state.items.get(item_id)
    return it is not None and (bool(it.props.get("lethal")) or "poison" in it.tags)


def needs_warning(state: WorldState, signature: str, lethal: bool) -> bool:
    """玩家第一次尝试标为 lethal 的动作：先警示，局面不变（2.4）。"""
    return lethal and signature not in state.warned


def mark_warned(state: WorldState, key: str) -> None:
    if key not in state.warned:
        state.warned.append(key)


def buffered(state: WorldState, key: str) -> bool:
    """同一来源对玩家的第一次致命结果要降级（6.10）。True：这一次要降级（尚未记下）。"""
    return f"kill:{key}" not in state.warned
