"""情绪与关系（方案 5.5）：按事件性质改写；情绪每个时辰衰减一成；给模型看的档位文字。"""

from __future__ import annotations

from tianlong.kernel import growth
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_HOUR, Character, Event, Relation, WorldState

MOODS = ("anger", "fear", "grief", "joy", "suspicion")
REL_RANGES: dict[str, tuple[int, int]] = {
    "trust": (-100, 100),
    "affinity": (-100, 100),
    "fear": (0, 100),
    "regard": (0, 10),
}
FRIEND = 30  # 朋友：好感不低于 30


def rel(c: Character, other: str) -> Relation:
    r = c.rel.get(other)
    if r is None:
        r = Relation()
        c.rel[other] = r
    return r


def adj_rel(state: WorldState, a: str, b: str, fieldname: str, delta: int) -> None:
    ca = state.chars.get(a)
    if ca is None or a == b:
        return
    r = rel(ca, b)
    lo, hi = REL_RANGES[fieldname]
    setattr(r, fieldname, max(lo, min(hi, getattr(r, fieldname) + delta)))


def set_rel(state: WorldState, a: str, b: str, fieldname: str, value: int) -> None:
    ca = state.chars.get(a)
    if ca is None or a == b:
        return
    lo, hi = REL_RANGES[fieldname]
    setattr(rel(ca, b), fieldname, max(lo, min(hi, value)))


def adj_mood(c: Character, kind: str, delta: int) -> None:
    v = max(0, min(100, c.mood.get(kind, 0) + delta))
    if v:
        c.mood[kind] = v
    else:
        c.mood.pop(kind, None)


def decay(state: WorldState, hours: int = 1) -> None:
    """情绪每过一个时辰衰减一成。"""
    for _ in range(max(0, hours)):
        for c in state.chars.values():
            if not c.mood:
                continue
            for k in list(c.mood):
                v = int(c.mood[k] * 0.9)
                if v:
                    c.mood[k] = v
                else:
                    del c.mood[k]


def on_event(state: WorldState, defs: Defs, event: Event, kinds: list[dict] | None = None) -> None:
    """按 5.5 的表改写当事人与旁观者的情绪与关系。"""
    kinds = event.kinds if kinds is None else kinds
    seers = [w for w, how in event.witnesses.items() if how == "saw"]
    for k in kinds:
        kind = k["k"]
        who, by = k.get("who"), k.get("by")
        cw = state.chars.get(who) if who else None
        if kind == "insult" and cw is not None and who in event.witnesses:
            adj_mood(cw, "anger", 20)
            if by:
                adj_rel(state, who, by, "affinity", -10)
        elif kind == "threaten" and cw is not None and by and who in event.witnesses:
            seen = rel(cw, by).regard
            if seen > growth.power(state, defs, cw):
                adj_mood(cw, "fear", 20)
                adj_rel(state, who, by, "fear", 20)
            else:
                adj_mood(cw, "anger", 15)
        elif kind in ("helped", "gift") and cw is not None and by:
            adj_rel(state, who, by, "trust", 10)
            adj_rel(state, who, by, "affinity", 10)
        elif kind == "rescue" and cw is not None and by:
            adj_rel(state, who, by, "trust", 30)
            adj_rel(state, who, by, "affinity", 25)
            cw.owes[by] = cw.owes.get(by, 0) + 1
        elif kind == "taught" and cw is not None and by:
            adj_rel(state, who, by, "trust", 15)
            adj_rel(state, who, by, "affinity", 15)
        elif kind == "deceived" and cw is not None:
            adj_mood(cw, "anger", 20)
            adj_mood(cw, "suspicion", 20)
            if by:
                adj_rel(state, who, by, "trust", -25)
        elif kind == "promise_kept" and cw is not None and by:
            adj_rel(state, who, by, "trust", 15)
        elif kind == "promise_broken" and cw is not None and by:
            adj_rel(state, who, by, "trust", -20)
        elif kind == "death":
            for w in seers:
                if w != who and (cw2 := state.chars.get(w)) is not None:
                    adj_mood(cw2, "fear", 25)
        elif kind == "show_power" and who:
            p = growth.power(state, defs, state.chars[who])
            for w in seers:
                cw2 = state.chars.get(w)
                if w == who or cw2 is None:
                    continue
                set_rel(state, w, who, "regard", p)
                if p >= growth.power(state, defs, cw2) + 2:
                    adj_rel(state, w, who, "fear", 15)
        elif kind == "comfort" and cw is not None and by:
            hour = state.tick // TICKS_PER_HOUR
            if cw.comforted.get(by) != hour:
                cw.comforted[by] = hour
                adj_mood(cw, "fear", -10)
                adj_rel(state, who, by, "affinity", 5)
        elif kind == "nag" and cw is not None:
            adj_mood(cw, "anger", 10)
        elif kind == "caught_stealing" and cw is not None and by:
            adj_rel(state, who, by, "trust", -30)
            adj_mood(cw, "anger", 25)
        elif kind == "spotted":
            for w in seers:
                if w != who and (cw2 := state.chars.get(w)) is not None:
                    adj_mood(cw2, "suspicion", 20)
        elif kind == "suspicion" and cw is not None:
            adj_mood(cw, "suspicion", int(k.get("n", 15)))
        # 朋友遇险：在场看见的人里，对受害者好感不低于 30 的，fear +20
        if kind in ("harmed", "captured", "poisoned", "threaten") and who:
            for w in seers:
                if w == who:
                    continue
                cw2 = state.chars.get(w)
                r = cw2.rel.get(who) if cw2 else None
                if cw2 is not None and r is not None and r.affinity >= FRIEND:
                    adj_mood(cw2, "fear", 20)


# ---- 档位文字（给模型看的是档位，不是数字） ----


def trust_text(v: int) -> str:
    if v < -40:
        return "信不过"
    if v < 10:
        return "不熟"
    if v < 50:
        return "有几分信"
    return "信得过"


def affinity_text(v: int) -> str:
    if v <= -30:
        return "厌恶"
    if v < -10:
        return "有些嫌弃"
    if v < 10:
        return "谈不上好恶"
    if v < 30:
        return "有几分好感"
    return "很有好感"


def fear_text(v: int) -> str:
    if v < 20:
        return "不怕"
    if v < 50:
        return "有些怕"
    if v < 80:
        return "很怕"
    return "怕极了"


def regard_text(power: int) -> str:
    if power <= 0:
        return "不会武功"
    if power <= 2:
        return "有些身手"
    if power <= 4:
        return "是个好手"
    return "深不可测"


def relative_regard_text(regard: int, own_power: int) -> str:
    """「身手比你强」这类相对的说法（J2 的【在场】一节用）。"""
    if regard >= own_power + 2:
        return "身手远比你强"
    if regard > own_power:
        return "身手比你强"
    if regard == own_power:
        return "身手与你相当"
    return "身手不如你"


_MOOD_WORDS: dict[str, tuple[str, str, str]] = {
    "anger": ("有些恼", "很恼火", "怒不可遏"),
    "fear": ("有些不安", "害怕", "惊恐万状"),
    "grief": ("有些难过", "悲伤", "悲痛欲绝"),
    "joy": ("心情不错", "高兴", "欣喜若狂"),
    "suspicion": ("有些起疑", "满腹狐疑", "疑心极重"),
}


def mood_text(c: Character) -> str:
    parts: list[str] = []
    for k in MOODS:
        v = c.mood.get(k, 0)
        if v < 20:
            continue
        words = _MOOD_WORDS[k]
        parts.append(words[0] if v < 50 else words[1] if v < 80 else words[2])
    return "、".join(parts) if parts else "心境平和"


def rel_text(c: Character, other: str) -> str:
    r = c.rel.get(other) or Relation()
    return f"{trust_text(r.trust)}；{affinity_text(r.affinity)}；{fear_text(r.fear)}"


def band(defs: Defs, state: WorldState, a: str, b: str) -> str:
    """a 对 b 的关系档（6.6）：按 trust 与 affinity 的平均值；同门、师徒、主仆至少「相熟」。"""
    ca, cb = state.chars.get(a), state.chars.get(b)
    if ca is None or cb is None:
        return "stranger"
    r = ca.rel.get(b) or Relation()
    avg = (r.trust + r.affinity) / 2
    bands = defs.world.request.bands
    hostile_hi = bands.get("hostile", [-100, -30])[1]
    stranger_hi = bands.get("stranger", [-30, 20])[1]
    familiar_hi = bands.get("familiar", [20, 60])[1]
    if avg < hostile_hi:
        b_ = "hostile"
    elif avg < stranger_hi:
        b_ = "stranger"
    elif avg < familiar_hi:
        b_ = "familiar"
    else:
        b_ = "trusted"
    if b_ in ("hostile", "stranger") and close_roles(defs, ca, cb, state):
        b_ = "familiar"
    return b_


def sect_roles(defs: Defs, state: WorldState | None = None) -> set[str] | None:
    """算作门派、人家的身份（K3）。world.yaml 写了 ``request.sect_roles`` 就以它为准；
    没写时取占着地盘的身份（各地点的 ``owner``）。两者都没有（没给 state）返回 None：任何共同身份都算。"""
    listed = defs.world.request.sect_roles
    if listed is not None:
        return set(listed)
    if state is None:
        return None
    return {loc.owner for loc in state.locations.values() if loc.owner}


def same_sect(defs: Defs, a: Character, b: Character, state: WorldState | None = None) -> bool:
    """同门、同一家：两人有一个共同的、算作门派或人家的身份。「同是来客」不算。"""
    shared = set(a.roles) & set(b.roles)
    if not shared:
        return False
    sects = sect_roles(defs, state)
    return True if sects is None else bool(shared & sects)


def close_roles(defs: Defs, a: Character, b: Character, state: WorldState | None = None) -> bool:
    """同门（``same_sect``）、师徒与主仆（一方听命于另一方）。"""
    rules = defs.world.request.close_roles
    if "same_sect" in rules and same_sect(defs, a, b, state):
        return True
    if ("master_disciple" in rules or "master_servant" in rules) and (
        a.superior == b.id or b.superior == a.id
    ):
        return True
    return False
