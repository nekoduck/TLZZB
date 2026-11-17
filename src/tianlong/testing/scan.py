"""扫事件日志（方案 11.4 的三条保证）。都返回「问题」的清单，空表即通过。

1. ``ghost_events``：没有幽灵事件——出自原著（canon）的步骤或情境，前提不成立时不得产生事件。
2. ``rebound_events``：没有回弹——分岔之后，世界的每一次改变都出自角色的动作、情境的结算或机会牌；
   此外只有内核自己随时间走的规则（状态到期、饥饿疲惫、到点的 scheduled）与记账。
3. ``stall_problems``：没有停摆——6.11 的活性约束始终成立（无事可做的人在一个时辰之内就有了新打算）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tianlong.kernel.state import Event
from tianlong.testing.monitor import HOUR_SLACK, Monitor
from tianlong.testing.trace import Tracer

# 角色的动作（玩家、在场的人、离场者的打算）、情境的结算、机会牌
WORLD_SOURCES = frozenset({"player", "mind", "agenda", "situation", "storyteller"})

# 以 rule 为来源、由内核之外发起的效果调用里，只许有这些记账用的效果：
# 换打算与排队（8.3）、认定分岔（8.6）、回合数与选项、牵挂与年谱、夜间反思的印象与标记。
BOOKKEEPING = frozenset(
    {
        "agenda", "replan", "dequeue", "mark_char", "step_state", "progress",
        "set_flag", "diverge", "turn", "offer", "thread", "close_thread", "chronicle",
    }
)  # fmt: skip


def ghost_events(events: Iterable[Event], tracer: Tracer, defs: Any, *, strict: bool = True) -> list[str]:
    """出自原著的步骤或情境，在前提不成立时产生的事件。

    ``strict``：带 ``step`` 的事件若没能在动手之前核对（探针没看到它从哪个入口出来），也算问题。
    """
    out: list[str] = []
    for ev in events:
        if ev.step is not None:
            chk = tracer.step_checks.get(ev.id)
            if chk is None:
                if strict:
                    out.append(f"event {ev.id} ({ev.verb}) carries step {ev.step} but no kernel entry was seen")
            elif chk.origin == "canon":
                if not chk.pending:
                    out.append(f"event {ev.id}: canon step {chk.char}/{chk.step} produced {ev.verb} after it was done or void")
                elif chk.pre_ok is False:
                    out.append(f"event {ev.id}: canon step {chk.char}/{chk.step} produced {ev.verb} while its pre was false")
                elif not chk.in_window:
                    out.append(f"event {ev.id}: canon step {chk.char}/{chk.step} produced {ev.verb} outside start/deadline")
                elif chk.pre_ok is None and strict:
                    out.append(f"event {ev.id}: canon step {chk.char}/{chk.step} could not be checked before it ran")
        sit = tracer.sit_checks.get(ev.id)
        if sit is not None:
            if sit.origin == "canon" and not sit.ok:
                out.append(f"event {ev.id}: canon situation {sit.kind} ({sit.how}) produced {ev.verb} while its condition was false")
        elif ev.source == "situation":
            path = tracer.entry_of.get(ev.id, "")
            if "scheduled" not in path:
                out.append(f"event {ev.id} ({ev.verb}) has source situation but came from no situation ({path or 'unseen'})")
    return out


def rebound_events(
    events: Iterable[Event],
    tracer: Tracer,
    *,
    after_id: int = 0,
    cheat_ids: Iterable[int] = (),
) -> list[str]:
    """分岔（事件 ``after_id``）之后不合规矩的改动。"""
    out: list[str] = []
    cheats = set(cheat_ids)
    rule_event_keys: dict[int, tuple[str, ...]] = {}
    for call in tracer.rule_calls:
        for eid in call.events:
            rule_event_keys[eid] = call.keys
        if call.cheat or call.during:  # 测试的布景；或内核自己在时间流逝里执行的
            continue
        if call.at_event <= after_id:
            continue  # 分岔之前的
        extra = set(call.keys) - BOOKKEEPING
        if extra:
            out.append(f"tick {call.tick}: rule-source effects outside bookkeeping: {sorted(extra)}")
    for ev in events:
        if ev.id <= after_id or ev.id in cheats:
            continue
        if ev.source in WORLD_SOURCES:
            continue
        path = tracer.entry_of.get(ev.id, "")
        if path.startswith("timeflow") or "scheduled" in path:
            continue  # 内核随时间走的规则
        if path.startswith(("apply", "settle")):
            continue  # 一次动作或情境结算之内、内核顺带结算出来的（例如答应了还价，条件当场兑现）
        keys = rule_event_keys.get(ev.id)
        if keys is not None and set(keys) <= BOOKKEEPING:
            continue
        out.append(f"event {ev.id} tick {ev.tick}: {ev.verb} by {ev.actor} has source {ev.source} ({path or 'unseen'}; {keys})")
    for w in tracer.foreign:
        out.append(f"tick {w.tick}: world changed outside the kernel before {w.before_entry}: {'; '.join(w.diff)}")
    return out


def stall_problems(monitor: Monitor, *, slack: int = HOUR_SLACK) -> list[str]:
    """活性：有人无事可做超过 ``slack`` 刻（默认一个时辰）。"""
    out = [f"{s.char} idle {s.ticks} ticks ({s.since} → {s.until})" for s in monitor.stalls]
    seen = {s.char for s in monitor.stalls}
    for cid, span in monitor.idle_spans.items():
        if span > slack and cid not in seen:
            out.append(f"{cid} idle for {span} ticks at some point")
    return out


def dead_actors(events: Iterable[Event], dead: dict[str, int]) -> list[str]:
    """死者不再出现在任何事件里：``dead`` 是 {人: 记下他死去的那条事件的 id}。

    死后的事件里，他不得是做事的人、被做事的对象、听话的人。（别人提起他、发现他的死，不在此列。）
    """
    out: list[str] = []
    for ev in events:
        for cid, at in dead.items():
            if ev.id <= at:
                continue
            if ev.actor == cid:
                out.append(f"event {ev.id} tick {ev.tick}: dead {cid} acts ({ev.verb})")
            elif ev.outcome != "blocked" and cid in (ev.params.get("target"), ev.params.get("target2")) and ev.verb != "examine":
                out.append(f"event {ev.id} tick {ev.tick}: {ev.verb} by {ev.actor} targets dead {cid}")
            elif ev.speech is not None and cid in ev.speech.to:
                out.append(f"event {ev.id} tick {ev.tick}: {ev.actor} speaks to dead {cid}")
    return out


def guarantees(
    events: list[Event],
    tracer: Tracer,
    monitor: Monitor,
    defs: Any,
    *,
    after_id: int = 0,
    cheat_ids: Iterable[int] = (),
    slack: int = HOUR_SLACK,
) -> dict[str, list[str]]:
    """方案 11.4 的三条统一断言。"""
    return {
        "ghost": ghost_events(events, tracer, defs),
        "rebound": rebound_events(events, tracer, after_id=after_id, cheat_ids=cheat_ids),
        "stall": stall_problems(monitor, slack=slack),
    }


__all__ = [
    "BOOKKEEPING",
    "WORLD_SOURCES",
    "dead_actors",
    "ghost_events",
    "guarantees",
    "rebound_events",
    "stall_problems",
]
