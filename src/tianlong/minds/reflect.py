"""夜间反思（方案 7.10 的 C6）。

时机由 game 定（玩家歇息满 16 刻，或跨日）。对每个活着的有魂角色，自上次反思以来新添的记忆不少于 8 条的，
各调用一次 C6（并行）。反思写成 ``kind`` 为 ``reflection`` 的记忆，印象写进 ``rel[x].impression``，
都经内核的效果 ``reflect`` 与 ``impression``（D-078）。信任、好感这些数字仍然只由规则改动。

「上次反思」的界线取记忆表里最后一条反思的位置（``minds.memory.new_since_reflection``）：
``Character.reflect_mark`` 没有效果能改写它，见交付说明。模型失败时这一夜悄悄跳过。
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from tianlong import prompting
from tianlong.kernel import mood, resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Ctx, Event, MemoryItem, WorldState, is_alive
from tianlong.llm.schemas import Impression, Reflection, ReflectOut
from tianlong.minds import render
from tianlong.minds.memory import new_since_reflection

MIN_NEW = 8  # 新增记忆不少于 8 条才反思（7.10）
MAX_REFLECTIONS = 3
MAX_IMPRESSIONS = 6
REFLECTION_MAX = 60
IMPRESSION_MAX = 30
FEED_MAX = 30  # 送给模型的新记忆至多这么多条（按分量取，再按时间排）
OLD_REFLECTIONS = 3


def due(state: WorldState) -> list[Character]:
    """今夜该反思的人。"""
    return [
        c
        for c in state.chars.values()
        if c.tier == "soul"
        and c.id != state.player
        and is_alive(c)
        and len(new_since_reflection(c)) >= MIN_NEW
    ]


def _feed(c: Character) -> list[MemoryItem]:
    mems = new_since_reflection(c)
    if len(mems) <= FEED_MAX:
        return mems
    keep = sorted(range(len(mems)), key=lambda i: (-mems[i].salience, -i))[:FEED_MAX]
    return [mems[i] for i in sorted(keep)]


def _people(state: WorldState, defs: Defs, c: Character, mems: list[MemoryItem]) -> list[dict[str, str]]:
    seen: list[str] = []
    for m in mems:
        for e in m.entities:
            if e != c.id and e not in seen and (e in state.chars or e in state.items):
                seen.append(e)
    out = []
    for e in seen:
        rel = render.rel_words(c, e) if e in state.chars else ""
        out.append({"id": e, "name": render.name_for(state, defs, c.id, e), "rel": rel})
    return out


def user_text(state: WorldState, defs: Defs, c: Character, mems: list[MemoryItem]) -> str:
    old = [m for m in c.memory if m.kind == "reflection"][-OLD_REFLECTIONS:]
    return prompting.render(
        "c6_user",
        now=timeflow.clock_text(state.tick),
        memories=[f"{render.ago(state.tick, m.tick)}，{render.render_for(m.text, state, defs, c.id)}" for m in mems],
        reflections=[render.render_for(m.text, state, defs, c.id) for m in old],
        people=_people(state, defs, c, mems),
    )


def mock_reflection(state: WorldState, defs: Defs, c: Character, mems: list[MemoryItem]) -> ReflectOut:
    """MockClaude 的回答：拿分量最重的一件事作一条反思；对打交道最多的人写一句按关系档的印象。"""
    if not mems:
        return ReflectOut(reflections=[], impressions=[])
    top = max(mems, key=lambda m: (m.salience, m.tick))
    about = [e for e in top.entities if e in state.chars and e != c.id]
    refl = Reflection(text="这阵子最要紧的是：" + render.clip(top.text, 40), about=about, salience=top.salience)
    counts = Counter(e for m in mems for e in m.entities if e in state.chars and e != c.id)
    imps = []
    if counts:
        who = counts.most_common(1)[0][0]
        r = c.rel.get(who)
        text = f"{mood.trust_text(r.trust)}，{mood.affinity_text(r.affinity)}" if r else "打过交道，还看不透"
        imps.append(Impression(char=who, text=text))
    return ReflectOut(reflections=[refl], impressions=imps)


def effects_for(state: WorldState, c: Character, out: ReflectOut) -> list[dict]:
    """把 C6 的输出校验后翻成内核效果：反思至多三条；about 只留认得出的实体；印象只给别的人。"""
    effs: list[dict] = []
    for r in out.reflections[:MAX_REFLECTIONS]:
        text = render.clip(r.text, REFLECTION_MAX)
        if not text:
            continue
        about = [a for a in dict.fromkeys(r.about) if a in state.chars or a in state.items or a in state.locations]
        effs.append({"reflect": [c.id, text, about, int(r.salience)]})
    seen: set[str] = set()
    for imp in out.impressions:
        if len(seen) >= MAX_IMPRESSIONS:
            break
        if imp.char == c.id or imp.char not in state.chars or imp.char in seen:
            continue
        text = render.clip(imp.text, IMPRESSION_MAX)
        if not text:
            continue
        seen.add(imp.char)
        effs.append({"impression": [c.id, imp.char, text]})
    return effs


async def _one(llm: Any, state: WorldState, defs: Defs, c: Character) -> ReflectOut | None:
    mems = _feed(c)
    if not llm.claude_available:
        return None
    try:
        system = [prompting.load("c6_system"), render.card_block(defs, c.id, state=state)]
        obj, _meta = await llm.claude.call_json(
            "C6",
            system,
            user_text(state, defs, c, mems),
            ReflectOut,
            mock_default=lambda: mock_reflection(state, defs, c, mems),
        )
        return obj
    except Exception:  # noqa: BLE001 — C6 失败：这一夜跳过（7.10）
        return None


async def nightly(llm: Any, state: WorldState, defs: Defs) -> list[Event]:
    """对今夜该反思的人各调一次 C6（并行），结果按人的先后经效果写入。返回效果产生的事件（通常为空）。"""
    people = due(state)
    outs = await asyncio.gather(*(_one(llm, state, defs, c) for c in people))
    events: list[Event] = []
    for c, out in zip(people, outs, strict=True):
        if out is None:
            continue
        effs = effects_for(state, c, out)
        if effs:
            events += resolve.run_effects(state, defs, effs, Ctx(self_id=c.id), source="mind")
    return events


__all__ = ["nightly", "due", "effects_for", "mock_reflection", "MIN_NEW"]
