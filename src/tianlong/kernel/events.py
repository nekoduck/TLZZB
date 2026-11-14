"""唯一造 ``Event`` 的地方（ARCHITECTURE 2）。

``emit`` 依次：分配连续 id；算见证者（6.8）；给见证者写记忆（5.6）；按 5.5 改情绪与关系；
放进 ``events_tail``（最近 200 条）；分量不低于 6 的事件成为可转述的事实 ``E<id>``（9.6）；
出了 8.3 所列的大事，相关的人进 ``replan_queue``。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import memory, mood, perception
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import (
    EVENTS_TAIL_MAX,
    Belief,
    Event,
    Fact,
    Speech,
    WorldState,
    has_status,
)

RETELL_SALIENCE = 6  # 分量不低于 6 的事件成为可转述的事实
REPLAN_SALIENCE = 8  # 记下一条分量不低于 8 的记忆，令其重新打算
MAJOR_KINDS = ("harmed", "captured", "poisoned", "death", "rescue", "relieved")
IMPORTANT_ITEM_TAGS = ("token", "manual", "antidote", "poison", "mount", "creature", "key", "treasure")


def kind(k: str, who: str | None = None, by: str | None = None, **extra: Any) -> dict:
    d: dict[str, Any] = {"k": k}
    if who is not None:
        d["who"] = who
    if by is not None:
        d["by"] = by
    d.update(extra)
    return d


def queue_replan(state: WorldState, cid: str | None) -> None:
    if not cid or cid == state.player:
        return
    c = state.chars.get(cid)
    if c is None or not perception.on_map(c):
        return
    if cid not in state.replan_queue:
        state.replan_queue.append(cid)


def emit(
    state: WorldState,
    defs: Defs,
    *,
    actor: str | None,
    verb: str,
    text: str,
    source: str,
    params: dict | None = None,
    outcome: str = "ok",
    loc: str | None = None,
    speech: Speech | None = None,
    step: str | None = None,
    offscreen: bool = False,
    loud: bool = False,
    hidden: bool = False,
    kinds: list[dict] | None = None,
    sound: str | None = None,
    overheard: str | None = None,
) -> Event:
    params = dict(params or {})
    if loc is None:
        if actor and actor in state.chars:
            loc = state.chars[actor].loc
        else:
            raise ValueError("emit: no location")
    kinds = list(kinds or [])
    if not kinds:
        kinds = [kind("talk")] if speech is not None else [kind("pass")]
    if loud and "sound" not in params:
        params["sound"] = sound or perception.sound_text(loc)
    if overheard:
        params["overheard"] = overheard
    if actor and not hidden and actor in state.chars and has_status(state.chars[actor], "hidden"):
        hidden = True  # 成功躲藏者的动作，别人看不见（6.8）
    w = perception.witnesses(state, defs, loc, actor, loud=loud, hidden=hidden)
    ev = Event(
        id=state.next_event_id,
        tick=state.tick,
        loc=loc,
        actor=actor,
        verb=verb,
        params=params,
        outcome=outcome,  # type: ignore[arg-type]
        fact_text=text,
        speech=speech,
        witnesses=w,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        step=step,
        offscreen=offscreen,
        kinds=kinds,
        salience=memory.event_salience(defs, kinds),
        loud=loud,
    )
    state.next_event_id += 1
    state.events_tail.append(ev)
    if len(state.events_tail) > EVENTS_TAIL_MAX:
        del state.events_tail[: len(state.events_tail) - EVENTS_TAIL_MAX]
    _write_memories(state, defs, ev)
    mood.on_event(state, defs, ev)
    _retellable(state, ev)
    _major(state, defs, ev, kinds)
    if state.player in w:
        perception.learn_terms_from_event(state, defs, ev)
    return ev


def _memory_text(state: WorldState, ev: Event, cid: str, how: str) -> tuple[str, str]:
    if how == "heard":
        return "heard", ev.params.get("sound") or perception.sound_text(ev.loc)
    if cid == ev.actor:
        return "did", ev.fact_text
    sp = ev.speech
    if sp is not None:
        if sp.whisper and cid not in sp.to:
            return "saw", ev.params.get("overheard") or ev.fact_text
        if cid in sp.to:
            return "told", ev.fact_text
    return "saw", ev.fact_text


def _write_memories(state: WorldState, defs: Defs, ev: Event) -> None:
    for cid, how in ev.witnesses.items():
        c = state.chars.get(cid)
        if c is None:
            continue
        mk, text = _memory_text(state, ev, cid, how)
        sal = memory.salience_for(defs, ev.kinds, cid, ev.actor) if how == "saw" else 1
        memory.write_event(state, defs, c, ev, kind=mk, text=text, salience=sal)
        if sal >= REPLAN_SALIENCE:
            queue_replan(state, cid)


def _retellable(state: WorldState, ev: Event) -> None:
    """分量不低于 6 的事件成为可转述的事实 E<id>，亲见者相信它。"""
    if ev.salience < RETELL_SALIENCE or ev.outcome == "blocked":
        return
    fid = f"E{ev.id}"
    if fid not in state.facts:
        state.facts[fid] = Fact(id=fid, text=ev.fact_text, tags=["event"])
    for cid, how in ev.witnesses.items():
        if how != "saw":
            continue
        c = state.chars.get(cid)
        if c is None:
            continue
        sp = ev.speech
        if sp is not None and sp.whisper and cid not in sp.to and cid != ev.actor:
            continue
        c.beliefs[fid] = Belief(confidence=1.0, source="saw", tick=ev.tick)


def _major(state: WorldState, defs: Defs, ev: Event, kinds: list[dict]) -> None:
    """8.3 的大事：亲近的人被擒、受伤、死去；自己中毒、被擒、得救；要紧的东西得失。"""
    for k in kinds:
        kk = k["k"]
        who = k.get("who")
        if kk in MAJOR_KINDS and who:
            queue_replan(state, who)
            for c in state.chars.values():
                r = c.rel.get(who)
                if r is not None and r.affinity >= mood.FRIEND and c.id != who:
                    queue_replan(state, c.id)
        elif kk == "item_change":
            it = state.items.get(k.get("item", ""))
            if it is not None and (set(it.tags) & set(IMPORTANT_ITEM_TAGS) or it.props.get("important")):
                queue_replan(state, k.get("who"))
                queue_replan(state, k.get("by"))


def add_kinds(state: WorldState, defs: Defs, ev: Event, kinds: list[dict]) -> None:
    """事件发出以后补上性质：补改情绪、记忆分量、可转述的事实与重新打算。"""
    if not kinds:
        return
    ev.kinds.extend(kinds)
    ev.salience = memory.event_salience(defs, ev.kinds)
    for cid, how in ev.witnesses.items():
        c = state.chars.get(cid)
        if c is None or how != "saw":
            continue
        sal = memory.salience_for(defs, ev.kinds, cid, ev.actor)
        memory.bump_salience(c, ev.id, sal)
        if sal >= REPLAN_SALIENCE:
            queue_replan(state, cid)
    mood.on_event(state, defs, ev, kinds)
    _retellable(state, ev)
    _major(state, defs, ev, kinds)
