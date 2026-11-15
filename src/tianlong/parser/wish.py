"""/心愿（方案 8.4）：把玩家写下的一句话对到人、物、本事、地点上，做成一条 wish 牵挂。

返回的 dict 直接当作 ``{thread: …}`` 效果的参数交给内核（kernel.effects 的 thread 效果分配 id）。
Jev 对每个候选实体问一道是非题；Jev 不可用时按名字匹配。
"""

from __future__ import annotations

from typing import Any

from tianlong import prompting
from tianlong.kernel import perception, refs, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.llm import LLMError
from tianlong.parser import lexical as lx
from tianlong.parser.candidates import model_text, pov
from tianlong.parser.intake import words
from tianlong.parser.stage import strip_stage

MAX_CANDS = 30
MAX_REFS = 4
MAX_TEXT = 60
WISH_WEIGHT = 3


def wish_candidates(state: WorldState, defs: Defs, text: str) -> list[str]:
    """候选实体：名字或叫法对得上的，加上在场的人、身上的东西、玩家已知的专名。"""
    me = state.chars[state.player]
    ids: list[str] = []

    def add(eid: str) -> None:
        if eid and eid != state.player and eid not in ids and len(ids) < MAX_CANDS:
            ids.append(eid)

    pool = [e for e in list(state.chars) + list(state.items) + list(defs.arts) + list(state.locations)
            if e != state.player and (e not in state.items or state.items[e].in_world)]
    for eid, _ in lx.match_entities(state, defs, text, pool):
        add(eid)
    for o in perception.present(state, me.loc, exclude=me.id):
        if perception.visible_to(me, o):
            add(o.id)
    for iid in me.inventory:
        add(iid)
    for eid in state.known_terms:
        if eid in state.chars or eid in state.items or eid in defs.arts or eid in state.locations:
            add(eid)
    return ids


def rule_refs(state: WorldState, defs: Defs, text: str, cands: list[str]) -> list[str]:
    return [eid for eid, _ in lx.match_entities(state, defs, text, cands, floor=3)][:MAX_REFS]


async def map_wish(llm: Any, state: WorldState, defs: Defs, text: str) -> dict:
    """心愿 → ``{kind: 'wish', text, refs, weight}``。永不抛异常。"""
    clean, _ = strip_stage(text)
    clean = clean[:MAX_TEXT]
    try:
        cands = wish_candidates(state, defs, clean)
    except Exception:  # noqa: BLE001
        cands = []
    found = rule_refs(state, defs, clean, cands) if cands else []
    mode = llm.mode("classify") if llm is not None else "rules_only"
    if cands and mode != "rules_only" and llm is not None:
        try:
            w = words()
            me = state.chars[state.player]
            state_text = prompting.render(
                "jev/j1_wish_state",
                clock=timeflow.clock_text(state.tick),
                place=pov(state, defs, refs.ref(me.loc)),
                words=clean,
            )
            qs = {
                f"w{i}": {"type": "noul",
                          "instructions": w["wish"].replace("{事}", model_text(state, defs, refs.ref(eid)))}
                for i, eid in enumerate(cands)
            }
            hints = {f"w{i}": 1.0 for i, eid in enumerate(cands) if eid in found}
            answers = await llm.jev.ask("J1_wish", state_text, qs, hints=hints)
            th = float(llm.settings.jev.thresholds.noul_yes)
            scored = sorted(
                ((float(answers[f"w{i}"].get("noul", 0.0)), i, eid) for i, eid in enumerate(cands)
                 if f"w{i}" in answers),
                key=lambda t: (-t[0], t[1]),
            )
            found = [eid for p, _, eid in scored if p > th][:MAX_REFS]
        except LLMError:
            pass
        except Exception:  # noqa: BLE001 — 心愿对不上不要紧，按名字匹配
            pass
    return {"kind": "wish", "text": clean or "一桩心愿", "refs": found, "weight": WISH_WEIGHT}
