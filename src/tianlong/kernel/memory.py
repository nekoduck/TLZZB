"""记忆的写入（方案 5.6）：salience 表、每人 200 条的上限与淘汰。检索在 ``minds/memory.py``。"""

from __future__ import annotations

from tianlong.kernel.defs import DEFAULT_SALIENCE, Defs
from tianlong.kernel.state import TICKS_PER_DAY, Character, Event, MemoryItem, WorldState

MAX_MEMORIES = 200
OLD_AFTER = 3 * TICKS_PER_DAY  # 早于三日
LOW_SALIENCE = 4

# 事件性质 → salience 表里的键
KIND_TO_KEY: dict[str, str] = {
    "death": "death",
    "harmed": "harmed",
    "captured": "harmed",
    "poisoned": "harmed",
    "rescue": "rescue",
    "promise": "promise",
    "promise_kept": "promise",
    "promise_broken": "promise",
    "exposed": "exposed",
    "deceived": "exposed",
    "insult": "insult",
    "threaten": "insult",
    "caught_stealing": "insult",
    "learn_key": "learn_key",
    "gift": "gift",
    "helped": "gift",
    "taught": "gift",
    "comfort": "talk",
    "nag": "talk",
    "talk": "talk",
    "relieved": "gift",
    "pass": "pass",
    "show_power": "pass",
    "spotted": "pass",
    "item_change": "pass",
}

# 不是当事人、只在一旁看见时的分量（方案没写，取保守值；见 DECISIONS）
BYSTANDER: dict[str, int] = {
    "death": 10,
    "harmed": 6,
    "rescue": 6,
    "promise": 3,
    "exposed": 3,
    "insult": 3,
    "learn_key": 1,
    "gift": 3,
    "talk": 3,
    "pass": 1,
}


def table(defs: Defs) -> dict[str, int]:
    t = dict(DEFAULT_SALIENCE)
    t.update(defs.world.salience or {})
    return t


def kind_salience(defs: Defs, kind: str, *, involved: bool) -> int:
    key = KIND_TO_KEY.get(kind, "pass")
    if involved:
        return table(defs).get(key, 1)
    return BYSTANDER.get(key, 1)


def event_salience(defs: Defs, kinds: list[dict]) -> int:
    """事件本身的分量：各性质按当事人算取最大。"""
    return max((kind_salience(defs, k["k"], involved=True) for k in kinds), default=1)


def salience_for(defs: Defs, kinds: list[dict], witness: str, actor: str | None) -> int:
    best = 1
    for k in kinds:
        involved = witness in (k.get("who"), k.get("by")) or (k.get("who") is None and witness == actor)
        if k["k"] in ("talk", "pass", "show_power", "item_change"):
            involved = True
        best = max(best, kind_salience(defs, k["k"], involved=involved))
    return max(1, min(10, best))


def _evict(c: Character, now: int) -> None:
    mem = c.memory
    over = len(mem) - MAX_MEMORIES
    # 先丢 salience 低于 4 且早于三日的（从最旧的丢起）；反思不丢
    i = 0
    while over > 0 and i < len(mem):
        m = mem[i]
        if m.kind != "reflection" and m.salience < LOW_SALIENCE and now - m.tick > OLD_AFTER:
            del mem[i]
            over -= 1
        else:
            i += 1
    # 仍然超出：按 (salience, tick) 从低到旧丢；反思不丢
    while over > 0:
        cands = [j for j, m in enumerate(mem) if m.kind != "reflection"]
        if not cands:
            break
        j = min(cands, key=lambda k: (mem[k].salience, mem[k].tick))
        del mem[j]
        over -= 1


def write(state: WorldState, c: Character, item: MemoryItem) -> None:
    c.memory.append(item)
    if len(c.memory) > MAX_MEMORIES:
        _evict(c, state.tick)


def write_event(
    state: WorldState,
    defs: Defs,
    c: Character,
    event: Event,
    *,
    kind: str,
    text: str,
    salience: int,
) -> None:
    entities = [e for e in _entities(event) if e != c.id]
    write(
        state,
        c,
        MemoryItem(
            tick=event.tick, kind=kind, text=text, entities=entities, event=event.id, salience=salience
        ),  # type: ignore[arg-type]
    )


def _entities(event: Event) -> list[str]:
    from tianlong.kernel import refs

    out: list[str] = []
    if event.actor:
        out.append(event.actor)
    for e in refs.refs_in(event.fact_text):
        if "#" not in e and e not in out:
            out.append(e)
    for key in ("target", "target2", "item"):
        v = event.params.get(key)
        if isinstance(v, str) and v not in out:
            out.append(v)
    if event.loc not in out:
        out.append(event.loc)
    return out


def bump_salience(c: Character, event_id: int, salience: int) -> None:
    """事件的性质补全以后，把这条记忆的分量调高（MemoryItem 不原地改）。"""
    for i in range(len(c.memory) - 1, -1, -1):
        m = c.memory[i]
        if m.event == event_id:
            if salience > m.salience:
                c.memory[i] = m.model_copy(update={"salience": salience})
            return
