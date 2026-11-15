"""年谱（方案 8.6、8.7）与「与原著的分岔」的显示。

**条目由规则生成**（``record``，每回合或每次世界推进后调用）：``salience`` 不低于 7 的事件；每个情境的开场
与了结；玩家每次身份与本事的变化；每条分岔。文字用事件的事实句。条目经 ``chronicle`` 效果写入，
``known_to_player`` 取「玩家亲见或听见」或「玩家相信这件事」。哪些是亲见的，另记在 teller 的
``chron_seen``（条目下标）里；年谱分三栏时据此区分「亲见的」与「听说的」。

**分岔**（``check_divergences``，每个时辰对照一次）：过了参照点的 ``by`` 再加一日的宽限，谓词从没成立过，
就记一条 ``Divergence``（``diverge`` 效果，同一参照点只记一次）。成立过一次的参照点记进 ``marks_hit``，
之后不再追究（D-075：一时的状态一旦成立即算达成）。起因取事件日志里最早一条由玩家引发、且涉及同一批
人或物的事件；有起因的分岔算玩家亲历过。参照点只用来事后对照和显示，运行时没有任何规则读它。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tianlong.kernel import mood, predicates, refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, Ctx, Event, Relation, WorldState, is_alive
from tianlong.storyteller import common

SALIENCE = 7
GRACE = TICKS_PER_DAY
ID_CAP = 400
BAND_TEXT = {"hostile": "敌视", "stranger": "生疏", "familiar": "相熟", "trusted": "信赖"}
ATTITUDE_CAP = 12


# ---------------------------------------------------------------- 条目


def _knows_event(state: WorldState, ev: Event) -> tuple[bool, bool]:
    """（玩家知道这件事，玩家亲眼见到）。"""
    how = ev.witnesses.get(state.player)
    p = common.player(state)
    heard = p is not None and common.believes(p, f"E{ev.id}")
    return (how is not None or heard), how == "saw"


def _situation_text(state: WorldState, defs: Defs, info: dict[str, Any], *, opened: bool) -> str:
    d = defs.situations.get(info.get("kind", ""))
    extra = (d.model_extra or {}) if d is not None else {}
    custom = extra.get("chronicle_open" if opened else "chronicle_close")
    roles = dict(info.get("roles") or {})
    if custom:
        return refs.fill_roles(str(custom), roles)
    people = [cid for cid in dict.fromkeys(roles.values())]
    who = "、".join(f"⟦{c}⟧" for c in people[:3]) or "几个人"
    where = f"⟦{info['loc']}⟧" if info.get("loc") else ""
    if opened:
        return f"{who}在{where}起了一场事"
    return f"{who}在{where}的那场事了结了"


def _role_text(defs: Defs, me: str, role: str, gained: bool) -> str | None:
    labels = defs.world.labels or {}
    if role.startswith("surname:"):
        return None
    if role.startswith("disciple_of:"):
        master = role.split(":", 1)[1]
        return f"⟦{me}⟧拜了⟦{master}⟧为师" if gained else f"⟦{me}⟧不再是⟦{master}⟧的徒弟"
    label = labels.get(f"role:{role}") or labels.get(role)
    if label is None and role in defs.lexicon.terms:
        label = f"⟦{role}⟧的人"
    if label is None:
        return f"⟦{me}⟧有了新的身份" if gained else f"⟦{me}⟧丢了原先的身份"
    return f"⟦{me}⟧成了{label}" if gained else f"⟦{me}⟧不再是{label}"


def _me_snapshot(state: WorldState) -> dict[str, Any]:
    p = state.chars[state.player]
    return {"roles": list(p.roles), "arts": dict(p.arts), "literacy": p.literacy}


def _sits_snapshot(state: WorldState) -> dict[str, dict]:
    return {s.id: {"kind": s.kind, "loc": s.loc, "roles": dict(s.roles)} for s in state.situations.values()}


def _art_text(state: WorldState, defs: Defs, art: str, level: int) -> str:
    ad = defs.arts.get(art)
    stage = ad.stages[level - 1].name if ad is not None and 0 < level <= len(ad.stages) else ""
    tail = f"：{stage}" if stage else ""
    return f"⟦{state.player}⟧的⟦{art}⟧有了长进{tail}"


def record(state: WorldState, defs: Defs, events: Iterable[Event] = ()) -> list[Event]:
    """把本回合（或这段时间）的大事记进年谱。重复调用不会重复记。"""
    p = common.player(state)
    if p is None:
        return []
    entries: list[tuple[dict, bool]] = []  # (条目, 玩家亲见)
    done_ids = list(common.teller(state, "chron_ids", []))
    done = set(done_ids)
    for ev in sorted(events, key=lambda e: e.id):
        if ev.id in done or ev.salience < SALIENCE or ev.outcome == "blocked":
            continue
        known, saw = _knows_event(state, ev)
        entries.append(({"tick": ev.tick, "text": ev.fact_text, "event": ev.id, "known_to_player": known}, saw))
        done_ids.append(ev.id)
        done.add(ev.id)
    # 情境的开场与了结
    before = common.teller(state, "chron_sits", {})
    now_sits = _sits_snapshot(state)
    if isinstance(before, dict):
        for sid, info in now_sits.items():
            if sid not in before:
                here = info["loc"] == p.loc
                text = _situation_text(state, defs, info, opened=True)
                entries.append(({"tick": state.tick, "text": text, "known_to_player": here}, here))
        for sid, info in before.items():
            if sid not in now_sits:
                here = info.get("loc") == p.loc
                text = _situation_text(state, defs, info, opened=False)
                entries.append(({"tick": state.tick, "text": text, "known_to_player": here}, here))
    # 玩家的身份与本事
    me_before = common.teller(state, "chron_me")
    me_now = _me_snapshot(state)
    if isinstance(me_before, dict):
        for r in me_now["roles"]:
            if r not in me_before.get("roles", []) and (t := _role_text(defs, state.player, r, True)):
                entries.append(({"tick": state.tick, "text": t, "known_to_player": True}, True))
        for r in me_before.get("roles", []):
            if r not in me_now["roles"] and (t := _role_text(defs, state.player, r, False)):
                entries.append(({"tick": state.tick, "text": t, "known_to_player": True}, True))
        for art, lvl in me_now["arts"].items():
            if lvl > int(me_before.get("arts", {}).get(art, 0)):
                text = _art_text(state, defs, art, lvl)
                entries.append(({"tick": state.tick, "text": text, "known_to_player": True}, True))
        if me_now["literacy"] > int(me_before.get("literacy", me_now["literacy"])):
            entries.append(({"tick": state.tick, "text": f"⟦{state.player}⟧识得的字又多了些", "known_to_player": True}, True))
    # 分岔
    marks = list(common.teller(state, "chron_marks", []))
    texts = {m.id: m.text for m in defs.baseline}
    for d in state.divergences:
        if d.mark in marks:
            continue
        marks.append(d.mark)
        text = f"与原著的分岔：{texts.get(d.mark, d.mark)}——这件事没有照原样发生"
        entries.append(({"tick": d.tick, "text": text, "known_to_player": d.known_to_player}, False))
    seen = list(common.teller(state, "chron_seen", []))
    base = len(state.chronicle)
    effs: list[dict] = []
    for i, (entry, saw) in enumerate(entries):
        effs.append({"chronicle": entry})
        if saw:
            seen.append(base + i)
    values: dict[str, Any] = {
        "chron_ids": done_ids[-ID_CAP:],
        "chron_sits": now_sits,
        "chron_me": me_now,
        "chron_marks": marks,
        "chron_seen": seen,
    }
    if common.teller(state, "chron_me0") is None:
        values["chron_me0"] = me_now
    effs += common.teller_effects(values)
    return common.book(state, defs, effs)


# ---------------------------------------------------------------- 分岔


def _mark_entities(state: WorldState, pred: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(pred, dict):
        for v in pred.values():
            out |= _mark_entities(state, v)
    elif isinstance(pred, list):
        for v in pred:
            out |= _mark_entities(state, v)
    elif isinstance(pred, str) and (pred in state.chars or pred in state.items):
        out.add(pred)
    return out


def _event_entities(ev: Event) -> set[str]:
    ids = set(refs.refs_in(ev.fact_text))
    if ev.actor:
        ids.add(ev.actor)
    ids |= {v for v in ev.params.values() if isinstance(v, str)}
    for k in ev.kinds:
        for f in ("who", "by"):
            if isinstance(k.get(f), str):
                ids.add(k[f])
    return ids


def _cause(state: WorldState, ents: set[str]) -> int | None:
    for ev in state.events_tail:
        if ev.source != "player" and ev.actor != state.player:
            continue
        if ev.outcome == "blocked":
            continue
        if ents & (_event_entities(ev) - {state.player}):
            return ev.id
    return None


def check_divergences(state: WorldState, defs: Defs) -> list[Event]:
    """对照参照点（每个时辰一次）。成立过的记进 marks_hit；过期仍没成立过的记一条分岔。"""
    hit = list(common.teller(state, "marks_hit", []))
    have = {d.mark for d in state.divergences}
    effs: list[dict] = []
    for m in defs.baseline:
        if m.id in hit or m.id in have:
            continue
        try:
            ok = predicates.holds(state, defs, m.holds, Ctx())
        except predicates.KernelError:
            continue
        if ok:
            hit.append(m.id)
            continue
        if m.by is None or state.tick <= m.by + GRACE:
            continue
        cause = _cause(state, _mark_entities(state, m.holds))
        effs.append({"diverge": {"mark": m.id, "tick": state.tick, "known_to_player": cause is not None,
                                 "cause_event": cause}})
    if hit != common.teller(state, "marks_hit", []):
        effs.append({"teller": ["marks_hit", hit]})
    return common.book(state, defs, effs)


# ---------------------------------------------------------------- 显示


def columns(state: WorldState) -> dict[str, list[int]]:
    """三栏：亲见的、听说的、当时不知道的（条目下标）。"""
    p = common.player(state)
    seen = set(common.teller(state, "chron_seen", []))
    out: dict[str, list[int]] = {"saw": [], "heard": [], "unknown": []}
    for i, e in enumerate(state.chronicle):
        if i in seen:
            out["saw"].append(i)
        elif e.known_to_player or (p is not None and e.event is not None and common.believes(p, f"E{e.event}")):
            out["heard"].append(i)
        else:
            out["unknown"].append(i)
    return out


def _line(state: WorldState, defs: Defs, idx: int, final: bool) -> str:
    e = state.chronicle[idx]
    text = common.omni(state, defs, e.text) if final else common.pv(state, defs, e.text)
    return f"  {common.clock(e.tick)}　{text}"


def attitudes(state: WorldState, defs: Defs, *, final: bool = False) -> list[str]:
    """各人对你的态度：只列与你打过交道的人（他对你有了看法，或你认得他），给档位不给数字。"""
    me = state.player
    rows: list[tuple[int, str]] = []
    for c in state.chars.values():
        if c.id == me:
            continue
        r = c.rel.get(me)
        mine = state.chars[me].rel.get(c.id) if me in state.chars else None
        met = (r is not None and (r.trust or r.affinity or r.fear)) or (mine is not None and (mine.trust or mine.affinity))
        if not met:
            continue
        r = r or Relation()
        band = BAND_TEXT.get(mood.band(defs, state, c.id, me), "生疏")
        bits = [band, mood.trust_text(r.trust), mood.affinity_text(r.affinity)]
        if r.fear >= 20:
            bits.append(mood.fear_text(r.fear))
        name = common.omni(state, defs, f"⟦{c.id}⟧") if final else common.pv(state, defs, f"⟦{c.id}⟧")
        dead = "（已故）" if not is_alive(c) and (final or _player_knows_death(state, c.id)) else ""
        rows.append((abs(r.trust) + abs(r.affinity) + r.fear, f"  {name}{dead}：{'，'.join(bits)}"))
    rows.sort(key=lambda x: -x[0])
    return [line for _, line in rows[:ATTITUDE_CAP]]


def _player_knows_death(state: WorldState, cid: str) -> bool:
    p = common.player(state)
    for ev in state.events_tail:
        if any(k.get("k") == "death" and k.get("who") == cid for k in ev.kinds):
            if ev.witnesses.get(state.player) or (p is not None and common.believes(p, f"E{ev.id}")):
                return True
    return False


def divergence_lines(state: WorldState, defs: Defs, *, final: bool = False) -> list[str]:
    """与原著的分岔：游戏中只列玩家亲历的；收场时列全部，附上追溯到的起因。"""
    texts = {m.id: m.text for m in defs.baseline}
    causes = {ev.id: ev for ev in state.events_tail}
    out: list[str] = []
    for d in state.divergences:
        if not final and not d.known_to_player:
            continue
        line = f"  · {texts.get(d.mark, d.mark)}——没有照原样发生（{common.clock(d.tick)}认定）"
        ev = causes.get(d.cause_event) if d.cause_event is not None else None
        if final and ev is not None:
            line += f"；起因：{common.omni(state, defs, ev.fact_text)}"
        out.append(line)
    return out


def render(state: WorldState, defs: Defs, *, final: bool = False) -> str:
    """/年谱 的画面。``final`` 为真（收场后）时多出「当时不知道的」一栏、完整的分岔清单，并用真名。"""
    cols = columns(state)
    lines = ["【年谱】", "亲见的："]
    lines += [_line(state, defs, i, final) for i in cols["saw"]] or ["  （还没有）"]
    lines.append("听说的：")
    lines += [_line(state, defs, i, final) for i in cols["heard"]] or ["  （还没有）"]
    if final:
        lines.append("当时不知道的：")
        lines += [_line(state, defs, i, final) for i in cols["unknown"]] or ["  （没有）"]
    lines.append("各人对你的态度：")
    lines += attitudes(state, defs, final=final) or ["  （还没有谁对你有什么看法）"]
    lines.append("与原著的分岔：")
    div = divergence_lines(state, defs, final=final)
    if div:
        lines += div
    else:
        lines.append("  （没有记下）" if final else "  （你还不知道有什么不同）")
    return "\n".join(lines)
