"""判定（方案 6.4）。d100 不大于成功率即成功；成功率封顶 95，保底 5。

杀招落下前的侥幸（6.5）只看气运，成功率 2 × luck，不设保底。
"""

from __future__ import annotations

from pydantic import BaseModel

from tianlong.kernel import growth, rng
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, WorldState, has_status, injury_level

CAP = 95
FLOOR = 5
FLEE_HARD_CAP = 10  # 追者高出 4 以上且没人牵制时


class CheckResult(BaseModel):
    kind: str
    chance: int
    roll: int
    ok: bool


def clamp(p: int, *, floor: int = FLOOR, cap: int = CAP) -> int:
    return max(floor, min(cap, p))


def condition_penalty(c: Character, *, injury: bool = True, fatigue: bool = True) -> int:
    """伤势每级 -15，疲惫 -10，饥饿 -5（6.9）。"""
    p = 0
    if injury:
        p += 15 * injury_level(c)
    if fatigue and has_status(c, "exhausted"):
        p += 10
    if has_status(c, "hungry"):
        p += 5
    return p


def is_night(state: WorldState) -> bool:
    from tianlong.kernel import timeflow

    return timeflow.time_of_day(state.tick) == "night"


def flee_chance(
    state: WorldState,
    defs: Defs,
    who: Character,
    pursuers: list[Character],
    *,
    unguarded: bool = False,
) -> int:
    """50 + terrain + 15（对方没防备或被牵制）- 12 ×（追者最高 power - 自己 - 1）- 15 × 伤 - 10 疲惫 + 轻功。"""
    loc = state.locations.get(who.loc)
    terrain = loc.terrain if loc else 0
    mine = growth.power(state, defs, who)
    top = max((growth.power(state, defs, p) for p in pursuers), default=None)
    impeded = any(has_status(p, "impeded") for p in pursuers)
    p = 50 + terrain
    if unguarded or impeded or not pursuers:
        p += 15
    if top is not None:
        p -= 12 * (top - mine - 1)
    p -= condition_penalty(who)
    p += growth.flee_bonus(defs, who)
    p += sum(int(state.items[i].props.get("flee_mod", 0)) for i in who.inventory if i in state.items)
    p = clamp(p)
    if top is not None and top - mine >= 4 and not impeded:
        p = min(p, FLEE_HARD_CAP)
    return p


def hide_chance(state: WorldState, defs: Defs, who: Character, cover: int, searchers: int, seen: bool) -> int:
    """45 + cover + 15（夜里）- 10 × 搜寻的人数 - 20（刚被看见）。"""
    p = 45 + cover
    if is_night(state):
        p += 15
    p -= 10 * searchers
    if seen:
        p -= 20
    p -= condition_penalty(who)
    return clamp(p)


def steal_chance(state: WorldState, defs: Defs, who: Character, target: Character) -> int:
    """30 + 25（对方分心、睡着或被缚）+ 10（对方对你 trust 不低于 50）- 10 × 对方 power。"""
    p = 30
    if any(has_status(target, k) for k in ("asleep", "bound", "unconscious", "impeded", "distracted")):
        p += 25
    r = target.rel.get(who.id)
    if r is not None and r.trust >= 50:
        p += 10
    p -= 10 * growth.power(state, defs, target)
    p -= condition_penalty(who)
    return clamp(p)


def has_rope(state: WorldState, who: Character) -> bool:
    return any("rope" in state.items[i].tags for i in who.inventory if i in state.items)


def climb_chance(state: WorldState, defs: Defs, who: Character) -> int:
    """攀爬、缒绳下崖：30 + 45（有绳）+ 10 × power - 15 × 伤 - 10（夜里或下雨）。"""
    p = 30
    if has_rope(state, who):
        p += 45
    p += 10 * growth.power(state, defs, who)
    p -= condition_penalty(who)
    if is_night(state) or state.flags.get("rain"):
        p -= 10
    return clamp(p)


def fall_survive_chance(state: WorldState, defs: Defs, who: Character, loc_id: str | None) -> int:
    """坠落活下来的机会：35 + 2 × luck + 20（崖壁可挂）。"""
    loc = state.locations.get(loc_id) if loc_id else None
    p = 35 + 2 * who.luck
    if loc is not None and loc.handholds:
        p += 20
    return clamp(p)


def death_save_chance(who: Character) -> int:
    """取命一击落下之前的侥幸：2 × luck，不设保底。"""
    return max(0, min(CAP, 2 * who.luck))


LUCKY_FLAG = "__lucky__"


def roll_against(state: WorldState, kind: str, chance: int, *, adverse: bool = False) -> CheckResult:
    """掷一次。旗标 __lucky__ 为真时判定一律成功（方案 11.3 的 --lucky 基线跑），仍消耗一次随机数。

    ``adverse``：这是一次「成了反而坏事」的判定（看押的人趁隙逃脱、暗处的人被发现……）。
    ``__lucky__`` 下它一律不成（K11）；平时与寻常判定一样掷。
    """
    r = rng.roll(state)
    if state.flags.get(LUCKY_FLAG):
        return CheckResult(kind=kind, chance=chance, roll=r, ok=not adverse)
    return CheckResult(kind=kind, chance=chance, roll=r, ok=r <= chance)
