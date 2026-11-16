"""P2：求人在一场情境里出一招，而那场情境散了——答应的那一步不再卡在打算里；
P3：许诺写的是答应了的那件事；玩家自己打招呼的要旨不写成「自称你」。"""

from __future__ import annotations

from tianlong.kernel import effects, refs, resolve, speech, timeflow

from .conftest import World


def _to_the_laugh(w: World) -> str:
    w.fx([{"set_flag": ["bout_running", True]}, {"open": ["bout_laugh"]}])
    sid = next(iter(w.s.situations))
    for actor, name in (("fighter", "feint_win"), ("guest", "laugh")):
        a = w.build(actor, "setpiece", situation=sid, name=name)
        assert a is not None and resolve.apply(w.s, w.defs, a, source="mind")
    return sid


def _urge(w: World, sid: str) -> str:
    ask = {"verb": "setpiece", "params": {"situation": sid, "name": "apologize"}, "desc": "拱手赔个不是"}
    evs = w.say("player", "request", ["guest"], gist="劝对方拱手赔个不是", ask_action=ask, action_class="trivial")
    assert evs and evs[0].speech is not None and evs[0].speech.request, w.s.violations
    rid = evs[0].speech.request
    w.s.requests[rid].verdict = "may"  # 裁决不是这里要验的
    return rid


def _pending(w: World, cid: str, sid: str) -> list:
    ag = w.c(cid).agenda
    return [s for s in (ag.steps if ag else []) if s.id == sid and s.state == "pending"]


def test_agreed_step_is_voided_when_the_situation_closes(w: World) -> None:
    sid = _to_the_laugh(w)
    rid = _urge(w, sid)
    w.say("guest", "agree", ["player"], request=rid)
    r = w.s.requests[rid]
    assert r.status == "agreed" and _pending(w, "guest", f"req_{rid}")
    (p,) = [p for p in w.s.promises.values() if p.request == rid]
    assert p.status == "open" and p.text == "拱手赔个不是"  # 写的是那件事，不是「答应某人的请求」
    trust = w.c("player").rel["guest"].trust if "guest" in w.c("player").rel else 0
    # 耳光先落下：情境散了，答应的那一步作废，请求过期，许诺静默了结
    slap = w.build("fighter", "setpiece", situation=sid, name="slap")
    assert slap is not None and resolve.apply(w.s, w.defs, slap, source="mind")
    assert sid not in w.s.situations
    assert r.status == "expired"
    assert not _pending(w, "guest", f"req_{rid}")
    step = next(s for s in w.c("guest").agenda.steps if s.id == f"req_{rid}")
    assert step.state == "void"
    assert p.status == "broken"
    # 过了期限也不算失信：不扣信任，不留「答应的事没有做到」
    timeflow.advance_time(w.s, w.defs, 2 * 96)
    after = w.c("player").rel["guest"].trust if "guest" in w.c("player").rel else 0
    assert after >= trust
    assert not any("没有做到" in m.text for m in w.c("player").memory)
    # 他眼下的打算不再被那一步占着
    assert not any(a.star and a.params.get("name") == "apologize" for a in w.legal("guest"))


def test_agreeing_after_the_situation_closed_inserts_nothing(w: World) -> None:
    sid = _to_the_laugh(w)
    rid = _urge(w, sid)
    r = w.s.requests[rid]
    # 请求还没答复，情境先散了：过期
    slap = w.build("fighter", "setpiece", situation=sid, name="slap")
    assert slap is not None and resolve.apply(w.s, w.defs, slap, source="mind")
    assert r.status == "expired" and effects.setpiece_gone(w.s, r.action)
    # 即便有人硬把它当作还开着来答应（旧存档、条件达成的回调）：accept 不插步、不记许诺
    r.status = "open"
    steps_before = len(w.c("guest").agenda.steps) if w.c("guest").agenda else 0
    speech.accept(w.s, w.defs, r)
    assert r.status == "expired"
    assert (len(w.c("guest").agenda.steps) if w.c("guest").agenda else 0) == steps_before
    assert not [p for p in w.s.promises.values() if p.request == rid]


def test_done_step_and_other_requests_are_left_alone(w: World) -> None:
    sid = _to_the_laugh(w)
    rid = _urge(w, sid)
    w.say("guest", "agree", ["player"], request=rid)
    # 另一桩与情境无关的请求
    evs = w.say("player", "request", ["guest"], ask_action={"verb": "show", "params": {"item": "token", "target": "player"}})
    other = evs[0].speech.request
    # 他照做了：赔礼，情境散场；做完的那一步、守了的诺都不动
    apology = w.build("guest", "setpiece", situation=sid, name="apologize")
    assert apology is not None and resolve.apply(w.s, w.defs, apology, source="mind")
    assert sid not in w.s.situations
    assert w.s.requests[rid].status == "agreed"
    step = next(s for s in w.c("guest").agenda.steps if s.id == f"req_{rid}")
    assert step.state == "done"
    assert [p.status for p in w.s.promises.values() if p.request == rid] == ["kept"]
    assert w.s.requests[other].status in ("open", "refused", "agreed", "countered")
    assert not effects.setpiece_gone(w.s, w.s.requests[other].action)


def test_setpiece_gone_matches_instance_or_kind(w: World) -> None:
    sid = _to_the_laugh(w)
    assert not effects.setpiece_gone(w.s, {"verb": "setpiece", "params": {"situation": sid, "name": "x"}})
    assert not effects.setpiece_gone(w.s, {"verb": "setpiece", "params": {"situation": "bout_laugh", "name": "x"}})
    assert effects.setpiece_gone(w.s, {"verb": "setpiece", "params": {"situation": "bout_laugh#9", "name": "x"}})
    assert not effects.setpiece_gone(w.s, {"verb": "wait", "params": {}})
    assert not effects.setpiece_gone(w.s, None)


def test_asked_text_and_player_greeting(w: World) -> None:
    assert speech.asked_text({"verb": "setpiece", "params": {}, "desc": "拱手赔个不是。"}) == "拱手赔个不是"
    assert speech.asked_text({"verb": "follow", "params": {"target": "player"}}, "player") == "跟着自己走"
    evs = w.say("player", "greet", ["guest"])
    assert evs and evs[0].speech.gist == "报了自己的姓名"
    shown = refs.render(evs[0].fact_text, w.s, w.defs, "player")
    assert "自称你" not in shown and shown.startswith("你向")
    evs = w.say("guest", "greet", ["player"])
    assert evs and evs[0].speech.gist == "自称⟦guest⟧"
