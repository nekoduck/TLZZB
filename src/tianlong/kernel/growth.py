"""成长（方案 6.12）：武功高低、内力档、研读与练习的进度。

识字也按同样的办法下功夫：本事 id 写 ``literacy``，每 24 个时辰升一档（6.12「别的长进」）。
"""

from __future__ import annotations

from pydantic import BaseModel

from tianlong.kernel import predicates
from tianlong.kernel.defs import ArtDef, Defs
from tianlong.kernel.state import TICKS_PER_HOUR, Character, Ctx, WorldState

LITERACY = "literacy"
LITERACY_HOURS = 24  # 有人教，24 个时辰升一档
LITERACY_MAX = 3
TEACHER_FACTOR = 1.5


def _c(state: WorldState, c: Character | str) -> Character:
    return state.chars[c] if isinstance(c, str) else c


def neili_band(neili: int) -> int:
    """内力档：不足 20 为 0，20 起为 1，40 起为 2，60 起为 3，80 起为 4。"""
    if neili >= 80:
        return 4
    if neili >= 60:
        return 3
    if neili >= 40:
        return 2
    if neili >= 20:
        return 1
    return 0


def art_level(c: Character, art: str) -> int:
    return c.arts.get(art, 0)


def learned_stages(defs: Defs, c: Character, art: str) -> list:
    a = defs.arts.get(art)
    if a is None:
        return []
    return a.stages[: art_level(c, art)]


def has_combat_art(defs: Defs, c: Character) -> bool:
    return any(
        lvl >= 1 and (a := defs.arts.get(aid)) is not None and a.kind == "combat"
        for aid, lvl in c.arts.items()
    )


def combat_power(defs: Defs, c: Character) -> int:
    best = 0
    for aid, lvl in c.arts.items():
        a = defs.arts.get(aid)
        if a is None or a.kind != "combat" or lvl < 1:
            continue
        for st in a.stages[:lvl]:
            best = max(best, st.power)
    return best


def power(state: WorldState, defs: Defs, c: Character | str) -> int:
    """power = max(base_power, 所学 combat 本事各层里最高的 power, 内力档)；
    一门 combat 本事都不会的人，内力档减 1。"""
    ch = _c(state, c)
    band = neili_band(ch.neili)
    if not has_combat_art(defs, ch):
        band = max(0, band - 1)
    return max(ch.base_power, combat_power(defs, ch), band)


def _effects(defs: Defs, c: Character) -> list[dict]:
    out: list[dict] = []
    for aid, lvl in c.arts.items():
        a = defs.arts.get(aid)
        if a is None or lvl < 1:
            continue
        stages = a.stages[:lvl]
        if stages:
            out.append(stages[-1].effects)
    return out


def flee_bonus(defs: Defs, c: Character) -> int:
    """轻功加值：所学各门本事当前一层的 flee 取最大。"""
    return max((int(e.get("flee", 0)) for e in _effects(defs, c)), default=0)


def has_evade(defs: Defs, c: Character) -> bool:
    return any(bool(e.get("evade")) for e in _effects(defs, c))


def absorb_amount(defs: Defs, c: Character) -> int:
    return max((int(e.get("absorb", 0)) for e in _effects(defs, c)), default=0)


def rate_per_hour(c: Character, *, teacher: bool) -> float:
    """每个时辰记 8 ×（1 + 0.25 × 悟性）刻；有人教再乘 1.5。"""
    r = TICKS_PER_HOUR * (1 + 0.25 * c.aptitude)
    if teacher:
        r *= TEACHER_FACTOR
    return r


def progress_for(c: Character, hours: int, *, teacher: bool) -> int:
    return int(round(rate_per_hour(c, teacher=teacher) * hours))


def stage_need(defs: Defs, art: str, level: int) -> int | None:
    """从 level 升到 level+1 需要的进度（刻）。已到顶返回 None。"""
    if art == LITERACY:
        return LITERACY_HOURS * TICKS_PER_HOUR if level < LITERACY_MAX else None
    a = defs.arts.get(art)
    if a is None or level >= len(a.stages):
        return None
    return a.stages[level].hours * TICKS_PER_HOUR


def requires_ok(state: WorldState, defs: Defs, c: Character, art: str) -> bool:
    if art == LITERACY:
        return True
    a = defs.arts.get(art)
    if a is None:
        return False
    return predicates.holds(state, defs, a.requires, Ctx(self_id=c.id))


def _first_failing(state: WorldState, defs: Defs, pred: object, ctx: Ctx) -> object:
    if isinstance(pred, dict) and len(pred) == 1 and "all" in pred:
        for p in pred["all"]:
            if not predicates.holds(state, defs, p, ctx):
                return _first_failing(state, defs, p, ctx)
    return pred


def blocked_reason(state: WorldState, defs: Defs, c: Character, art: str) -> str:
    """requires 不成立时，叙述里写明卡在哪里（6.12）。"""
    a: ArtDef | None = defs.arts.get(art)
    if a is None:
        return "不知从何学起"
    p = _first_failing(state, defs, a.requires, Ctx(self_id=c.id))
    if isinstance(p, dict) and len(p) == 1:
        key, arg = next(iter(p.items()))
        if key == "stat" and isinstance(arg, list) and len(arg) == 4:
            fieldname, op = arg[1], arg[2]
            if fieldname == "literacy":
                return "满纸的字倒有一半不认得"
            if fieldname == "neili" and op in ("<=", "<"):
                return "体内已有的一股内力与它格格不入"
            if fieldname == "neili":
                return "内力太浅，练不下去"
            return "底子不够，看不明白"
        if key == "art" and isinstance(arg, list) and len(arg) == 3:
            return f"里头讲的东西要先懂得⟦{arg[1]}⟧才看得明白"
        if key == "role":
            return "这是门内的功夫，外人学不得"
    return "总是不得其门而入"


class TrainResult(BaseModel):
    art: str
    added: int = 0
    level_before: int = 0
    level_after: int = 0
    blocked: str | None = None


def apply_progress(
    defs: Defs, c: Character, art: str, ticks: int, *, max_level: int | None = None
) -> TrainResult:
    """给 art 记 ticks 刻进度；记满一层就升一层（余数留给下一层）。直接改人物。"""
    before = c.literacy if art == LITERACY else art_level(c, art)
    level = before
    prog = c.art_progress.get(art, 0) + max(0, ticks)
    while True:
        if max_level is not None and level >= max_level:
            prog = 0
            break
        need = stage_need(defs, art, level)
        if need is None:
            prog = 0
            break
        if prog < need:
            break
        prog -= need
        level += 1
        if art != LITERACY:
            a = defs.arts[art]
            gain = int(a.stages[level - 1].effects.get("neili", 0))
            if gain:
                c.neili = max(0, min(100, c.neili + gain))
    if art == LITERACY:
        c.literacy = level
    else:
        c.arts[art] = level
    if prog:
        c.art_progress[art] = prog
    else:
        c.art_progress.pop(art, None)
    return TrainResult(art=art, added=max(0, ticks), level_before=before, level_after=level)


def study_amount(
    state: WorldState, defs: Defs, c: Character, art: str, hours: int, *, teacher: bool
) -> TrainResult:
    """研读或听讲：只能下到第一层；requires 不成立时没有进度。"""
    lvl = c.literacy if art == LITERACY else art_level(c, art)
    if art != LITERACY and lvl >= 1:
        return TrainResult(
            art=art, level_before=lvl, level_after=lvl, blocked="书上的已经懂了，往下要靠自己练"
        )
    if not requires_ok(state, defs, c, art):
        return TrainResult(
            art=art, level_before=lvl, level_after=lvl, blocked=blocked_reason(state, defs, c, art)
        )
    return TrainResult(
        art=art, added=progress_for(c, hours, teacher=teacher), level_before=lvl, level_after=lvl
    )


def practice_amount(state: WorldState, defs: Defs, c: Character, art: str, hours: int) -> TrainResult:
    lvl = art_level(c, art)
    if lvl < 1:
        return TrainResult(art=art, blocked="还没入门，无从练起")
    if stage_need(defs, art, lvl) is None:
        return TrainResult(art=art, level_before=lvl, level_after=lvl, blocked="这门本事已经练到头了")
    return TrainResult(
        art=art, added=progress_for(c, hours, teacher=False), level_before=lvl, level_after=lvl
    )


def can_learn_from_item(defs: Defs, item_teaches: list[str], art: str) -> bool:
    a = defs.arts.get(art)
    return a is not None and art in item_teaches


def can_teach(state: WorldState, defs: Defs, teacher: Character, student: Character, art: str) -> bool:
    """师父得比徒弟高一层以上；teacher_roles 写了的，师父得有其中一个身份。"""
    if art == LITERACY:
        return teacher.literacy > student.literacy and student.literacy < LITERACY_MAX
    a = defs.arts.get(art)
    if a is None or "teacher" not in a.sources:
        return False
    if a.teacher_roles and not set(a.teacher_roles) & set(teacher.roles):
        return False
    tl, sl = art_level(teacher, art), art_level(student, art)
    return tl >= 1 and tl > sl and stage_need(defs, art, sl) is not None
