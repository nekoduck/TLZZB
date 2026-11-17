"""记忆的检索（方案 5.6）。写入、上限与淘汰在 ``kernel/memory.py``。

不用向量。沿用 Generative Agents 的三项相加，把「相关」换成实体重合度，取前 k 条::

    score = 0.98 ^ 距今的时辰数 + salience / 10 + 2 × |记忆的实体 ∩ 当前实体| / 当前实体数

必带：关于每个在场者的最新一条反思；自己未了的承诺。必带的排在前面，不占 k 的名额。
"""

from __future__ import annotations

from collections.abc import Iterable

from tianlong.kernel import perception
from tianlong.kernel.state import TICKS_PER_HOUR, Character, MemoryItem, WorldState

DECAY = 0.98
TOP_K = 6
PROMISE_SALIENCE = 7  # 许诺的分量（5.6 的表）


def score(m: MemoryItem, now: int, entities: set[str]) -> float:
    """方案 5.6 的检索分。``entities`` 为空时「相关」一项记 0。"""
    hours = max(0, now - m.tick) / TICKS_PER_HOUR
    recency = DECAY**hours
    importance = m.salience / 10
    relevance = 2 * len(set(m.entities) & entities) / len(entities) if entities else 0.0
    return recency + importance + relevance


def present_ids(state: WorldState, c: Character) -> list[str]:
    """此人看得见的在场者。"""
    if not perception.on_map(c):
        return []
    return [o.id for o in perception.visible_people(state, c)]


def latest_reflections(c: Character, people: Iterable[str]) -> list[MemoryItem]:
    """关于每个在场者的最新一条反思（同一条反思只取一次）。"""
    out: list[MemoryItem] = []
    for pid in people:
        for m in reversed(c.memory):
            if m.kind == "reflection" and pid in m.entities:
                if not any(x is m for x in out):
                    out.append(m)
                break
    return out


def open_promises(state: WorldState, c: Character) -> list[MemoryItem]:
    """自己未了的承诺，写成不入库的记忆条目（只给模型看）。"""
    out: list[MemoryItem] = []
    for p in state.promises.values():
        if p.frm != c.id or p.status != "open":
            continue
        text = f"你答应过⟦{p.to}⟧：{p.text}" if p.text else f"你答应过⟦{p.to}⟧一件事，还没做到"
        tick = state.tick
        for ev in reversed(state.events_tail):
            if ev.actor == c.id and ev.verb == "say" and p.to in (ev.speech.to if ev.speech else []):
                tick = ev.tick
                break
        out.append(MemoryItem(tick=tick, kind="did", text=text, entities=[p.to], salience=PROMISE_SALIENCE))
    return out


def must_carry(state: WorldState, c: Character) -> list[MemoryItem]:
    return latest_reflections(c, present_ids(state, c)) + open_promises(state, c)


def retrieve(
    state: WorldState,
    char_id: str,
    entities: Iterable[str],
    k: int = TOP_K,
    *,
    exclude_events: Iterable[int] | None = None,
) -> list[MemoryItem]:
    """必带的条目在前，其后是得分最高的 k 条（同分取较新的）。

    ``exclude_events``：这些事件的记忆不取（例如本轮刚发生、已写在【刚才】里的事）。
    """
    c = state.chars[char_id]
    ents = {e for e in entities if e and e != char_id}
    skip = set(exclude_events or ())
    must = must_carry(state, c)
    rest = [
        m
        for m in c.memory
        if not any(x is m for x in must) and (m.event is None or m.event not in skip)
    ]
    ranked = sorted(
        enumerate(rest), key=lambda im: (-score(im[1], state.tick, ents), -im[1].tick, -im[0])
    )
    return must + [m for _, m in ranked[: max(0, k)]]


def new_since_reflection(c: Character) -> list[MemoryItem]:
    """上次反思以来新添的记忆（不含反思本身）。

    以最后一条反思在记忆表里的位置为界：反思从不被淘汰，记忆只在末尾追加，所以这条界线稳定。
    ``Character.reflect_mark`` 没有效果能改写（见交付说明），这里不读它。
    """
    last = -1
    for i in range(len(c.memory) - 1, -1, -1):
        if c.memory[i].kind == "reflection":
            last = i
            break
    return [m for m in c.memory[last + 1 :] if m.kind != "reflection"]


__all__ = ["retrieve", "score", "must_carry", "new_since_reflection", "TOP_K"]
