"""开口的选项（方案 2.1、2.3、8.4）：真内容、Mock 模型。

一句话 = 内核的 ``Speech`` + 1 刻的 ``wait``，经 ``resolve.apply`` 结算；这里连同内核与 ``minds`` 一起走到底。
"""

from __future__ import annotations

import json

import pytest

from tianlong import minds
from tianlong.kernel import actions, refs, resolve
from tianlong.kernel import speech as kspeech
from tianlong.kernel.state import Speech
from tianlong.storyteller import common, options, speaking, threads
from tianlong.storyteller.options import Option, note_action, note_choice, pick_options
from tianlong.world import situations as world_situations

from .conftest import World

GUEST, VICTOR, BOSS = "duan_yu", "gong_guangjie", "ma_wude"


async def _opts(llm, w) -> list[Option]:
    threads.update(w.s, w.defs, [])
    return await pick_options(llm, w.s, w.defs)


def _cands(w) -> list[Option]:
    threads.update(w.s, w.defs, [])
    return options.candidates(w.s, w.defs)[1]


def _spoken(w) -> list[Option]:
    return [o for o in _cands(w) if o.speech is not None]


def _shown(w, o: Option) -> str:
    return common.pv(w.s, w.defs, o.desc)


def _bout(w) -> str:
    return next(s.id for s in w.s.situations.values() if s.kind == "bout_laugh")


def _setpiece(w, actor: str, name: str) -> None:
    a = actions.build(w.s, w.defs, actor, "setpiece", {"situation": _bout(w), "name": name})
    assert a is not None, (actor, name)
    assert resolve.apply(w.s, w.defs, a, source="mind")


def _to_the_laugh(w) -> None:
    """开局那场比剑走到客人失声一笑：胜者使诈赢了一招，客人笑出了声，耳光还没落下。"""
    w.open_bout()
    _setpiece(w, VICTOR, "feint_win")
    _setpiece(w, GUEST, "laugh")
    assert w.s.flags.get("guest_laughed") and not w.s.flags.get("slapped")


def _say(w, o: Option):
    assert o.speech is not None
    before = list(w.s.violations)
    evs = resolve.apply(w.s, w.defs, o.action, o.speech, source="player")
    assert w.s.violations == before
    assert evs and all(e.outcome != "blocked" for e in evs), [e.fact_text for e in evs]
    return evs


def _find(cands: list[Option], act: str, to: str | None = None) -> list[Option]:
    return [o for o in cands if o.speech is not None and o.speech.act == act and (to is None or o.speech.to == [to])]


def _check_knowledge(w, cands: list[Option]) -> None:
    """开口的候选不露玩家不知道的专名，说的事都是他相信的，提到的人都听得见。"""
    unknown = common.unknown_names(w.s, w.defs)
    me = w.p
    heard = {c.id for c in kspeech.audience(w.s, me)}
    for o in cands:
        sp = o.speech
        if sp is None:
            continue
        assert o.action.verb == "wait" and o.action.params == {"ticks": 1}
        assert speaking.sayable(w.s, w.defs, sp)
        assert sp.to and set(sp.to) <= heard
        for text in (o.desc, sp.gist, str((sp.ask_action or {}).get("desc") or "")):
            shown = common.pv(w.s, w.defs, text)
            assert "⟦" not in shown, shown
            assert not any(n in shown for n in unknown), shown
        # 说出口的要旨里只带玩家叫得出名字的实体
        for eid in refs.refs_in(sp.gist):
            assert eid == w.s.player or eid in w.s.known_terms, (eid, sp.gist)
        for fid in sp.fact_refs:
            assert common.believes(me, fid), fid
        # 不许诺结果：只写打算
        assert not any(k in _shown(w, o) for k in ("成功", "果然", "于是", "终于")), _shown(w, o)


# ---------------------------------------------------------------- 开局：劝客人赔个不是


async def test_before_the_laugh_no_apology_is_hinted(llm, w):
    """比剑刚开场，客人还没笑：有开口的选项，但不会提前给「劝他赔不是」（那等于剧透）。"""
    w.open_bout()
    spoken = _spoken(w)
    assert spoken, "开局应当有话可说"
    assert not [o for o in spoken if (o.speech.ask_action or {}).get("verb") == "setpiece"]
    assert not any("赔" in _shown(w, o) for o in spoken)
    opts = await _opts(llm, w)
    assert len(opts) == 3 and len({o.tag for o in opts}) == 3
    assert any(o.speech is not None and o.tag in ("人物", "介入") for o in opts)
    _check_knowledge(w, spoken)


async def test_persuasion_opens_the_apology_end_to_end(llm, w):
    """方案 5.7：赔礼那一招没有 lean，「有人劝过，他才可能选它」。

    笑声一出，三个选项里就有一句「低声劝……赔个不是」（介入）；选了它，经 resolve.apply 成了一桩请求；
    客人答应，同一个抉择里就赔了不是（答应即照做）；耳光不再落下。
    """
    _to_the_laugh(w)
    sid = _bout(w)
    opts = await _opts(llm, w)
    assert len(opts) == 3 and len({o.tag for o in opts}) == 3
    pick = next(o for o in opts if o.speech is not None and o.tag == "介入")
    sp = pick.speech
    assert sp.act == "request" and sp.to == [GUEST] and sp.whisper
    assert sp.ask_action["verb"] == "setpiece"
    assert sp.ask_action["params"] == {"situation": sid, "name": "apologize"}
    assert sp.leverage == {"urgent": True}  # 耳光眼看要落下：这句劝正帮到他的急处
    assert _shown(w, pick) == "低声劝段公子拱手赔个不是"
    assert pick.rule >= 50 and f"ev:{sid}" in pick.thread_ids
    assert pick is opts[0]  # 眼前插得上手的事排在最前

    def apology():
        return next(
            (a for a in actions.legal_actions(w.s, w.defs, GUEST) if a.params.get("name") == "apologize"), None
        )

    assert apology() is not None and not apology().star  # 做得出，但不是他性子里会出的
    evs = _say(w, pick)
    note_choice(w.s, w.defs, pick)
    (req,) = [r for r in w.s.requests.values() if r.frm == w.s.player and r.to == GUEST]
    assert req.status == "open" and req.verdict == "may"
    assert req.action["params"] == {"situation": sid, "name": "apologize"}
    assert evs[0].speech is not None and evs[0].speech.act == "request"

    # 客人掂量之后答应（Mock 的抉择照内核的裁决）；答应了就做：同一个抉择里赔了不是（P1）
    d = await minds.decide_one(llm, w.s, w.defs, GUEST, events=evs)
    assert d.speech is not None and d.speech.act == "agree" and d.speech.request == req.id
    assert d.action.verb == "setpiece" and d.action.params == {"situation": sid, "name": "apologize"}
    evs2 = minds.apply_decision(w.s, w.defs, d)
    assert [e.verb for e in evs2] == ["say", "apologize"]
    assert req.status == "agreed"
    # 答应的那一步当场做完，不留在打算里；这场事了结，耳光没有落下
    assert not [st for st in w.s.chars[GUEST].agenda.steps if st.id == f"req_{req.id}" and st.state == "pending"]
    assert w.s.flags.get("apologized") and not w.s.flags.get("slapped")
    assert not [v for v in w.s.violations if "rollback" in v]
    world_situations.end_of_turn(w.s, w.defs)
    assert sid not in w.s.situations
    assert not [s for s in w.s.chars[GUEST].statuses if s.kind == "injured"]


async def test_persuasion_is_not_repeated_once_asked(llm, w):
    _to_the_laugh(w)
    pick = next(o for o in await _opts(llm, w) if o.speech is not None and o.tag == "介入")
    _say(w, pick)
    note_choice(w.s, w.defs, pick)
    assert w.s.storyteller["last_chosen"]["sig"] == pick.signature
    assert w.s.storyteller["recent_chosen"][-1][1:3] == ["say.request", f"to={GUEST}"]
    again = _spoken(w)
    asks = [(o.speech.ask_action or {}).get("params") for o in again]
    assert pick.speech.ask_action["params"] not in asks  # 那桩请求还开着：不再给同一句
    assert pick.signature not in {o.signature for o in await _opts(llm, w)}


async def test_warning_and_pleading_inside_the_running_situation(w):
    """眼前有人正要对客人下手：提醒他（示警）、替他向动手的人求情，都算「介入」。"""
    _to_the_laugh(w)
    spoken = _spoken(w)
    (warn,) = _find(spoken, "warn", GUEST)
    assert warn.tag == "介入" and warn.rule >= 50 and warn.speech.whisper
    assert _shown(w, warn) == "低声提醒段公子当心场中舞剑的青年"
    plea = next(o for o in spoken if (o.speech.ask_action or {}).get("stop") and o.speech.to == [VICTOR])
    assert plea.tag == "介入" and plea.speech.act in ("request", "plead")
    assert plea.speech.ask_action["verb"] == "wait"  # 「住手」：与解析器写这类请求的办法相同
    _check_knowledge(w, spoken)
    # 提醒过了，他有了防备：一个时辰之内不再给
    _say(w, warn)
    assert not _find(_spoken(w), "warn", GUEST)


# ---------------------------------------------------------------- 各类话


def test_greeting_asking_and_telling_at_the_opening(w):
    spoken = _spoken(w)
    acts = {o.speech.act for o in spoken}
    assert {"greet", "ask", "tell", "request"} <= acts
    assert all(o.tag in options.TAGS for o in spoken)
    # 招呼：只对还没打过交道的人；东家是熟人，不必招呼
    greeted = {o.speech.to[0] for o in _find(spoken, "greet")}
    assert greeted and BOSS not in greeted
    assert all(w.s.player not in w.s.chars[c].rel for c in greeted)
    # 打听：问一个不知其名的人的来历
    origin = next(o for o in _find(spoken, "ask") if o.speech.about == o.speech.to[0])
    assert origin.speech.to[0] not in w.s.known_terms and origin.tag == "人物" and origin.rule == 30
    # 相求：向东家讨教本事（成长）
    teach = next(o for o in _find(spoken, "request", BOSS))
    assert teach.speech.ask_action["verb"] == "teach" and teach.tag == "成长"
    _check_knowledge(w, spoken)


def test_greeting_is_offered_once(w):
    first = _find(_spoken(w), "greet")[0]
    who = first.speech.to[0]
    _say(w, first)
    assert who not in {o.speech.to[0] for o in _find(_spoken(w), "greet")}


def test_only_believed_facts_are_told(w):
    """玩家不相信的事不说；信了才出现在选项里；对方已经知道的事不再说。"""
    w.fx([{"fact": {"id": "F_test_debt", "text": "⟦ma_wude⟧欠着⟦zuo_zimu⟧一笔旧账", "known_by": ["gan_guanghao"]}}])
    assert "F_test_debt" not in {f for o in _spoken(w) for f in o.speech.fact_refs}
    w.fx([{"learn": ["player", "F_test_debt", 0.9, "told:gan_guanghao"]}])
    told = [o for o in _spoken(w) if "F_test_debt" in o.speech.fact_refs]
    assert told and all(o.speech.act == "tell" for o in told)
    assert {o.speech.to[0] for o in told} <= {"zuo_zimu", BOSS}
    assert "gan_guanghao" not in {o.speech.to[0] for o in told}  # 告诉你这件事的人不必再听一遍
    _check_knowledge(w, told)
    one = told[0]
    _say(w, one)
    assert one.signature not in {o.signature for o in _spoken(w)}
    # 半信半疑的事不拿去说
    w.fx([{"fact": {"id": "F_test_rumor", "text": "⟦zuo_zimu⟧暗地里另有打算"}},
          {"learn": ["player", "F_test_rumor", 0.3, "inferred"]}])
    assert "F_test_rumor" not in {f for o in _spoken(w) for f in o.speech.fact_refs}


def test_danger_fact_becomes_a_warning(w):
    w.fx([{"fact": {"id": "F_test_trap", "text": "有人要在山道上对⟦ma_wude⟧不利", "tags": ["danger"]}},
          {"learn": ["player", "F_test_trap", 0.9, "saw"]}])
    (warn,) = [o for o in _spoken(w) if "F_test_trap" in o.speech.fact_refs]
    assert warn.speech.act == "warn" and warn.speech.to == [BOSS]
    assert "T_escort_home" in warn.thread_ids and warn.tag == "推进"


def test_unknown_names_never_leak_into_what_is_said(w):
    """说到一个不知其名的人：要旨里写的是玩家眼里的叫法，不是能还原出真名的 ⟦id⟧。"""
    w.fx([{"add_status": [GUEST, "captive", {"source": VICTOR}]}])
    spoken = _spoken(w)
    pleas = [o for o in spoken if (o.speech.ask_action or {}).get("verb") == "release"]
    assert pleas, [_shown(w, o) for o in spoken]
    for o in pleas:
        assert o.speech.ask_action["params"] == {"target": GUEST}
        assert f"⟦{GUEST}⟧" not in o.speech.gist and "段公子" in o.speech.gist
        assert "段誉" not in _shown(w, o) and "段誉" not in o.speech.gist
    assert any(o.speech.act == "comfort" and o.speech.to == [GUEST] for o in spoken)  # 有好感的人落了难
    _check_knowledge(w, spoken)


async def test_pleading_for_oneself_in_danger(llm, w):
    """被人捆了、对方有敌意：求饶算「自保」，险境里必给。"""
    w.fx([
        {"rel": [VICTOR, "player", "affinity", -60]},
        {"add_status": ["player", "bound", {"source": VICTOR}]},
    ])
    spoken = _spoken(w)
    mine = [o for o in spoken if (o.speech.ask_action or {}).get("params") == {"target": w.s.player}]
    assert mine, [_shown(w, o) for o in spoken]
    plea = next(o for o in mine if o.speech.to == [VICTOR])
    assert plea.speech.ask_action["verb"] == "release" and plea.speech.action_class == "costly"
    assert _shown(w, plea) == "求场中舞剑的青年放了你"
    if plea.speech.act == "plead":
        assert kspeech.can_plead_to(w.s, w.p, w.s.chars[VICTOR])
    _check_knowledge(w, spoken)
    opts = await _opts(llm, w)
    assert opts and all(o.speech is None or speaking.sayable(w.s, w.defs, o.speech) for o in opts)


def test_comforting_an_injured_friend(w):
    assert not _find(_spoken(w), "comfort")
    w.fx([{"injure": [BOSS, 1]}])
    (c,) = _find(_spoken(w), "comfort", BOSS)
    assert c.tag == "人物" and c.rule == 30 and _shown(w, c) == "安慰马五德几句"
    _say(w, c)
    assert not _find(_spoken(w), "comfort", BOSS)  # 同一个时辰里不再重复


def test_asking_about_a_lead(w):
    """牵挂里提到、眼下不在跟前的人或地方：向在场的人打听（推进，按牵挂的分量）。"""
    threads.update(w.s, w.defs, [])
    w.fx([{"fact": {"id": "F_test_gate", "text": "⟦jianhu_gate⟧外来了一队生人"}},
          {"learn": ["player", "F_test_gate", 0.9, "inferred"]}])
    spoken = _spoken(w)  # 新得知的事生出一条线索
    tid = "lead:F_test_gate:jianhu_gate"
    assert tid in {t.id for t in threads.open_threads(w.s)}
    (o,) = [x for x in _find(spoken, "ask") if x.speech.about == "jianhu_gate"]
    assert o.thread_ids == [tid] and o.speech.to == [BOSS]  # 先问相熟的
    assert o.rule == pytest.approx(30 + 0.25 * 20) and o.tag == "人物"  # 线索的分量是 2：10 × 2 记在「推进」
    assert "前院山门" in _shown(w, o)
    _check_knowledge(w, spoken)
    _say(w, o)
    assert any(r.kind == "question" and r.frm == w.s.player and r.to == BOSS for r in w.s.requests.values())
    assert not [x for x in _find(_spoken(w), "ask", BOSS) if x.speech.about == "jianhu_gate"]  # 问过了
    # 心愿的分量更重：方向是「推进」
    w2 = World()
    threads.add_wish(w2.s, w2.defs, {"text": "想去⟦jianhu_gate⟧外头看看", "refs": ["jianhu_gate"], "weight": 4})
    (o2,) = [x for x in _find(_spoken(w2), "ask") if x.speech.about == "jianhu_gate"]
    assert o2.tag == "推进" and o2.rule == pytest.approx(40 + 0.25 * 30) and o2.thread_ids == ["wish:1"]


def test_replying_to_a_request(w):
    """有人求你一件事、还没答复：答应或回绝是「人物」的未了话头。"""
    ask = {"verb": "give", "params": {"item": "tea_sample", "target": "zuo_zimu"}, "desc": "把⟦tea_sample⟧交给⟦zuo_zimu⟧"}
    wait = actions.build(w.s, w.defs, BOSS, "wait", {"ticks": 1})
    resolve.apply(w.s, w.defs, wait, Speech(act="request", to=["player"], ask_action=ask), source="mind")
    (req,) = [r for r in w.s.requests.values() if r.to == w.s.player]
    replies = [o for o in _spoken(w) if o.speech.request == req.id]
    assert len(replies) == 1
    o = replies[0]
    assert o.speech.act in ("agree", "refuse") and o.speech.to == [BOSS] and o.tag == "人物"
    assert o.speech.act in kspeech.allowed_replies(req)
    _say(w, o)
    assert req.status != "open"
    assert not [x for x in _spoken(w) if x.speech.request == req.id]


# ---------------------------------------------------------------- 选项对象


async def test_speech_option_round_trips_through_pending(llm, w):
    _to_the_laugh(w)
    opts = await _opts(llm, w)
    for o in opts:
        d = json.loads(json.dumps(o.to_pending(), ensure_ascii=False))  # game 存进 state.pending_options
        back = Option.model_validate(d)
        assert back == o and back.signature == o.signature and back.id == o.id
        if o.speech is not None:
            assert isinstance(back.speech, Speech) and back.speech == o.speech
            assert back.action.verb == "wait"
    offered = w.s.storyteller["offered"]
    assert [x["sig"] for x in offered] == [o.signature for o in opts]
    assert any(x["sig"].startswith("say:") for x in offered)


async def test_note_choice_and_note_action_for_speech(llm, w):
    opts = await _opts(llm, w)
    said = next(o for o in opts if o.speech is not None)
    _say(w, said)
    note_choice(w.s, w.defs, said)
    assert w.s.storyteller["last_chosen"]["sig"] == said.signature
    assert said.signature not in {o.signature for o in await _opts(llm, w)}
    # 自由输入只说了一句话：game 把那句话一并交给 note_action，记的是那句话而不是 wait
    w2 = World()
    sp = Speech(act="greet", to=["zuo_zimu"], stance="warm")
    wait = actions.build(w2.s, w2.defs, w2.s.player, "wait", {"ticks": 1})
    greet = next(o for o in _spoken(w2) if o.speech.act == "greet" and o.speech.to == ["zuo_zimu"])
    assert greet.signature == speaking.speech_signature(sp)
    note_action(w2.s, w2.defs, wait, sp)
    assert w2.s.storyteller["last_chosen"]["sig"] == greet.signature
    assert w2.s.storyteller["recent_chosen"][-1][1:3] == ["say.greet", "to=zuo_zimu"]
    assert greet.signature not in {o.signature for o in _spoken(w2)}  # 本回合不再给同一句
    assert greet.signature not in {o.signature for o in await _opts(llm, w2)}
    # 不带那句话时，记的是动作本身
    note_action(w2.s, w2.defs, wait)
    assert w2.s.storyteller["last_chosen"]["sig"] == wait.signature


def test_speech_signature_is_stable_and_distinct():
    a = Speech(act="request", to=["x"], ask_action={"verb": "give", "params": {"item": "i", "target": "y"}, "desc": "一"})
    b = Speech(act="request", to=["x"], ask_action={"verb": "give", "params": {"target": "y", "item": "i"}, "desc": "二"})
    c = Speech(act="request", to=["x"], ask_action={"verb": "wait", "params": {"ticks": 1}, "stop": True})
    d = Speech(act="request", to=["x"], ask_action={"verb": "wait", "params": {"ticks": 1}, "permit": {"verb": "go"}})
    assert speaking.speech_signature(a) == speaking.speech_signature(b)  # 不看描述文字与参数的先后
    assert len({speaking.speech_signature(x) for x in (a, c, d)}) == 3
    assert speaking.speech_signature(Speech(act="tell", to=["x"], fact_refs=["F1"])) != speaking.speech_signature(
        Speech(act="tell", to=["x"], fact_refs=["F2"])
    )
    wait = actions.ActionInst(actor="p", verb="wait", params={"ticks": 1}, cost=1, desc="等一等",
                              signature=actions.signature("wait", {"ticks": 1}))
    o = Option(action=wait, speech=a, tag="人物", score=0, rule=0)
    assert o.signature == speaking.speech_signature(a) and o.desc and options.pair_of(o) == ("say.request", "to=x")


async def test_no_speech_options_when_nobody_can_hear(llm, w):
    """玩家开不了口（昏迷）：没有选项；身边没有听得见的人：没有开口的选项。"""
    w.fx([{"move": ["player", "jianhu_gate"]}])
    here = [c for c in kspeech.audience(w.s, w.p)]
    spoken = _spoken(w)
    assert all(set(o.speech.to) <= {c.id for c in here} for o in spoken)
    for c in here:
        w.fx([{"move": [c.id, "jianhu_hall"]}])
    assert not _spoken(w)
    assert all(o.speech is None for o in await _opts(llm, w))


def test_no_asking_after_someone_the_character_has_never_seen_or_heard_of(w):
    """2.3、8.4：牵挂里对上了角色说不出的人（旧存档、内容写的牵挂），不替他开口「打听」；知道了名字才问。"""
    assert "zhong_ling" not in w.s.known_terms and not common.referable(w.s)("zhong_ling")
    threads.add_wish(w.s, w.defs, {"text": "我要找到钟灵", "refs": ["zhong_ling", "valley_cave"], "weight": 4})
    asks = [o for o in _find(_spoken(w), "ask") if o.speech.about in ("zhong_ling", "valley_cave")]
    assert asks == []
    shown = [_shown(w, o) for o in _cands(w)]
    assert not any(common.pv(w.s, w.defs, "⟦zhong_ling⟧") in t for t in shown)
    w.fx([{"know_term": "valley_cave"}])
    assert [o.speech.about for o in _find(_spoken(w), "ask") if o.speech.about in ("zhong_ling", "valley_cave")] == ["valley_cave"]
