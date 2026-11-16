"""状态与时间（方案 6.9）：``advance_time``；时辰与日期的文字。

1 刻是最小单位，8 刻一个时辰，96 刻一日。``tick = 0`` 是第一日子时初；辰时是第五个时辰。
``advance_time`` 逐刻推进：到点的 ``scheduled``（到点时 ``pre`` 不成立的丢弃）；状态到期（中毒到期即死，
到期前一日起出现征兆）；许诺到期未守即为失约；饥饿与疲惫；每个时辰情绪衰减一成。
饥饿与疲惫只算玩家（见 DECISIONS）。
"""

from __future__ import annotations

from tianlong.kernel import effects, events, memory, mood, predicates
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import (
    TICKS_PER_DAY,
    TICKS_PER_HOUR,
    Ctx,
    Event,
    MemoryItem,
    WorldState,
    has_status,
    is_alive,
)

HOURS = ("子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥")
DIGITS = "零一二三四五六七八九"
HUNGER_AFTER = 96
EXHAUSTED_AFTER = 64
SYMPTOM_BEFORE = TICKS_PER_DAY


def cn_num(n: int) -> str:
    """1 到 999 的中文数字。"""
    if n < 0:
        return "负" + cn_num(-n)
    if n < 10:
        return DIGITS[n]
    if n < 20:
        return "十" + (DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return DIGITS[n // 10] + "十" + (DIGITS[n % 10] if n % 10 else "")
    rest = n % 100
    head = DIGITS[n // 100] + "百"
    if rest == 0:
        return head
    if rest < 10:
        return head + "零" + DIGITS[rest]
    if rest < 20:
        return head + "一十" + (DIGITS[rest % 10] if rest % 10 else "")
    return head + cn_num(rest)


def hour_index(tick: int) -> int:
    return (tick // TICKS_PER_HOUR) % len(HOURS)


def day_of(tick: int) -> int:
    return tick // TICKS_PER_DAY + 1


def clock_text(tick: int) -> str:
    """「第一日 辰时」。"""
    return f"第{cn_num(day_of(tick))}日 {HOURS[hour_index(tick)]}时"


def time_of_day(tick: int) -> str:
    """day / dusk / night：卯时到申时为白天，酉时为黄昏，其余为夜里。"""
    h = hour_index(tick)
    if 3 <= h <= 8:
        return "day"
    if h == 9:
        return "dusk"
    return "night"


def run_due_scheduled(state: WorldState, defs: Defs) -> list[Event]:
    """执行已经到点的 ``scheduled``（到点时 ``pre`` 不成立的丢弃），返回产生的事件。

    ``advance_time`` 每一刻调用一次；world 在不推进时间的地方（开局、离场结算之后）也可以直接调用（K5）。
    """
    from tianlong.kernel import resolve

    due = [s for s in state.scheduled if int(s.get("at", 0)) <= state.tick]
    if not due:
        return []
    state.scheduled[:] = [s for s in state.scheduled if int(s.get("at", 0)) > state.tick]
    out: list[Event] = []
    for s in due:
        ctx = Ctx(self_id=s.get("self"), roles=dict(s.get("roles") or {}), situation=s.get("situation"))
        try:
            ok = predicates.holds(state, defs, s.get("pre"), ctx)
        except predicates.KernelError:
            ok = False
        if not ok:
            continue
        out += resolve.run_effects(
            state, defs, list(s.get("effects") or []), ctx, source=s.get("source") or "rule"
        )
    return out


_run_scheduled = run_due_scheduled  # 旧名，留给已经在用它的调用方


def _statuses(state: WorldState, defs: Defs) -> list[Event]:
    from tianlong.kernel import resolve

    out: list[Event] = []
    now = state.tick
    for c in list(state.chars.values()):
        if not c.statuses or not is_alive(c):
            continue
        expired = []
        for s in c.statuses:
            if s.until is None:
                continue
            if s.kind.startswith("poisoned"):
                if not s.noted and s.until - now <= SYMPTOM_BEFORE and c.onstage:
                    s.noted = True
                    out.append(
                        events.emit(
                            state,
                            defs,
                            actor=c.id,
                            verb="symptom",
                            text=f"⟦{c.id}⟧捂着肚子，额上渗出冷汗",
                            source="rule",
                        )
                    )
                if s.until <= now:
                    out += resolve.run_effects(
                        state,
                        defs,
                        [{"kill": [c.id, f"poison:{s.kind}"]}],
                        Ctx(self_id=s.source),
                        source="rule",
                    )
                    break
            elif s.until <= now:
                expired.append(s.kind)
        if expired and is_alive(c):
            c.statuses = [s for s in c.statuses if s.kind not in expired or s.until is None or s.until > now]
            if "asleep" in expired:
                c.awake_since = now
    return out


def _needs(state: WorldState) -> None:
    """饥饿与疲惫（只算玩家）。"""
    p = state.chars.get(state.player)
    if p is None or not is_alive(p) or not p.onstage:
        return
    from tianlong.kernel.state import Status

    if not has_status(p, "hungry") and state.tick - p.last_meal >= HUNGER_AFTER:
        p.statuses.append(Status(kind="hungry"))
    if (
        not has_status(p, "exhausted")
        and not has_status(p, "asleep")
        and state.tick - p.awake_since >= EXHAUSTED_AFTER
    ):
        p.statuses.append(Status(kind="exhausted"))


def _promises(state: WorldState) -> None:
    for p in state.promises.values():
        if p.status != "open" or p.deadline is None or p.deadline > state.tick:
            continue
        p.status = "broken"
        mood.adj_rel(state, p.to, p.frm, "trust", -20)
        to = state.chars.get(p.to)
        if to is not None and is_alive(to):
            memory.write(
                state,
                to,
                MemoryItem(
                    tick=state.tick,
                    kind="saw",
                    text=f"⟦{p.frm}⟧答应的事没有做到：{p.text}",
                    entities=[p.frm],
                    salience=7,
                ),
            )
            events.queue_replan(state, p.to)


def advance_time(state: WorldState, defs: Defs, ticks: int) -> list[Event]:
    """推进 ticks 刻，返回这段时间里内核自己产生的事件。"""
    out: list[Event] = []
    predicates.sync_enmity(state, defs)
    for _ in range(max(0, int(ticks))):
        state.tick += 1
        out += run_due_scheduled(state, defs)
        out += _statuses(state, defs)
        _needs(state)
        _promises(state)
        if state.tick % TICKS_PER_HOUR == 0:
            mood.decay(state)
    return out


def wake(state: WorldState, defs: Defs, cid: str) -> list[Event]:
    """歇息中途有事找上门：叫醒他。"""
    c = state.chars[cid]
    if not has_status(c, "asleep"):
        return []
    return effects.apply_effects(
        state, defs, [{"remove_status": [cid, "asleep"]}], Ctx(self_id=cid), source="rule"
    )
