"""不变量（方案 6.11）：``check(state, defs) -> list[str]``（空表即通过）；``liveness``。"""

from __future__ import annotations

from tianlong.kernel import memory, mood, predicates
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Event, WorldState, is_alive

RANGES: dict[str, tuple[int, int]] = {
    "neili": (0, 100),
    "literacy": (0, 3),
    "aptitude": (0, 3),
    "luck": (0, 20),
    "base_power": (0, 10),
}


def check(
    state: WorldState,
    defs: Defs,
    *,
    new_events: list[Event] | None = None,
    tick_before: int | None = None,
    pending_before: set[str] | None = None,
    able_before: set[str] | None = None,
) -> list[str]:
    out: list[str] = []
    holders: dict[str, str] = {}
    for c in state.chars.values():
        alive = is_alive(c)
        if c.onstage and alive and c.loc not in state.locations:
            out.append(f"{c.id} is at unknown location {c.loc!r}")
        for f, (lo, hi) in RANGES.items():
            v = getattr(c, f)
            if not lo <= v <= hi:
                out.append(f"{c.id}.{f}={v} out of range")
        for k, v in c.mood.items():
            if not 0 <= v <= 100:
                out.append(f"{c.id}.mood[{k}]={v} out of range")
        for oid, r in c.rel.items():
            for f, (lo, hi) in mood.REL_RANGES.items():
                v = getattr(r, f)
                if not lo <= v <= hi:
                    out.append(f"{c.id}.rel[{oid}].{f}={v} out of range")
        for s in c.statuses:
            if s.kind == "injured" and not 1 <= s.level <= 3:
                out.append(f"{c.id} injured level {s.level}")
        for fid, b in c.beliefs.items():
            if fid not in state.facts:
                out.append(f"{c.id} believes unknown fact {fid}")
            if not 0.0 <= b.confidence <= 1.0:
                out.append(f"{c.id} belief {fid} confidence {b.confidence}")
        if len(c.memory) > memory.MAX_MEMORIES:
            out.append(f"{c.id} has {len(c.memory)} memories")
        for iid in c.inventory:
            it = state.items.get(iid)
            if it is None:
                out.append(f"{c.id} holds unknown item {iid}")
                continue
            if iid in holders:
                out.append(f"item {iid} held by both {holders[iid]} and {c.id}")
            holders[iid] = c.id
            if it.holder != c.id:
                out.append(f"item {iid} in {c.id} inventory but holder is {it.holder}")
        if c.agenda is not None and not 0 <= c.agenda.cursor <= len(c.agenda.steps):
            out.append(f"{c.id} agenda cursor out of range")
    for it in state.items.values():
        if it.in_world:
            if (it.holder is None) == (it.loc is None):
                out.append(f"item {it.id} must have exactly one of holder/loc")
            if it.holder is not None and holders.get(it.id) != it.holder:
                out.append(f"item {it.id} holder {it.holder} does not carry it")
            if it.loc is not None:
                if it.loc not in state.locations:
                    out.append(f"item {it.id} at unknown location {it.loc}")
                elif it.feature is not None and it.feature not in state.locations[it.loc].features:
                    out.append(f"item {it.id} in unknown feature {it.feature}")
        elif it.holder is not None or it.loc is not None:
            out.append(f"item {it.id} is out of the world but placed")
    if tick_before is not None and state.tick < tick_before:
        out.append(f"tick went back {tick_before} -> {state.tick}")
    tail = state.events_tail
    for a, b in zip(tail, tail[1:], strict=False):
        if b.id != a.id + 1:
            out.append(f"event ids not contiguous: {a.id} -> {b.id}")
            break
    if tail and tail[-1].id != state.next_event_id - 1:
        out.append("next_event_id out of step with events_tail")
    for r in state.requests.values():
        if r.status in ("open", "countered"):
            for who in (r.frm, r.to):
                c = state.chars.get(who)
                if c is None or not is_alive(c):
                    out.append(f"request {r.id} is open but {who} is dead")
    for s in state.situations.values():
        if s.loc not in state.locations:
            out.append(f"situation {s.id} at unknown location")
        for cid in s.roles.values():
            if cid not in state.chars:
                out.append(f"situation {s.id} binds unknown {cid}")
    for ev in new_events or []:
        sp = ev.speech
        if sp is not None and ev.actor in state.chars:
            speaker = state.chars[ev.actor]
            for fid in sp.fact_refs:
                b = speaker.beliefs.get(fid)
                if b is None or b.confidence < predicates.BELIEVE:
                    out.append(f"event {ev.id}: {ev.actor} says {fid} without believing it")
        if pending_before is not None and ev.step is not None and ev.step not in pending_before:
            out.append(f"event {ev.id} comes from finished or void step {ev.step}")
        if (
            able_before is not None
            and ev.actor is not None
            and ev.actor not in able_before
            and ev.verb != "enter"
        ):
            out.append(f"event {ev.id}: {ev.actor} acted while dead or off the map")
    return out


def liveness(state: WorldState, defs: Defs) -> list[str]:
    """活性约束：图内每个活着的有魂、有名角色，要么有一步进行中的打算，要么在 waiting 并写明在等什么。
    返回不满足的人。"""
    stalled: list[str] = []
    for c in state.chars.values():
        if c.id == state.player or c.tier not in ("soul", "named") or not c.onstage or not is_alive(c):
            continue
        ag = c.agenda
        if ag is None:
            stalled.append(c.id)
            continue
        if ag.status == "waiting" and ag.waiting_for:
            continue
        if ag.status == "active" and any(st.state == "pending" for st in ag.steps[ag.cursor :]):
            continue
        stalled.append(c.id)
    return stalled
