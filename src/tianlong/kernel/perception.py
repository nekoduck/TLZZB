"""感知（方案 6.8）与玩家视角过滤（方案 4.4）。

- 同一地点且清醒的人都算看见；成功躲藏者的动作别人看不见。
- 出声的事件传到 ``loud_to`` 里的地点，那里的人只得到声响层面的一句话。
- 耳语只有对象听得到内容，其余在场者只看见两人在低语。
- 专名要听到才知道：``learn_terms_from_event``。
"""

from __future__ import annotations

from pydantic import BaseModel

from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import (
    Character,
    Event,
    Item,
    WorldState,
    get_status,
    has_status,
    injury_level,
    is_alive,
)

UNCONSCIOUS = ("unconscious", "asleep", "dead")
BELIEVE = 0.6  # 与 predicates.BELIEVE 相同（这里不 import predicates，免得成环）


def conscious(c: Character) -> bool:
    return is_alive(c) and not any(has_status(c, k) for k in UNCONSCIOUS)


def on_map(c: Character) -> bool:
    return c.onstage and is_alive(c)


def present(state: WorldState, loc: str, *, exclude: str | None = None) -> list[Character]:
    """在 loc 的活人（含躲着的）。"""
    return [c for c in state.chars.values() if c.loc == loc and on_map(c) and c.id != exclude]


def visible_to(viewer: Character, other: Character) -> bool:
    """viewer 看不看得见 other：躲着的人别人看不见。"""
    if other.id == viewer.id:
        return True
    return not has_status(other, "hidden")


def visible_people(state: WorldState, viewer: Character) -> list[Character]:
    return [o for o in present(state, viewer.loc, exclude=viewer.id) if visible_to(viewer, o)]


OPEN_TAGS = ("mount",)  # 牵在手里、骑在胯下的东西，谁都看得见
MEMORY_SEEN = ("saw", "did", "told")


def carried_openly(it: Item) -> bool:
    """别人带在身上、却一眼看得见的东西：坐骑，或物件写了 ``props.visible``。"""
    return bool(it.props.get("visible")) or bool(set(it.tags) & set(OPEN_TAGS))


def seen_entities(state: WorldState, viewer: Character) -> set[str]:
    """viewer 见过、经手过、听人当面说起过的实体 id；外加他细看过的人与物（``char:<id>`` / ``item:<id>``）。

    玩家细看过什么记在 ``state.examined``；其余的人看最近的事件。一次 ``legal_actions`` 里只算一遍。
    """
    out: set[str] = set()
    for m in viewer.memory:
        if m.kind in MEMORY_SEEN:
            out.update(m.entities)
    if viewer.id == state.player:
        out.update(k for k in state.examined if k.startswith(("char:", "item:")))
    else:
        for ev in state.events_tail:
            if ev.verb == "examine" and ev.actor == viewer.id and ev.outcome == "ok":
                if "target" in ev.params:
                    out.add(f"char:{ev.params['target']}")
                elif "item" in ev.params:
                    out.add(f"item:{ev.params['item']}")
    return out


def knows_item_on(state: WorldState, viewer: Character, it: Item, *, seen: set[str] | None = None) -> bool:
    """viewer 知不知道 ``it`` 在它的主人身上：细看过主人或这件东西；见过、经手过这件东西；
    相信一条同时提到主人与这件东西的事实；自己的打算里就有这件东西。"""
    holder = it.holder
    if holder is None:
        return False
    if seen is None:
        seen = seen_entities(state, viewer)
    if it.id in seen or f"char:{holder}" in seen or f"item:{it.id}" in seen:
        return True
    for fid, b in viewer.beliefs.items():
        if b.confidence < BELIEVE:
            continue
        f = state.facts.get(fid)
        if f is None or it.id not in f.text:
            continue
        mentioned = refs.refs_in(f.text)
        if it.id in mentioned and holder in mentioned:
            return True
    ag = viewer.agenda
    if ag is not None:
        for st in ag.steps[ag.cursor :]:
            if st.state == "pending" and st.do.get("item") == it.id:
                return True
    return False


def item_visible(
    state: WorldState, viewer: Character, item_id: str | None, *, seen: set[str] | None = None
) -> bool:
    """viewer 此刻看不看得见（或知不知道）这件东西（K9）。

    自己身上的；地上明摆着的（不在景物里）；在场而看得见的人身上的东西，要么明摆着
    （``carried_openly``），要么 viewer 知道它在那人身上（``knows_item_on``）。
    ``seen`` 可以传 ``seen_entities`` 的结果，免得每件东西都重算一遍。
    """
    it = state.items.get(item_id) if isinstance(item_id, str) else None
    if it is None or not it.in_world:
        return False
    if it.holder == viewer.id:
        return True
    if it.holder is None:
        return it.loc == viewer.loc and it.feature is None
    h = state.chars.get(it.holder)
    if h is None or h.loc != viewer.loc or not h.onstage or not visible_to(viewer, h):
        return False
    return carried_openly(it) or knows_item_on(state, viewer, it, seen=seen)


def witnesses(
    state: WorldState,
    defs: Defs,
    loc: str,
    actor: str | None,
    *,
    loud: bool = False,
    hidden: bool = False,
) -> dict[str, str]:
    """谁看见（saw）、谁听见（heard）。``hidden``：动作者躲着，或者这是一件暗中的事。"""
    out: dict[str, str] = {}
    for c in state.chars.values():
        if c.loc != loc or not c.onstage or not conscious(c):
            continue
        if hidden and not loud and c.id != actor:
            continue
        out[c.id] = "saw"
    if actor and actor in state.chars and actor not in out:
        a = state.chars[actor]
        if a.onstage and is_alive(a) and not has_status(a, "dead"):
            out[actor] = "saw"
    if loud:
        L = state.locations.get(loc)
        for other in L.loud_to if L else []:
            for c in state.chars.values():
                if c.loc == other and c.onstage and conscious(c) and c.id not in out:
                    out[c.id] = "heard"
    return out


def sound_text(loc: str) -> str:
    return f"⟦{loc}⟧那边传来一阵响动"


def learn_terms_from_text(state: WorldState, text: str) -> None:
    """玩家听到这句话：话里提到的专名都进 known_terms。"""
    for e in refs.refs_in(text):
        if "#" in e:
            continue
        if e not in state.known_terms:
            state.known_terms.append(e)


def learn_term(state: WorldState, entity_id: str) -> None:
    if entity_id and "#" not in entity_id and entity_id not in state.known_terms:
        state.known_terms.append(entity_id)


def player_heard_speech(state: WorldState, event: Event) -> bool:
    """玩家是否听得到这句话的内容（不是耳语，或耳语冲着玩家）。"""
    if event.speech is None or event.witnesses.get(state.player) != "saw":
        return False
    if event.speech.whisper:
        return state.player in event.speech.to or event.actor == state.player
    return True


def learn_terms_from_event(state: WorldState, defs: Defs, event: Event) -> None:
    """专名要听到才知道：被直呼其名、自报家门、话里说到的事实所提到的实体。

    玩家自己说的话不算（K14）：要旨里的 ``⟦id⟧`` 是内核替他拼的，他未必叫得出那个名字。
    """
    if not player_heard_speech(state, event) or event.speech is None:
        return
    if event.actor == state.player:
        return
    sp = event.speech
    for n in sp.names:
        learn_term(state, n)
    for fid in sp.fact_refs:
        f = state.facts.get(fid)
        if f is not None:
            learn_terms_from_text(state, f.text)
    learn_terms_from_text(state, sp.gist)


# ---------------------------------------------------------------- player_view


class ViewEvent(BaseModel):
    id: int
    text: str  # 渲染好的事实句（玩家视角的称呼）
    actor: str | None = None  # 动作者的称呼
    verb: str
    outcome: str
    speech: dict | None = None  # {act, to, stance, gist, spoken_text, whisper}
    by_player: bool = False
    # 新增（K7）：叙述要按 id 找人，不必再回头翻 events_tail
    actor_id: str | None = None  # 动作者的 id
    target_ids: list[str] = []  # 对象的 id：params 里的 target / target2，加上说话的对象
    loc: str = ""  # 事发地点的 id


def event_targets(event: Event) -> list[str]:
    """一条事件牵涉到的对象（人）的 id：target、target2、听话的人。按出现的先后，去重。"""
    out: list[str] = []
    for key in ("target", "target2"):
        v = event.params.get(key)
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    if event.speech is not None:
        for t in event.speech.to:
            if t not in out:
                out.append(t)
    return out


class PlayerView(BaseModel):
    clock: str
    location: str
    location_desc: str
    scene_mode: str
    events: list[ViewEvent] = []
    heard: list[str] = []
    present: list[str] = []
    items_here: list[str] = []
    names: dict[str, str] = {}  # 称呼表：id → 玩家眼里的叫法
    body: list[str] = []
    danger: int = 0
    ended: str | None = None


def _speech_view(state: WorldState, defs: Defs, event: Event) -> dict | None:
    sp = event.speech
    if sp is None:
        return None
    p = state.player
    if not player_heard_speech(state, event):
        return {
            "act": "whisper",
            "to": [refs.display_name(state, defs, t, p) for t in sp.to],
            "whisper": True,
        }
    return {
        "act": sp.act,
        "to": [refs.display_name(state, defs, t, p) for t in sp.to],
        "stance": sp.stance,
        "gist": refs.render(sp.gist, state, defs, p),
        "spoken_text": sp.spoken_text,
        "whisper": sp.whisper,
    }


def body_sensations(state: WorldState, c: Character) -> list[str]:
    out: list[str] = []
    lvl = injury_level(c)
    if lvl == 1:
        out.append("身上的伤处隐隐作痛")
    elif lvl == 2:
        out.append("伤处一阵阵钻心地疼")
    elif lvl >= 3:
        out.append("伤得极重，连挪动一下都难")
    for s in c.statuses:
        if s.kind.startswith("poisoned") and s.until is not None and s.until - state.tick <= 96:
            out.append("腹中一阵阵绞痛")
            break
    if has_status(c, "hungry"):
        out.append("肚子饿得发慌")
    if has_status(c, "exhausted"):
        out.append("浑身乏力，眼皮发沉")
    if has_status(c, "bound"):
        out.append("手脚被捆着，动弹不得")
    elif has_status(c, "captive"):
        out.append("被人看押着，走不脱")
    hidden = get_status(c, "hidden")
    if hidden is not None:
        out.append("你屏住呼吸藏着")
    return out


def player_view(state: WorldState, defs: Defs, events: list[Event]) -> PlayerView:
    """只给叙述调用看玩家此刻看得见、听得见、身上感觉得到的东西。"""
    from tianlong.kernel import danger, timeflow

    p = state.player
    me = state.chars[p]
    loc = state.locations.get(me.loc)
    names: dict[str, str] = {}

    def name(eid: str) -> str:
        n = refs.display_name(state, defs, eid, p)
        names[eid] = n
        return n

    def render(text: str) -> str:
        for e in refs.refs_in(text):
            name(e)
        return refs.render(text, state, defs, p)

    view_events: list[ViewEvent] = []
    heard: list[str] = []
    for ev in events:
        how = ev.witnesses.get(p)
        if how == "saw":
            text = ev.fact_text
            if ev.speech is not None and ev.speech.whisper and not player_heard_speech(state, ev):
                text = ev.params.get("overheard") or text
            view_events.append(
                ViewEvent(
                    id=ev.id,
                    text=render(text),
                    actor=name(ev.actor) if ev.actor else None,
                    verb=ev.verb,
                    outcome=ev.outcome,
                    speech=_speech_view(state, defs, ev),
                    by_player=ev.actor == p,
                    actor_id=ev.actor,
                    target_ids=event_targets(ev),
                    loc=ev.loc,
                )
            )
            for line in ev.params.get("heard_lines", []) if ev.actor == p else []:
                heard.append(render(line))
        elif how == "heard":
            heard.append(render(ev.params.get("sound") or sound_text(ev.loc)))

    present_names = [name(c.id) for c in visible_people(state, me)]
    items_here = [
        name(it.id)
        for it in state.items.values()
        if it.in_world and it.holder is None and it.loc == me.loc and it.feature is None
    ]
    desc = ""
    if loc is not None:
        tod = timeflow.time_of_day(state.tick)
        desc = loc.desc.get(tod) or loc.desc.get("day") or next(iter(loc.desc.values()), "")
        desc = render(desc)
        if loc.traces:
            desc = desc + "".join(
                render(t) + "。" if not t.endswith("。") else render(t) for t in loc.traces[-3:]
            )
    return PlayerView(
        clock=timeflow.clock_text(state.tick),
        location=name(me.loc) if loc is not None else me.loc,
        location_desc=desc,
        scene_mode=state.scene_mode,
        events=view_events,
        heard=heard,
        present=present_names,
        items_here=items_here,
        names=names,
        body=body_sensations(state, me),
        danger=danger.danger(state, defs),
        ended=state.ended,
    )
