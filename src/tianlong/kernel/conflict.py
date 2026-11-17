"""冲突（方案 6.5）。

- power 高的赢；相同则各半，每多一个帮手加 10，被牵制（impeded）的一方对手加 10。
- 物品 ``props.power_mod`` 加在 power 上。弱者打未被制住的强者，必败；被缚、昏迷、睡着的必败。
- 败者的下场按胜者此刻的意图取：推开、打伤一级、捆起来、取命。取命之前败者掷一次侥幸（2 × luck，不设保底），
  成了就改为重伤，并可再逃一次。
- 只躲不攻且身负 ``evade`` 的守方，对方高出不到 3 级、身边又有路可退时，至多被逼退。
- 身负 ``absorb`` 的一方吸取对方至多那么多内力。
- 亲见动手的人更新对双方的 regard（5.5）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tianlong.kernel import checks, danger, events, growth, perception, rng
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, WorldState, has_status

Intent = Literal["push", "injure", "restrain", "kill"]
EVADE_GAP = 3


class Fight(BaseModel):
    attacker: str
    defender: str
    winner: str
    loser: str
    intent: str
    outcome: str  # push / injure / restrain / kill / saved / buffered
    chance: int | None = None
    roll: int | None = None
    attacker_power: int
    defender_power: int
    absorbed: int = 0
    absorber: str | None = None


def power_mod(state: WorldState, c: Character) -> int:
    return sum(int(state.items[i].props.get("power_mod", 0)) for i in c.inventory if i in state.items)


def effective_power(state: WorldState, defs: Defs, c: Character) -> int:
    return growth.power(state, defs, c) + power_mod(state, c)


def helpers(state: WorldState, side: Character, other: Character) -> int:
    """帮手：在场、清醒、对对方有敌意而对这一方没有敌意的人。"""
    n = 0
    for o in perception.present(state, side.loc):
        if o.id in (side.id, other.id) or not perception.conscious(o) or has_status(o, "bound"):
            continue
        if danger.is_hostile(state, o, other.id) and not danger.is_hostile(state, o, side.id):
            n += 1
    return n


def defender_intent(state: WorldState, d: Character, a: Character) -> Intent:
    """守方赢了以后的意图：想取命就取命；要拿人就捆；有敌意就打伤；否则推开。"""
    if danger.intends_kill(state, d, a.id):
        return "kill"
    if danger.intends(state, d, a.id, ("restrain",)):
        return "restrain"
    if danger.is_hostile(state, d, a.id):
        return "injure"
    return "push"


def can_retreat(state: WorldState, c: Character) -> bool:
    loc = state.locations.get(c.loc)
    return loc is not None and any(not e.edge and not e.hidden and not e.check for e in loc.exits)


def fight(state: WorldState, defs: Defs, attacker: Character, defender: Character, intent: Intent) -> Fight:
    """决出胜负与下场（掷骰在这里），不改状态。状态的改变由 ``outcome_effects`` 给出。"""
    pa, pd = effective_power(state, defs, attacker), effective_power(state, defs, defender)
    chance = roll = None
    if danger.restrained(defender):
        a_wins = True
    elif danger.restrained(attacker):
        a_wins = False
    elif pa != pd:
        a_wins = pa > pd
    else:
        chance = 50 + 10 * (helpers(state, attacker, defender) - helpers(state, defender, attacker))
        if has_status(defender, "impeded"):
            chance += 10
        if has_status(attacker, "impeded"):
            chance -= 10
        chance = checks.clamp(chance)
        roll = rng.roll(state)
        a_wins = roll <= chance
    winner, loser = (attacker, defender) if a_wins else (defender, attacker)
    w_intent: Intent = intent if a_wins else defender_intent(state, defender, attacker)
    outcome: str = w_intent
    if (
        a_wins
        and loser.id == defender.id
        and growth.has_evade(defs, defender)
        and pa - pd < EVADE_GAP
        and can_retreat(state, defender)
        and not danger.restrained(defender)
    ):
        outcome = "push"
    if outcome == "kill":
        save = checks.roll_against(state, "death_save", checks.death_save_chance(loser))
        if save.ok:
            outcome = "saved"
        elif loser.id == state.player and danger.buffered(state, winner.id):
            outcome = "buffered"
    absorbed, absorber = 0, None
    for x, y in ((attacker, defender), (defender, attacker)):
        amt = growth.absorb_amount(defs, x)
        if amt and y.neili > 0:
            absorbed, absorber = min(amt, y.neili), x.id
            break
    return Fight(
        attacker=attacker.id,
        defender=defender.id,
        winner=winner.id,
        loser=loser.id,
        intent=intent,
        outcome=outcome,
        chance=chance,
        roll=roll,
        attacker_power=pa,
        defender_power=pd,
        absorbed=absorbed,
        absorber=absorber,
    )


def caught(state: WorldState, defs: Defs, pursuer: Character, fleer: Character) -> Fight:
    """逃跑失败：被追上，下场按追者的意图取（6.4）。"""
    intent = defender_intent(state, pursuer, fleer)
    outcome: str = intent
    if intent == "kill":
        save = checks.roll_against(state, "death_save", checks.death_save_chance(fleer))
        if save.ok:
            outcome = "saved"
        elif fleer.id == state.player and danger.buffered(state, pursuer.id):
            outcome = "buffered"
    return Fight(
        attacker=fleer.id,
        defender=pursuer.id,
        winner=pursuer.id,
        loser=fleer.id,
        intent=intent,
        outcome=outcome,
        attacker_power=growth.power(state, defs, fleer),
        defender_power=growth.power(state, defs, pursuer),
    )


OUTCOME_TEXT = {
    "push": "⟦{w}⟧把⟦{l}⟧逼退了几步",
    "injure": "⟦{l}⟧挨了一下，受了伤",
    "restrain": "⟦{w}⟧制住了⟦{l}⟧，把人捆了起来",
    "kill": "⟦{l}⟧倒在地上，再也没有起来",
    "saved": "⟦{l}⟧被重手击中，侥幸没有送命",
    "buffered": "⟦{l}⟧被重手击中，伤得极重",
}


def outcome_effects(f: Fight) -> list[dict]:
    """下场的效果（在胜者的上下文里执行，self = 胜者）。"""
    w, loser = f.winner, f.loser
    out: list[dict] = []
    if f.outcome == "push":
        out.append({"add_status": [loser, "impeded", {"ticks": 2, "source": w}]})
    elif f.outcome == "injure":
        out.append({"injure": [loser, 1, w]})
    elif f.outcome == "restrain":
        out.append({"add_status": [loser, "bound", {"source": w}]})
    elif f.outcome == "saved":
        out.append({"injure": [loser, 3, w]})
        out.append({"add_status": [loser, "alerted", {"ticks": 2, "source": w}]})
        # 「可再逃一次」（6.5）：重伤本来不能走动（6.9），这 2 刻里许他逃一次（actions 的 flee）
        out.append({"add_status": [loser, "reprieve", {"ticks": 2, "source": w}]})
    elif f.outcome in ("kill", "buffered"):
        out.append({"kill": [loser, w]})
    if f.absorbed and f.absorber:
        other = f.defender if f.absorber == f.attacker else f.attacker
        out.append({"stat": [f.absorber, "neili", f.absorbed]})
        out.append({"stat": [other, "neili", -f.absorbed]})
    return out


def fight_text(f: Fight, lead: str) -> str:
    return lead + "。" + OUTCOME_TEXT[f.outcome].format(w=f.winner, l=f.loser)


def fight_kinds(f: Fight) -> list[dict]:
    return [
        events.kind("show_power", who=f.attacker),
        events.kind("show_power", who=f.defender),
        events.kind("harmed", who=f.loser, by=f.winner) if f.outcome != "push" else events.kind("pass"),
    ]
