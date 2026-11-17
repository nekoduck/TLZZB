"""讲述者的节奏（方案 8.5）：张力、停滞、出牌。只看节奏，不决定谁做什么、事情成不成。

- **张力**：每回合一个 0 到 100 的读数 = danger × 20 + 临近的期限（剩不到两日的每条 +20，至多 40）
  + 进行中的情境数 × 10；记下近 6 回合的读数，取均值。
- **停滞**：连续 6 回合（``[limits].stall_turns``），玩家没有获知新事实、没有换地点、牵挂没有新增或了结，
  且张力低于 20。
- **出牌**：停滞时，J6 从此刻条件成立的机会牌里选一张，或者不出。每个游戏日至多 2 张
  （``[limits].cards_per_day``）；张力高于 60 时不出牌。J6 出错这一次就不出牌；
  路由为 rules_only / claude_only 时（J6 没有慢路）按牌的 weight 取一张。

记账全在 ``state.storyteller``（teller 效果）：``tension`` ``tension_hist`` ``quiet`` ``last_loc``
``facts_n`` ``threads_sig`` ``cards_day`` ``cards_today`` ``card_last`` ``cards_used`` ``last_card``
``last_card_tick``。
"""

from __future__ import annotations

from collections.abc import Iterable

from tianlong import prompting
from tianlong.kernel import danger, rng, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, Event, WorldState
from tianlong.llm import LLM, LLMError, sample_choice
from tianlong.storyteller import common, opportunities, threads

WINDOW = 6
STALL_TENSION = 20
NO_CARD_TENSION = 60
NEAR = 2 * TICKS_PER_DAY
NONE = "none"


def tension_now(state: WorldState, defs: Defs) -> int:
    """这一刻的张力读数（不取均值）。"""
    d = danger.danger(state, defs)
    near = 0
    for t in threads.open_threads(state):
        if t.kind == "deadline" and t.expires is not None and 0 <= t.expires - state.tick < NEAR:
            near += 20
    return max(0, min(100, d * 20 + min(near, 40) + len(state.situations) * 10))


def tension(state: WorldState) -> float:
    """近 6 回合的均值（record_turn 记下的）。"""
    return float(state.storyteller.get("tension", 0) or 0)


def record_turn(state: WorldState, defs: Defs, events: Iterable[Event] = ()) -> list[Event]:
    """每回合末调用一次：记下张力读数，更新「几回合没有动静」的计数。"""
    p = common.player(state)
    if p is None:
        return []
    hist = [int(x) for x in common.teller(state, "tension_hist", [])][-(WINDOW - 1) :]
    hist.append(tension_now(state, defs))
    mean = round(sum(hist) / len(hist), 1)
    facts_n = sum(1 for fid in p.beliefs if common.believes(p, fid))
    sig = threads.signature(state)
    last_loc = common.teller(state, "last_loc")
    changed = (
        last_loc is None
        or last_loc != p.loc
        or facts_n > int(common.teller(state, "facts_n", 0) or 0)
        or sig != common.teller(state, "threads_sig", [])
    )
    quiet = 0 if changed else int(common.teller(state, "quiet", 0) or 0) + 1
    return common.book(
        state,
        defs,
        common.teller_effects(
            {
                "tension_hist": hist,
                "tension": mean,
                "quiet": quiet,
                "last_loc": p.loc,
                "facts_n": facts_n,
                "threads_sig": sig,
            }
        ),
    )


def stall_turns(llm: LLM | None) -> int:
    return int(llm.settings.limits.stall_turns) if llm is not None else WINDOW


def stalled(state: WorldState, *, turns: int = WINDOW) -> bool:
    return int(state.storyteller.get("quiet", 0) or 0) >= turns and tension(state) < STALL_TENSION


def cards_left_today(state: WorldState, per_day: int) -> int:
    day = timeflow.day_of(state.tick)
    if common.teller(state, "cards_day") != day:
        return per_day
    return max(0, per_day - int(common.teller(state, "cards_today", 0) or 0))


def j6_state(state: WorldState, defs: Defs) -> str:
    me = state.player
    mine = [
        common.pv(state, defs, ev.fact_text)
        for ev in state.events_tail
        if ev.actor == me and ev.outcome != "blocked" and ev.witnesses.get(me) == "saw"
    ][-WINDOW:]
    return prompting.render(
        "jev/j6_state",
        clock=common.clock(state.tick),
        recent=mine,
        threads=[common.pv(state, defs, threads.model_line(t)) for t in threads.open_threads(state)],
        tension=round(tension(state)),
        quiet=int(state.storyteller.get("quiet", 0) or 0),
    )


async def _choose(llm: LLM, state: WorldState, defs: Defs, cands: list[opportunities.Bound]) -> str | None:
    """J6 选牌；返回牌 id、NONE，或 None（出错，这一次不出）。"""
    route = llm.mode("cards")
    if route in ("rules_only", "claude_only"):
        return rng.pick_weighted(state, {b.card_id: b.weight for b in cands})
    criteria = {b.card_id: b.text for b in cands}
    criteria[NONE] = prompting.load("jev/j6_none")
    q = {"card": {"type": "choice", "instructions": prompting.load("jev/j6_question"), "criteria": criteria}}
    try:
        answers = await llm.jev.ask("J6", j6_state(state, defs), q, hints={"card": cands[0].card_id})
    except LLMError:
        return None
    a = answers.get("card")
    probs = a.probabilities if a is not None and hasattr(a, "probabilities") else {}
    probs = {k: v for k, v in probs.items() if k in criteria}
    if not probs:
        return None
    floor = float(llm.settings.jev.thresholds.sample_floor)
    return sample_choice(probs, lambda: rng.uniform(state), floor)


async def pace(llm: LLM, state: WorldState, defs: Defs) -> list[Event]:
    """停滞时出一张机会牌（或不出）。返回牌带来的事件。"""
    p = common.player(state)
    if p is None or state.ended:
        return []
    if not stalled(state, turns=stall_turns(llm)) or tension(state) > NO_CARD_TENSION:
        return []
    per_day = int(llm.settings.limits.cards_per_day)
    if cards_left_today(state, per_day) <= 0:
        return []
    cands = opportunities.eligible(state, defs)
    if not cands:
        return []
    pick = await _choose(llm, state, defs, cands)
    if pick is None or pick == NONE:
        return []
    bound = next((b for b in cands if b.card_id == pick), None)
    if bound is None:
        return []
    ok, evs = opportunities.fire(state, defs, bound)
    if not ok:
        return []
    day = timeflow.day_of(state.tick)
    today = int(common.teller(state, "cards_today", 0) or 0) if common.teller(state, "cards_day") == day else 0
    last = common.teller(state, "card_last", {})
    last[pick] = state.tick
    values: dict = {
        "cards_day": day,
        "cards_today": today + 1,
        "card_last": last,
        "last_card": pick,
        "last_card_tick": state.tick,
        "quiet": 0,
    }
    if bound.card.get("once"):
        used = common.teller(state, "cards_used", [])
        values["cards_used"] = used + [pick]
    common.book(state, defs, common.teller_effects(values))
    return evs
