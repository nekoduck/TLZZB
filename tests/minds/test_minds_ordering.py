"""T2：同一微轮里的结算次序与结算那一刻的核对。

- 会让情境散场的一招排在同场其他人的招之后（原来每个种子都在第 40 刻记一条
  「setpiece … no longer legal at resolution → wait」：搭话的那一招先结算，把情境散了，道谢落空）。
- 听话的人走开了、要答复的请求不在了：这句话不说，也不让内核记成违规（「speech dropped: … not here」）。
- ★ 的那一步写明了所求之事（do.ask / do.cls），在场时也照它求；指名说给某人的话，他不在就不对满屋子说。
"""

from __future__ import annotations

from tianlong import minds
from tianlong.kernel import actions, resolve
from tianlong.kernel import speech as kspeech
from tianlong.kernel.state import Ctx, Speech
from tianlong.minds import decide, talk
from tianlong.minds.types import MindDecision

BEAM, VICTIM, BULLY = "zhong_ling", "duan_yu", "gong_guangjie"


def _fx(defs, state, effs, self_id=None):
    return resolve.run_effects(state, defs, effs, Ctx(self_id=self_id), source="rule")


def _open_beam(defs, state) -> str:
    _fx(defs, state, [{"set_flag": ["slapped", True]}, {"open": ["beam_girl"]}])
    sid = next(s.id for s in state.situations.values() if s.kind == "beam_girl")
    jeer = actions.build(state, defs, BEAM, "setpiece", {"situation": sid, "name": "jeer"})
    assert jeer is not None and resolve.apply(state, defs, jeer, source="mind")
    return sid


def _move(defs, state, actor: str, sid: str, name: str):
    a = actions.build(state, defs, actor, "setpiece", {"situation": sid, "name": name})
    assert a is not None, name
    return a


async def test_the_move_that_ends_the_situation_resolves_last(world, llm):
    defs, state = world
    sid = _open_beam(defs, state)
    befriend, thank, threaten = (
        _move(defs, state, BEAM, sid, "befriend"),
        _move(defs, state, VICTIM, sid, "thank"),
        _move(defs, state, BULLY, sid, "threaten_beam"),
    )
    assert decide.closes_situation(state, defs, befriend)
    assert not decide.closes_situation(state, defs, thank) and not decide.closes_situation(state, defs, threaten)
    actors = minds.pick_reactors(state, defs, [])
    decisions = await minds.decide_all(llm, state, defs, actors)
    by = {d.actor: d for d in decisions}
    assert by[BEAM].action.params.get("name") == "befriend" and by[BEAM].closing
    assert by[VICTIM].action.params.get("name") == "thank" and not by[VICTIM].closing
    ordered = [d.actor for d in minds.order(state, decisions, [])]
    assert ordered.index(BEAM) > ordered.index(VICTIM) and ordered.index(BEAM) > ordered.index(BULLY)
    verbs = []
    for d in minds.order(state, decisions, []):
        verbs += [e.verb for e in minds.apply_decision(state, defs, d)]
    assert {"thank", "threaten", "befriend"} <= set(verbs)
    assert not [v for v in state.violations if "no longer legal" in v], state.violations
    assert sid not in state.situations  # 搭上了话，这场事散了


def test_two_closing_moves_keep_the_usual_order(world):
    """两招都会散场：仍按「话冲着谁说、当事人、initiative」排（劝说之后客人的赔礼先于耳光）。"""
    defs, state = world
    w = actions.build(state, defs, "ma_wude", "wait", {"ticks": 1})
    ds = [
        MindDecision(actor="ma_wude", action=w.model_copy(update={"actor": "ma_wude"})),
        MindDecision(actor=VICTIM, action=w.model_copy(update={"actor": VICTIM}), closing=True),
        MindDecision(actor=BULLY, action=w.model_copy(update={"actor": BULLY}), closing=True),
    ]
    assert state.chars[BULLY].initiative > state.chars[VICTIM].initiative
    assert [d.actor for d in minds.order(state, ds, [])] == ["ma_wude", BULLY, VICTIM]


def test_speech_to_someone_who_left_is_dropped_without_a_violation(world):
    defs, state = world
    a = actions.build(state, defs, "zuo_zimu", "wait", {"ticks": 1})
    d = MindDecision(actor="zuo_zimu", action=a, speech=Speech(act="taunt", to=["ma_wude"], gist="激对方动手"))
    assert decide.speech_stands(state, defs, d)
    _fx(defs, state, [{"move": ["ma_wude", "jianhu_gate"]}])
    assert not decide.speech_stands(state, defs, d)
    n = len(state.violations)
    evs = minds.apply_decision(state, defs, d)
    assert all(e.verb != "say" for e in evs)
    assert state.violations[n:] == []  # 不记「speech dropped: … not here」


def test_reply_to_a_request_that_is_gone_is_dropped(world):
    defs, state = world
    w = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    ask = Speech(act="request", to=["ma_wude"], ask_action={"verb": "wait", "params": {}}, action_class="trivial")
    evs = resolve.apply(state, defs, w, ask, source="player")
    rid = evs[0].speech.request
    a = actions.build(state, defs, "ma_wude", "wait", {"ticks": 1})
    d = MindDecision(actor="ma_wude", action=a, speech=Speech(act="refuse", to=[state.player], request=rid))
    assert decide.speech_stands(state, defs, d)
    state.requests[rid].status = "expired"
    assert not decide.speech_stands(state, defs, d)
    n = len(state.violations)
    assert all(e.verb != "say" for e in minds.apply_decision(state, defs, d))
    assert state.violations[n:] == []


def test_star_request_uses_the_ask_written_in_the_step(world):
    """打算里写明了所求之事：在场时照它求（原来一律求成「等一等」，对方一口答应，隔一阵又来求）。"""
    defs, state = world
    step = {
        "id": "t_ask",
        "text": "去请⟦ma_wude⟧助一臂之力",
        "do": {"verb": "say", "act": "request", "to": "ma_wude",
               "ask": {"verb": "follow", "params": {"target": "self"}}, "cls": "risky"},
        "takes": 2,
    }
    _fx(defs, state, [{"step": ["zuo_zimu", step]}])
    c = state.chars["zuo_zimu"]
    opts = kspeech.speech_options(state, defs, "zuo_zimu")
    assert (opts.star or {}).get("act") == "request"
    assert talk.step_ask(state, c, "request") == ({"verb": "follow", "params": {"target": "zuo_zimu"}}, "risky")
    sp = talk.star_speech(state, defs, c, opts)
    assert sp is not None and sp.to == ["ma_wude"]
    assert sp.ask_action == {"verb": "follow", "params": {"target": "zuo_zimu"}}
    assert sp.action_class == "risky"
    assert sp.gist == "请对方跟着自己走"
    # 他不在跟前：这句话不对着满屋子的人说
    _fx(defs, state, [{"move": ["ma_wude", "jianhu_gate"]}])
    opts = kspeech.speech_options(state, defs, "zuo_zimu")
    assert talk.star_speech(state, defs, c, opts) is None


def test_reply_gists_name_the_setpiece_move_not_the_verb(world):
    """P3：答应、回绝一桩「情境里的一招」的请求，要旨写那一招的说法，不写「情境」。"""
    defs, state = world
    _fx(defs, state, [{"open": ["bout_laugh"]}])
    sid = next(s.id for s in state.situations.values() if s.kind == "bout_laugh")
    for name in ("feint_win", "laugh"):
        actor = BULLY if name == "feint_win" else VICTIM
        assert resolve.apply(state, defs, _move(defs, state, actor, sid, name), source="mind")
    w = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    ask = {"verb": "setpiece", "params": {"situation": sid, "name": "apologize"}, "desc": "拱手赔个不是"}
    sp = Speech(act="request", to=[VICTIM], whisper=True, gist="劝对方拱手赔个不是", ask_action=ask,
                leverage={"urgent": True})
    evs = resolve.apply(state, defs, w, sp, source="player")
    rid = evs[0].speech.request
    assert state.requests[rid].verdict == "may"
    c = state.chars[VICTIM]
    opts = kspeech.speech_options(state, defs, VICTIM)
    yes = talk.compose(state, defs, c, opts, act="agree", request_ref=rid)
    no = talk.compose(state, defs, c, opts, act="refuse", request_ref=rid)
    assert yes is not None and yes.gist == "答应拱手赔个不是"
    assert no is not None and no.gist.startswith("不肯拱手赔个不是")
    assert talk.describe_do({"verb": "setpiece", "situation": sid, "name": "apologize"}) == "照办"
    assert talk.describe_do({"verb": "give", "item": "x", "target": "a"}, "a") == "把⟦x⟧交给自己"
    from tianlong.minds import render

    line = render.request_line(state, defs, c, state.requests[rid])
    assert "拱手赔个不是" in line and "情境" not in line
