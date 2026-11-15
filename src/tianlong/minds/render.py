"""给模型看的文字：J2 的状态（方案 7.3）、C2 与 C6 的人物卡块（方案 7.6、7.10）。

人物看世界用自己的眼睛：认识的人写真名，不认识的人写叫法（``unknown_alias``），自己写「你」。
「认识」= 关系表里有他（打过交道，或开局就认得）、上下级、有共同的身份（同门、同伙、同为座上客）、
或自己相信的事实里提到过他。
地点与本事一律写真名（本地人都知道）；物件认得的写真名，否则写叫法。
情绪与关系只给档位文字（方案 5.5），不给数字。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from tianlong import prompting
from tianlong.kernel import danger, growth, mood, perception, predicates, refs, timeflow
from tianlong.kernel import speech as kspeech
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import (
    TICKS_PER_DAY,
    TICKS_PER_HOUR,
    Character,
    Event,
    MemoryItem,
    Request,
    WorldState,
    has_status,
    injury_level,
)

STATE_MAX = 900  # J2 的状态 600 到 900 字（7.3）；超了先删记忆，再删【刚才】
MEMORY_TEXT_MAX = 60
RECENT_MAX = 6
YOU = "你"


# ---------------------------------------------------------------- names


def knows_person(state: WorldState, viewer: Character, other_id: str) -> bool:
    if other_id == viewer.id or other_id in viewer.rel:
        return True
    o = state.chars.get(other_id)
    if o is None:
        return False
    if viewer.superior == other_id or o.superior == viewer.id or set(viewer.roles) & set(o.roles):
        return True
    for fid, b in viewer.beliefs.items():
        if b.confidence < predicates.BELIEVE:
            continue
        f = state.facts.get(fid)
        if f is not None and f"⟦{other_id}⟧" in f.text:
            return True
    return False


def knows_item(state: WorldState, viewer: Character, item_id: str) -> bool:
    if item_id in viewer.inventory:
        return True
    for fid, b in viewer.beliefs.items():
        if b.confidence < predicates.BELIEVE:
            continue
        f = state.facts.get(fid)
        if f is not None and f"⟦{item_id}⟧" in f.text:
            return True
    return False


def name_for(state: WorldState, defs: Defs, viewer_id: str | None, entity_id: str) -> str:
    """``viewer_id`` 眼里这个实体叫什么。``viewer_id`` 为空时用真名。"""
    if viewer_id is None:
        return refs.display_name(state, defs, entity_id)
    if viewer_id == state.player:
        return refs.display_name(state, defs, entity_id, viewer_id)
    if entity_id == viewer_id:
        return YOU
    viewer = state.chars.get(viewer_id)
    found = refs.lookup(state, defs, entity_id)
    if found is None or viewer is None:
        return found[0] if found else entity_id
    name, alias = found
    if entity_id in state.chars:
        return name if knows_person(state, viewer, entity_id) else alias
    if entity_id in state.items:
        return name if knows_item(state, viewer, entity_id) else alias
    return name


def render_for(text: str, state: WorldState, defs: Defs, viewer_id: str | None) -> str:
    """渲染文字里的 ``⟦id⟧``，按 ``viewer_id`` 的眼睛。"""
    return refs.REF_RE.sub(lambda m: name_for(state, defs, viewer_id, m.group(1)), text or "")


def clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- pieces


def ago(now: int, tick: int) -> str:
    d = max(0, now - tick)
    if d <= 1:
        return "刚才"
    if d < TICKS_PER_HOUR:
        return "不久前"
    if d < TICKS_PER_DAY:
        n = d // TICKS_PER_HOUR
        return f"{'两' if n == 2 else timeflow.cn_num(n)}个时辰前"
    n = d // TICKS_PER_DAY
    return f"{'两' if n == 2 else timeflow.cn_num(n)}日前"


def who_text(state: WorldState, defs: Defs, c: Character) -> str:
    card = defs.card_for(c.persona) or defs.cards.get(c.id)
    if card is None:
        return f"{c.name}，{c.unknown_alias}。"
    intro = render_for(card.self_intro, state, defs, c.id).strip().rstrip("。")
    vals = "；".join(card.values)
    out = c.name + ("，" + intro if intro else "") + "。"
    return out + (vals + "。" if vals else "")


def now_text(state: WorldState, defs: Defs, c: Character) -> str:
    place = name_for(state, defs, c.id, c.loc)
    tense = state.scene_mode == "tense" and with_player(state, c)
    return f"{timeflow.clock_text(state.tick).replace(' ', '')}，{place}。{'场面紧张' if tense else '场面平静'}。"


def with_player(state: WorldState, c: Character) -> bool:
    p = state.chars.get(state.player)
    return p is not None and p.loc == c.loc


def _status_words(o: Character) -> list[str]:
    out = []
    if has_status(o, "dead"):
        return ["已经死了"]
    if has_status(o, "unconscious"):
        out.append("昏迷不醒")
    elif has_status(o, "asleep"):
        out.append("睡着")
    if has_status(o, "bound"):
        out.append("被捆着")
    elif has_status(o, "captive"):
        out.append("被人扣着")
    lvl = injury_level(o)
    if lvl >= 3:
        out.append("伤得很重")
    elif lvl >= 1:
        out.append("受了伤")
    return out


def rel_words(c: Character, other: str) -> str:
    r = c.rel.get(other)
    if r is None:
        return "素不相识"
    words = []
    if abs(r.trust) >= abs(r.affinity):
        words.append(mood.trust_text(r.trust))
        if abs(r.affinity) >= 10:
            words.append(mood.affinity_text(r.affinity))
    else:
        words.append(mood.affinity_text(r.affinity))
        words.append(mood.trust_text(r.trust))
    if r.fear >= 20:
        words.append(mood.fear_text(r.fear))
    return "、".join(dict.fromkeys(words))


def regard_words(state: WorldState, defs: Defs, c: Character, other: str) -> str:
    r = c.rel.get(other)
    regard = r.regard if r is not None else 0
    mine = growth.power(state, defs, c)
    if regard > mine:
        return mood.relative_regard_text(regard, mine)
    return mood.regard_text(regard)


def person_text(state: WorldState, defs: Defs, c: Character, o: Character) -> str:
    rw = rel_words(c, o.id)
    if o.id not in c.rel and knows_person(state, c, o.id):
        rw = "不熟"
    parts = [rw, regard_words(state, defs, c, o.id)]
    if c.superior == o.id:
        parts.append("你听命于他")
    elif o.superior == c.id:
        parts.append("听命于你")
    parts += _status_words(o)
    r = c.rel.get(o.id)
    if r is not None and r.impression:
        parts.append("印象：" + clip(render_for(r.impression, state, defs, c.id), 20))
    return f"{name_for(state, defs, c.id, o.id)}（{'；'.join(parts)}）"


def present_text(state: WorldState, defs: Defs, c: Character) -> str:
    people = perception.visible_people(state, c)
    if not people:
        return "只有你一个人"
    return "｜".join(person_text(state, defs, c, o) for o in people)


def event_line(state: WorldState, defs: Defs, c: Character, ev: Event) -> str | None:
    how = ev.witnesses.get(c.id)
    if how is None:
        return None
    if how == "heard":
        return render_for(str(ev.params.get("sound") or perception.sound_text(ev.loc)), state, defs, c.id)
    text = ev.fact_text
    sp = ev.speech
    if sp is not None and sp.whisper and c.id not in sp.to and c.id != ev.actor:
        text = str(ev.params.get("overheard") or text)
    line = render_for(text, state, defs, c.id).rstrip("。")
    judged = (ev.params.get("judgements") or {}).get(c.id)
    if judged:
        line += "（" + "；".join(render_for(j, state, defs, c.id) for j in judged) + "）"
    return line + "。"


def recent_lines(state: WorldState, defs: Defs, c: Character, events: Sequence[Event]) -> list[str]:
    evs = list(events)
    if not evs:
        evs = [e for e in state.events_tail if e.tick >= state.tick - 1 and c.id in e.witnesses]
    out = []
    for ev in evs:
        line = event_line(state, defs, c, ev)
        if line and line not in out:
            out.append(line)
    return out[-RECENT_MAX:]


def memory_line(state: WorldState, defs: Defs, c: Character, m: MemoryItem) -> str:
    text = clip(render_for(m.text, state, defs, c.id), MEMORY_TEXT_MAX)
    if m.kind == "reflection":
        return f"（你的想法）{text}"
    return f"{ago(state.tick, m.tick)}，{text}"


def condition_text(c: Character) -> str:
    parts = [mood.mood_text(c)]
    lvl = injury_level(c)
    parts.append(("没有受伤", "受了点轻伤", "伤得不轻", "伤重，走不动")[min(3, lvl)])
    if has_status(c, "bound"):
        parts.append("被捆着")
    elif has_status(c, "captive"):
        parts.append("被人扣着")
    if any(s.kind.startswith("poisoned") for s in c.statuses):
        parts.append("中了毒")
    if has_status(c, "hidden"):
        parts.append("藏身暗处，没人看见你")
    if has_status(c, "exhausted"):
        parts.append("很疲惫")
    if has_status(c, "hungry"):
        parts.append("饿了")
    return "。".join(parts) + "。"


def plan_text(state: WorldState, defs: Defs, c: Character) -> str:
    ag = c.agenda
    if ag is None or ag.status == "done":
        return "没有特别的打算。"
    out = render_for(ag.goal, state, defs, c.id).rstrip("。")
    st = danger.current_step(c)
    if ag.status == "waiting" and ag.waiting_for:
        out += f"（在等：{render_for(ag.waiting_for, state, defs, c.id)}）"
    elif st is not None:
        out += "；眼下：" + render_for(st.text, state, defs, c.id).rstrip("。")
    return out + "。"


def ask_text(state: WorldState, defs: Defs, r: Request) -> str:
    from tianlong.minds.talk import describe_do

    params = dict(r.action.get("params") or {})
    if r.kind == "question":
        about = params.get("about")
        return f"打听{'⟦' + about + '⟧的事' if isinstance(about, str) and about else '一件事'}"
    return describe_do({"verb": r.action.get("verb"), **params})


def request_line(state: WorldState, defs: Defs, c: Character, r: Request) -> str:
    from tianlong.minds.talk import describe_do

    if r.frm == c.id:
        cond = describe_do({"verb": (r.condition or {}).get("verb"), **((r.condition or {}).get("params") or {})})
        text = f"你求⟦{r.to}⟧{ask_text(state, defs, r)}，对方开了条件：要你{cond}"
        return render_for(text, state, defs, c.id)
    verb = {"request": "请你", "plead": "求你", "order": "命你", "question": "向你"}.get(r.kind, "请你")
    if r.kind == "question":
        text = f"⟦{r.frm}⟧{verb}{ask_text(state, defs, r)}"
    else:
        text = f"⟦{r.frm}⟧{verb}{ask_text(state, defs, r)}（{kspeech.VERDICT_TEXT.get(r.verdict, '')}）"
    return render_for(text, state, defs, c.id)


def incoming(state: WorldState, c: Character) -> list[Request]:
    return kspeech.open_requests_to(state, c.id) + kspeech.countered_from(state, c.id)


def requests_text(state: WorldState, defs: Defs, c: Character) -> str:
    rs = incoming(state, c)
    if not rs:
        return "无"
    return "；".join(request_line(state, defs, c, r) for r in rs)


def scene_entities(state: WorldState, c: Character, events: Iterable[Event]) -> list[str]:
    """检索记忆用的「当前场景与话题的实体」：在场的人、所在地点、本轮事件里出现的实体。"""
    out: list[str] = [o.id for o in perception.visible_people(state, c)] + [c.loc]
    for ev in events:
        if c.id not in ev.witnesses:
            continue
        if ev.actor:
            out.append(ev.actor)
        out += [e for e in refs.refs_in(ev.fact_text) if "#" not in e]
        for k in ("target", "target2", "item"):
            v = ev.params.get(k)
            if isinstance(v, str):
                out.append(v)
    return [e for e in dict.fromkeys(out) if e != c.id]


# ---------------------------------------------------------------- the J2 state


def state_text(
    state: WorldState,
    defs: Defs,
    char_id: str,
    events: Sequence[Event] = (),
    *,
    memories: Sequence[MemoryItem] | None = None,
    limit: int = STATE_MAX,
) -> str:
    """J2a、J2b、C2 共用的状态（方案 7.3 的八个小节，顺序照抄）。"""
    from tianlong.minds import memory as mmem

    c = state.chars[char_id]
    if memories is None:
        skip = [ev.id for ev in events]
        memories = mmem.retrieve(state, char_id, scene_entities(state, c, events), exclude_events=skip)
    mem_lines = [memory_line(state, defs, c, m) for m in memories]
    recent = recent_lines(state, defs, c, events)
    fixed = {
        "who": who_text(state, defs, c),
        "now": now_text(state, defs, c),
        "present": present_text(state, defs, c),
        "status": condition_text(c),
        "plan": plan_text(state, defs, c),
        "requests": requests_text(state, defs, c),
    }

    def build() -> str:
        return prompting.render(
            "jev/j2_state",
            recent="".join(recent) if recent else "无",
            memories=mem_lines,
            **fixed,
        )

    text = build()
    while len(text) > limit and mem_lines:
        mem_lines.pop()
        text = build()
    while len(text) > limit and len(recent) > 1:
        recent.pop(0)
        text = build()
    return text


# ---------------------------------------------------------------- the card block


def _plain(text: str, defs: Defs, state: WorldState | None) -> str:
    """全知视角的真名（人物卡不分谁在看）。"""
    if state is not None:
        return refs.render(text, state, defs)

    def sub(m: object) -> str:
        eid = m.group(1)  # type: ignore[attr-defined]
        t = defs.lexicon.names.get(eid) or defs.lexicon.terms.get(eid)
        return t.name if t is not None else eid

    return refs.REF_RE.sub(sub, text or "")


def card_block(defs: Defs, char_id: str, *, state: WorldState | None = None) -> str:
    """C2 的 system 第二块（方案 7.6）：自述、看重什么、底线、说话习惯、原著里做过的抉择。卡里没有「将来」。"""
    persona = char_id
    if state is not None and char_id in state.chars:
        persona = state.chars[char_id].persona or char_id
    card = defs.cards.get(persona) or defs.cards.get(char_id)
    if card is None:
        name = state.chars[char_id].name if state is not None and char_id in state.chars else char_id
        return prompting.render(
            "c2_card", name=name, intro="", values=[], bottom_lines=[], habits=[], choices=[]
        )
    return prompting.render(
        "c2_card",
        name=card.name,
        intro=_plain(card.self_intro, defs, state).strip(),
        values=list(card.values),
        bottom_lines=[b.text for b in card.bottom_lines if b.text],
        habits=list(card.voice.habits),
        choices=[{"situation": ch.situation, "did": ch.did} for ch in card.choices if ch.situation or ch.did],
    )


__all__ = [
    "state_text",
    "card_block",
    "render_for",
    "name_for",
    "knows_person",
    "person_text",
    "event_line",
    "scene_entities",
    "requests_text",
    "incoming",
    "clip",
]
