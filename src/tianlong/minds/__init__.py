"""人物的心思（方案 4.1）：记忆的检索、抉择（J2 → C2 → 规则）、路人的作息、夜间反思（C6）。

上层（game）这样用（ARCHITECTURE 4.3）::

    actors    = minds.pick_reactors(state, defs, events, limit=settings.limits.max_reactors)
    decisions = await minds.decide_all(llm, state, defs, actors, events=events)
    for d in minds.order(state, decisions, events):
        events += minds.apply_decision(state, defs, d)       # 已不合法的降级为 wait
    minds.memory.retrieve(state, char_id, entities, k=6)
    await minds.reflect.nightly(llm, state, defs)
"""

from tianlong.minds import memory, reflect, render, routines, talk
from tianlong.minds.decide import (
    MAX_REACTORS,
    apply_decision,
    decide_all,
    decide_one,
    is_important,
    order,
    pick_reactors,
)
from tianlong.minds.types import MindDecision

__all__ = [
    "MAX_REACTORS",
    "MindDecision",
    "apply_decision",
    "decide_all",
    "decide_one",
    "is_important",
    "memory",
    "order",
    "pick_reactors",
    "reflect",
    "render",
    "routines",
    "talk",
]
