"""与原著的分岔（方案 8.6）：每个时辰对照一次 ``content/baseline.yaml`` 的参照点，事后显示。

**全项目只有这一处认定分岔**：``check`` 由 ``world.tick`` 每个时辰调用一次（方案 8.1 第 5 步），经内核效果
``diverge`` 写进 ``state.divergences``（同一参照点只记一次），带上起因 ``cause_event`` 与
``known_to_player``。storyteller 只读 ``state.divergences`` 来写年谱和收场的清单，不另行对照。

- 参照点的谓词成立过一次就算达成（D-075：被擒这类一时的状态也一样）。
- 过了 ``by`` 再加一日的宽限而仍未达成，记一条 ``Divergence``。
- 起因：事件日志里最早一条由玩家引发、且涉及同一批人或物的事件。事件尾巴只留最近 200 条，所以每个时辰
  扫一遍新事件，把候选的起因记在旗标 ``__baseline__`` 里。改变了那些人或物的事件（易手、伤亡、擒放、
  中毒、得救，以及开口相求、相劝、示警、喝令、威胁）优先于只是沾了边的事件（例如跟那人打过招呼）。
- 「同一批人或物」：参照点谓词参数里的人与物、所引事实提到的人与物、参照点自己列的 ``refs``，以及
  参照点那句话里叫得出名字的人与物（只写旗标的参照点靠这一条才追得到起因）。
- ``known_to_player``：玩家亲见或听见了起因事件，或者之后相信了它（事实 ``E<id>``）。

参照点只用来事后对照与显示。运行时没有任何规则读它来决定事情的走向：这里写下的记账只有本模块自己读。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import predicates, refs, resolve, timeflow
from tianlong.kernel.defs import BaselineMark, Defs
from tianlong.kernel.state import TICKS_PER_DAY, Ctx, Divergence, Event, WorldState
from tianlong.world.common import BASELINE_FLAG, LOG, flag_dict

GRACE = TICKS_PER_DAY  # 过了 by 再宽限一日
MAJOR_KINDS = ("item_change", "harmed", "captured", "death", "rescue", "relieved", "poisoned", "gift")
MAJOR_VERBS = ("take", "steal", "give", "attack", "restrain", "release", "use", "push", "block")
MAJOR_ACTS = ("request", "plead", "order", "warn", "threaten")  # 想改变别人做什么的话
NAME_MIN = 2  # 参照点那句话里，至少两个字的名字才算数


def mark_entities(state: WorldState, mark: BaselineMark) -> set[str]:
    """参照点涉及的人与物：谓词参数里的人、物，所引事实文字里提到的人、物，参照点自己列的 refs，
    以及参照点那句话里叫得出名字的人、物。"""
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            if x in state.chars or x in state.items:
                out.add(x)
            elif x in state.facts and not x.startswith("exit:"):
                for e in refs.refs_in(state.facts[x].text):
                    if e in state.chars or e in state.items:
                        out.add(e)

    walk(mark.holds)
    walk((mark.model_extra or {}).get("refs"))
    text = mark.text or ""
    for e in refs.refs_in(text):
        if e in state.chars or e in state.items:
            out.add(e)
    for table in (state.chars, state.items):
        for eid, x in table.items():
            if len(x.name) >= NAME_MIN and x.name in text:
                out.add(eid)
    out.discard(state.player)
    return out


def event_entities(state: WorldState, ev: Event) -> set[str]:
    out: set[str] = set()
    if ev.actor:
        out.add(ev.actor)
    for v in ev.params.values():
        vals = v if isinstance(v, list) else [v]
        for x in vals:
            if isinstance(x, str) and (x in state.chars or x in state.items):
                out.add(x)
    for k in ev.kinds:
        for f in ("who", "by", "item"):
            v = k.get(f)
            if isinstance(v, str):
                out.add(v)
    if ev.speech is not None:
        out.update(ev.speech.to)
    for e in refs.refs_in(ev.fact_text):
        if e in state.chars or e in state.items:
            out.add(e)
    return out


def player_caused(state: WorldState, ev: Event) -> bool:
    return ev.source == "player" or ev.actor == state.player


def is_major(ev: Event) -> bool:
    """改变了人或物的处境的事件；开口相求、相劝、示警、喝令、威胁也算（它们想改变别人做什么）。"""
    if ev.verb in MAJOR_VERBS or any(k.get("k") in MAJOR_KINDS for k in ev.kinds):
        return True
    return ev.speech is not None and ev.speech.act in MAJOR_ACTS


def player_witnessed(state: WorldState, ev: Event) -> bool:
    return ev.actor == state.player or state.player in ev.witnesses


def _player_believes(state: WorldState, eid: int | None) -> bool:
    if eid is None:
        return False
    p = state.chars.get(state.player)
    if p is None:
        return False
    b = p.beliefs.get(f"E{eid}")
    return b is not None and b.confidence >= predicates.BELIEVE


def _book(state: WorldState) -> dict[str, Any]:
    b = flag_dict(state, BASELINE_FLAG)
    b.setdefault("reached", {})
    b.setdefault("cause", {})  # mark → {"major": [id, seen], "any": [id, seen]}
    b.setdefault("scan", 0)
    return b


def _scan(state: WorldState, defs: Defs, book: dict[str, Any]) -> None:
    """扫一遍上次以来的新事件，记下各参照点候选的起因（最早的那条）。"""
    last = int(book.get("scan", 0))
    open_marks = [
        m
        for m in defs.baseline
        if m.id not in book["reached"] and not any(d.mark == m.id for d in state.divergences)
    ]
    ents = {m.id: mark_entities(state, m) for m in open_marks}
    for ev in state.events_tail:
        if ev.id <= last:
            continue
        last = ev.id
        if not player_caused(state, ev) or ev.outcome == "blocked":
            continue
        touched = event_entities(state, ev)
        for m in open_marks:
            if not ents[m.id] & touched:
                continue
            slot = book["cause"].setdefault(m.id, {})
            key = "major" if is_major(ev) else "any"
            if key not in slot:
                slot[key] = [ev.id, player_witnessed(state, ev)]
    book["scan"] = last


def check(state: WorldState, defs: Defs) -> list[Divergence]:
    """每个时辰调用一次。返回这次新记下的分岔。"""
    if not defs.baseline:
        return []
    book = _book(state)
    _scan(state, defs, book)
    new: list[Divergence] = []
    effs: list[dict] = []
    for m in defs.baseline:
        if m.id in book["reached"] or any(d.mark == m.id for d in state.divergences):
            continue
        try:
            ok = predicates.holds(state, defs, m.holds, Ctx())
        except predicates.KernelError as e:
            LOG.warning("baseline %s: bad predicate: %s", m.id, e)
            continue
        if ok:
            book["reached"][m.id] = state.tick
            continue
        if m.by is None or state.tick <= m.by + GRACE:
            continue
        slot = book["cause"].get(m.id) or {}
        cause = slot.get("major") or slot.get("any")
        eid = int(cause[0]) if cause else None
        known = bool(cause and cause[1]) or _player_believes(state, eid)
        d = Divergence(mark=m.id, tick=state.tick, known_to_player=known, cause_event=eid)
        new.append(d)
        effs.append({"diverge": d.model_dump()})
    effs.append({"set_flag": [BASELINE_FLAG, book]})
    resolve.run_effects(state, defs, effs, source="rule")
    recorded = {d.mark for d in state.divergences}
    return [d for d in new if d.mark in recorded]  # 效果整体回滚时什么也没记下


def reached(state: WorldState) -> dict[str, int]:
    """已经达成的参照点 → 达成的时刻（记账里读出来，只供报告用）。"""
    return dict(_book(state)["reached"])


def divergences_report(state: WorldState, defs: Defs) -> list[dict[str, Any]]:
    """收场时「与原著的分岔」那张清单（8.6），由规则列出，不经模型。纯函数。"""
    marks = {m.id: m for m in defs.baseline}
    tail = {e.id: e for e in state.events_tail}
    out = []
    for d in sorted(state.divergences, key=lambda x: (x.tick, x.mark)):
        m = marks.get(d.mark)
        ev = tail.get(d.cause_event) if d.cause_event is not None else None
        out.append(
            {
                "mark": d.mark,
                "text": m.text if m else d.mark,
                "by": m.by if m else None,
                "tick": d.tick,
                "clock": timeflow.clock_text(d.tick),
                "cause_event": d.cause_event,
                "cause_text": refs.render(ev.fact_text, state, defs) if ev is not None else "",
                "known_to_player": d.known_to_player or _player_believes(state, d.cause_event),
            }
        )
    return out


__all__ = ["check", "divergences_report", "reached", "mark_entities", "event_entities", "GRACE"]
