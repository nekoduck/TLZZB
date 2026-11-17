"""答应即照做、被问话的人先答（P1；方案 5.7「有人劝过，他才可能选它」、4.3 的结算顺序）。"""

from __future__ import annotations

from tianlong import minds
from tianlong.kernel import actions, resolve
from tianlong.kernel.state import Speech
from tianlong.minds import decide
from tianlong.minds.types import MindDecision

from .conftest import open_bout

VICTOR, GUEST = "gong_guangjie", "duan_yu"


def _player_says(defs, state, sp: Speech) -> list:
    w = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    assert w is not None
    return resolve.apply(state, defs, w, sp, source="player")


def _setpiece(defs, state, actor: str, sid: str, name: str) -> None:
    a = actions.build(state, defs, actor, "setpiece", {"situation": sid, "name": name})
    assert a is not None, name
    assert resolve.apply(state, defs, a, source="mind")


def _to_the_laugh(defs, state) -> str:
    sid = open_bout(defs, state)
    _setpiece(defs, state, VICTOR, sid, "feint_win")
    _setpiece(defs, state, GUEST, sid, "laugh")
    return sid


def _urge(defs, state, sid: str) -> list:
    sp = Speech(
        act="request",
        to=[GUEST],
        whisper=True,
        gist="拱手赔个不是",
        ask_action={"verb": "setpiece", "params": {"situation": sid, "name": "apologize"}, "desc": "拱手赔个不是"},
        action_class="effort",
        leverage={"urgent": True},
    )
    evs = _player_says(defs, state, sp)
    assert evs and evs[0].speech is not None and evs[0].speech.request
    return evs


def test_order_puts_the_person_spoken_to_first(world):
    defs, state = world
    sid = _to_the_laugh(defs, state)
    evs = _urge(defs, state, sid)
    assert state.chars[VICTOR].initiative > state.chars[GUEST].initiative
    ds = [
        MindDecision(actor=a, action=actions.build(state, defs, a, "wait", {"ticks": 1}))
        for a in ["ma_wude", VICTOR, "zuo_zimu", GUEST]
    ]
    out = [d.actor for d in minds.order(state, ds, evs)]
    assert out[0] == GUEST  # 话是冲着他说的：他先答，别人再出手
    assert out[1] == VICTOR  # 其余当事人次之
    # 没有话冲着谁说：照旧按当事人、initiative
    assert [d.actor for d in minds.order(state, ds, [])][:2] == [VICTOR, GUEST]
    assert decide.addressed(state, evs, state.chars[GUEST].loc) == {GUEST}


async def test_agree_carries_the_asked_action_in_the_same_decision(world, llm):
    defs, state = world
    sid = _to_the_laugh(defs, state)
    evs = _urge(defs, state, sid)
    (req,) = [r for r in state.requests.values() if r.to == GUEST]
    actors = minds.pick_reactors(state, defs, evs)
    assert GUEST in actors and VICTOR in actors
    decisions = await minds.decide_all(llm, state, defs, actors, events=evs)
    by = {d.actor: d for d in decisions}
    g = by[GUEST]
    assert g.speech is not None and g.speech.act == "agree"
    assert g.action.verb == "setpiece" and g.action.params["name"] == "apologize"
    assert by[VICTOR].action.params.get("name") == "slap"  # 他照性子本来要掴这一掌
    out = []
    for d in minds.order(state, decisions, evs):
        out += minds.apply_decision(state, defs, d)
    verbs = [(e.actor, e.verb) for e in out]
    assert (GUEST, "apologize") in verbs and (VICTOR, "slap") not in verbs
    assert verbs.index((GUEST, "say")) < verbs.index((GUEST, "apologize"))
    assert state.flags.get("apologized") and not state.flags.get("slapped")
    assert req.status == "agreed"
    assert not [v for v in state.violations if "rollback" in v]
    # 答应的那一步当场做完，没有留在打算最前面
    ag = state.chars[GUEST].agenda
    assert ag is None or not [s for s in ag.steps if s.id == f"req_{req.id}" and s.state == "pending"]


def test_agreed_action_only_when_the_verdict_allows_and_it_is_legal(world):
    defs, state = world
    sid = _to_the_laugh(defs, state)
    evs = _urge(defs, state, sid)
    rid = evs[0].speech.request
    agree = Speech(act="agree", to=[state.player], request=rid)
    a = decide.agreed_action(state, defs, GUEST, agree)
    assert a is not None and a.params == {"situation": sid, "name": "apologize"}
    assert decide.agreed_action(state, defs, GUEST, Speech(act="refuse", to=[state.player], request=rid)) is None
    assert decide.agreed_action(state, defs, GUEST, None) is None
    # 裁决不许答应：不带动作
    state.requests[rid].verdict = "no"
    assert decide.agreed_action(state, defs, GUEST, agree) is None
    state.requests[rid].verdict = "may"
    # 那一招此刻做不出（耳光已经落下，情境散了）：不带动作
    _setpiece(defs, state, VICTOR, sid, "slap")
    assert decide.agreed_action(state, defs, GUEST, agree) is None


def test_agreeing_to_stop_means_waiting(world):
    defs, state = world
    sp = Speech(
        act="request",
        to=["ma_wude"],
        gist="停下手上的事",
        ask_action={"verb": "wait", "params": {"ticks": 4}, "stop": True, "desc": "停下手上的事"},
        action_class="trivial",
    )
    evs = _player_says(defs, state, sp)
    rid = evs[0].speech.request
    assert state.requests[rid].verdict == "may"
    go = next(a for a in actions.legal_actions(state, defs, "ma_wude") if a.verb != "wait")
    d = MindDecision(actor="ma_wude", action=go, speech=Speech(act="agree", to=[state.player], request=rid))
    kept = decide.keep_word(state, defs, d)
    assert kept.action.verb == "wait" and any("agreed" in n for n in kept.notes)
    out = minds.apply_decision(state, defs, kept)
    assert [e.verb for e in out] == ["say"] and state.requests[rid].status == "agreed"
    # 玩家的抉择不归 minds 管
    mine = MindDecision(actor=state.player, action=go.model_copy(update={"actor": state.player}))
    assert decide.keep_word(state, defs, mine) is mine


async def test_offscreen_decisions_keep_their_own_action(world, llm):
    """离场的答复由 world.offscreen 一次结算：decide_one(offscreen=True) 不把动作换成答应的那件事。"""
    defs, state = world
    sid = _to_the_laugh(defs, state)
    evs = _urge(defs, state, sid)
    d_off = await minds.decide_one(llm, state, defs, GUEST, events=evs, offscreen=True)
    assert d_off.speech is not None and d_off.speech.act == "agree"
    assert d_off.action.params.get("name") != "apologize"
    d_on = await minds.decide_one(llm, state, defs, GUEST, events=evs)
    assert d_on.action.params.get("name") == "apologize" and d_on.closing
