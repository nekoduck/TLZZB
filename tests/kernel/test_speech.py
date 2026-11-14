"""方案 6.6：言语行为与请求裁决；speech_options。"""

from __future__ import annotations

from tianlong.kernel import speech, timeflow

from .conftest import World


def req(w: World, frm: str, to: str, verb: str, kind: str = "request", **kw) -> str:  # type: ignore[no-untyped-def]
    params = kw.pop("params", {})
    evs = w.say(frm, kind, [to], ask_action={"verb": verb, "params": params}, **kw)
    assert evs and evs[0].speech is not None and evs[0].speech.request, w.s.violations
    return evs[0].speech.request


def test_available_acts(w: World) -> None:
    acts = speech.available_acts(w.s, w.defs, w.c("player"))
    assert {
        "greet",
        "tell",
        "ask",
        "request",
        "promise",
        "threaten",
        "warn",
        "comfort",
        "insult",
        "taunt",
        "lie",
    } <= set(acts)
    assert "plead" not in acts and "order" not in acts and "agree" not in acts
    w.fx([{"rel": ["fighter", "player", "affinity", -50]}])
    assert "plead" in speech.available_acts(w.s, w.defs, w.c("player"))
    w.c("player").superior = "guest"
    assert "order" in speech.available_acts(w.s, w.defs, w.c("guest"))
    assert "plead" not in speech.available_acts(w.s, w.defs, w.c("fighter"))  # 底线：不求饶
    w.c("player").loc = "cliff"
    assert speech.available_acts(w.s, w.defs, w.c("player")) == []  # 没人可说


def test_greet_first_meeting_and_names(w: World) -> None:
    w.say("player", "greet", ["fighter"])
    assert w.c("fighter").rel["player"].affinity == 1
    w.say("player", "greet", ["fighter"])
    assert w.c("fighter").rel["player"].affinity == 1  # 只有初次见面
    assert "fighter" not in w.s.known_terms
    w.say("fighter", "greet", ["player"])  # 自报家门
    assert "fighter" in w.s.known_terms


def test_tell_updates_beliefs_by_formula(w: World) -> None:
    w.fx(
        [
            {"learn": ["player", "F_girl_on_beam", 1.0, "saw"]},
            {"learn": ["player", "F_girl_sister", 1.0, "saw"]},
        ]
    )
    evs = w.say("player", "tell", ["guest"], fact_refs=["F_girl_on_beam"])
    assert w.c("guest").beliefs["F_girl_on_beam"].confidence == 0.6  # 0.5 + 20/200
    assert w.c("guest").beliefs["F_girl_on_beam"].source == "told:player"
    assert any(k["k"] == "learn_key" and k["who"] == "guest" for k in evs[0].kinds)
    assert evs[0].params["judgements"]["guest"][0].startswith("你的判断：信了")
    # 旁边的雷猛也听见了：trust 0 → 0.5，将信将疑
    assert w.c("fighter").beliefs["F_girl_on_beam"].confidence == 0.5
    evs = w.say("player", "tell", ["fighter"], fact_refs=["F_girl_sister"])
    assert w.c("fighter").beliefs["F_girl_sister"].confidence == 0.2  # 与确信的事相悖 -0.3
    assert "相悖" in evs[0].params["judgements"]["fighter"][0]


def test_cannot_say_unbelieved_or_forbidden_facts(w: World) -> None:
    evs = w.say("player", "tell", ["guest"], fact_refs=["F_girl_on_beam"])
    assert evs[0].speech.fact_refs == [] and any("stripped" in v for v in w.s.violations)
    assert "F_guest_family" not in speech.speakable_facts(w.s, w.defs, w.c("guest"))
    evs = w.say("guest", "tell", ["player"], fact_refs=["F_guest_family"])
    assert evs[0].speech.fact_refs == []
    assert "F_guest_family" not in w.c("player").beliefs


def test_request_scoring_table(w: World) -> None:
    # 书生对玩家：trust 20、affinity 20 → 均值 20 → 相熟
    r = w.s.requests[req(w, "player", "guest", "show", params={"item": "token", "target": "player"})]
    assert r.action_class == "trivial" and r.score == 70 and r.verdict == "may"
    r = w.s.requests[req(w, "player", "guest", "follow", params={"target": "player"})]
    assert r.action_class == "effort" and r.score == 50 and r.verdict == "conditional"
    r = w.s.requests[req(w, "player", "guest", "give", params={"item": "token", "target": "player"})]
    assert r.action_class == "costly" and r.score == 30 and r.verdict == "no"
    # 雷猛对玩家：陌生
    r = w.s.requests[req(w, "player", "fighter", "follow", params={"target": "player"})]
    assert r.score == 30 and r.verdict == "no"
    # 解析器给的类别为准
    r = w.s.requests[req(w, "player", "fighter", "look", action_class="betray")]
    assert r.score == -30 and r.verdict == "no"


def test_bands_and_close_roles(w: World) -> None:
    from tianlong.kernel import mood

    assert mood.band(w.defs, w.s, "guest", "player") == "familiar"
    assert mood.band(w.defs, w.s, "fighter", "player") == "stranger"
    w.fx([{"rel": ["fighter", "player", "trust", -80]}])
    assert mood.band(w.defs, w.s, "fighter", "player") == "hostile"
    w.fx([{"role": ["player", "hall_sect", "add"]}])
    assert mood.band(w.defs, w.s, "fighter", "player") == "familiar"  # 同门至少相熟
    w.fx([{"rel": ["guest", "player", "trust", 60]}, {"rel": ["guest", "player", "affinity", 60]}])
    assert mood.band(w.defs, w.s, "guest", "player") == "trusted"


def test_evidence_and_leverage(w: World) -> None:
    w.fx([{"give": ["guest", "player", "token"]}])
    rid = req(w, "player", "fighter", "follow", params={"target": "player"}, evidence=["token"])
    r = w.s.requests[rid]
    assert "strong:item:token" in r.evidence and r.score == 30 + 20
    # 对方亲眼见过的事：强凭据；对方已知的事：弱凭据
    w.fx(
        [
            {"learn": ["player", "F_girl_stranger", 1.0, "saw"]},
            {"learn": ["player", "F_well_secret", 1.0, "saw"]},
            {"learn": ["fighter", "F_well_secret", 0.8, "told:x"]},
        ]
    )
    r = w.s.requests[req(w, "player", "fighter", "look", fact_refs=["F_girl_stranger", "F_well_secret"])]
    assert "strong:fact:F_girl_stranger" in r.evidence and "weak:fact:F_well_secret" in r.evidence
    assert r.score == 50 + 20 + 10
    # 筹码：急需 +30，报酬 10 到 30，可信的威胁加 fear 的三分之一，欠着人情 +20
    r = w.s.requests[
        req(
            w,
            "player",
            "fighter",
            "help",
            params={"target": "player"},
            leverage={"urgent": True, "reward": 99},
        )
    ]
    assert r.leverage == 60 and r.score == 30 + 60
    w.c("guest").rel["player"].fear = 30
    w.c("guest").owes["player"] = 1
    r = w.s.requests[
        req(w, "player", "guest", "help", params={"target": "player"}, leverage={"threat": True})
    ]
    assert r.leverage == 20 + 10


def test_repeats_cost_and_nagging(w: World) -> None:
    rid = req(w, "player", "guest", "follow", params={"target": "player"})
    assert w.s.requests[rid].score == 50
    rid2 = req(w, "player", "guest", "follow", params={"target": "player"})
    assert rid2 == rid and w.s.requests[rid].attempts == 2 and w.s.requests[rid].score == 40
    before = w.c("guest").mood.get("anger", 0)
    req(w, "player", "guest", "follow", params={"target": "player"})
    assert w.s.requests[rid].score == 30 and w.c("guest").mood.get("anger", 0) == before + 10


def test_never_on_bottom_line_and_reply_rules(w: World) -> None:
    rid = req(w, "player", "guest", "attack", params={"target": "fighter"})
    r = w.s.requests[rid]
    assert r.verdict == "never"
    view = speech.speech_options(w.s, w.defs, "guest").requests[0]
    assert view.verdict_text == "决不答应" and view.allowed_replies == ["refuse"]
    assert "agree" not in speech.speech_options(w.s, w.defs, "guest").acts
    # 硬选 agree：改判为 refuse 并记违规
    w.say("guest", "refuse", ["player"], request=rid)
    assert r.status == "refused"


def test_verdict_texts() -> None:
    assert speech.VERDICT_TEXT == {
        "never": "决不答应",
        "no": "不会答应",
        "conditional": "可以谈条件",
        "may": "可以答应，也可以不答应",
    }


def test_agree_inserts_step_and_promise_kept(w: World) -> None:
    rid = req(w, "player", "guest", "show", params={"item": "token", "target": "player"})
    view = speech.speech_options(w.s, w.defs, "guest").requests[0]
    assert view.allowed_replies == ["agree", "refuse"]
    w.say("guest", "agree", ["player"], request=rid)
    r = w.s.requests[rid]
    assert r.status == "agreed"
    ag = w.c("guest").agenda
    st = ag.steps[ag.cursor]
    assert st.do == {"verb": "show", "item": "token", "target": "player"} and st.origin == "rule"
    legal = w.legal("guest")
    assert legal[0].star and legal[0].verb == "show"  # 下一回合在其清单里标 ★
    trust = w.c("player").rel["guest"].trust
    w.act("guest", "show", item="token", target="player")
    assert st.state == "done"
    assert w.c("player").rel["guest"].trust == trust + 15  # 守诺
    assert all(p.status == "kept" for p in w.s.promises.values())


def test_bargain_then_requester_meets_condition(w: World) -> None:
    rid = req(w, "player", "guest", "follow", params={"target": "player"})
    view = speech.speech_options(w.s, w.defs, "guest").requests[0]
    assert view.verdict_text == "可以谈条件" and view.allowed_replies == ["bargain", "refuse"]
    assert {"verb": "give", "params": {"item": "rope", "target": "guest"}} in view.counter_options
    w.say(
        "guest",
        "bargain",
        ["player"],
        request=rid,
        condition={"verb": "give", "params": {"item": "rope", "target": "guest"}},
    )
    r = w.s.requests[rid]
    assert r.status == "countered" and r.condition["verb"] == "give"
    opts = speech.speech_options(w.s, w.defs, "player")
    assert any(v.request.id == rid and v.allowed_replies == ["agree", "refuse"] for v in opts.requests)
    assert any("reply" in a.tags for a in w.legal("player"))
    w.say("player", "agree", ["guest"], request=rid)
    assert w.s.items["rope"].holder == "guest" and r.status == "agreed"


def test_condition_met_by_doing_it(w: World) -> None:
    rid = req(w, "player", "guest", "follow", params={"target": "player"})
    w.say(
        "guest",
        "bargain",
        ["player"],
        request=rid,
        condition={"verb": "give", "params": {"item": "rations", "target": "guest"}},
    )
    w.act("player", "give", item="rations", target="guest")
    assert w.s.requests[rid].status == "agreed"


def test_agree_not_allowed_is_refused(w: World) -> None:
    rid = req(w, "player", "fighter", "give", params={"item": "sword", "target": "player"})
    assert w.s.requests[rid].verdict == "no"
    evs = w.say("fighter", "agree", ["player"], request=rid)
    assert evs[0].speech.act == "refuse"  # 模型硬选 agree：改判为拒绝
    assert w.s.requests[rid].status == "refused"
    assert any("not allowed by the verdict" in v for v in w.s.violations)


def test_question_and_answer(w: World) -> None:
    evs = w.say("player", "ask", ["guest"], about="fighter")
    rid = evs[0].speech.request
    assert w.s.requests[rid].kind == "question"
    view = speech.speech_options(w.s, w.defs, "guest").requests[0]
    assert view.allowed_replies == ["tell", "refuse", "lie"]
    w.say("guest", "tell", ["player"], request=rid, fact_refs=["F_bout"])
    assert w.s.requests[rid].status == "agreed"


def test_order_and_plead(w: World) -> None:
    w.c("player").superior = "guest"
    r = w.s.requests[req(w, "guest", "player", "follow", kind="order", params={"target": "guest"})]
    assert r.kind == "order" and r.score == 50 + 60  # 主仆：至少相熟；命令 +60
    evs = w.say("player", "plead", ["fighter"], ask_action={"verb": "wait"})
    assert [e.verb for e in evs] == ["wait"]  # 对方没有敌意：这句话去掉，只剩等待
    w.fx([{"rel": ["fighter", "player", "affinity", -50]}])
    r = w.s.requests[req(w, "player", "fighter", "release", kind="plead", params={"target": "player"})]
    assert r.kind == "plead"


def test_promise_deadline_broken(w: World) -> None:
    w.say(
        "player",
        "promise",
        ["guest"],
        promise_do={"verb": "give", "item": "rope", "target": "guest"},
        deadline=w.s.tick + 2,
    )
    p = next(iter(w.s.promises.values()))
    assert p.frm == "player" and p.status == "open"
    trust = w.c("guest").rel["player"].trust
    timeflow.advance_time(w.s, w.defs, 3)
    assert p.status == "broken" and w.c("guest").rel["player"].trust == trust - 20
    assert any("没有做到" in m.text for m in w.c("guest").memory)


def test_threaten_by_regard(w: World) -> None:
    w.fx([{"rel": ["guest", "fighter", "regard", 2]}])
    w.say("fighter", "threaten", ["guest"])
    assert w.c("guest").mood["fear"] == 20 and w.c("guest").rel["fighter"].fear == 20
    w.say("guest", "threaten", ["fighter"])
    assert w.c("fighter").mood["anger"] == 15


def test_warn_comfort_insult_lie(w: World) -> None:
    w.say("player", "warn", ["guest"])
    assert w.status("guest", "alerted") is not None
    assert any(a.verb == "flee" for a in w.legal("guest"))
    assert any(a.verb == "hide" and not a.params for a in w.legal("guest"))
    w.c("guest").mood["fear"] = 30
    w.say("player", "comfort", ["guest"])
    assert w.c("guest").mood["fear"] == 20 and w.c("guest").rel["player"].affinity == 25
    w.say("player", "comfort", ["guest"])
    assert w.c("guest").mood["fear"] == 20  # 同一人每时辰一次
    w.say("player", "insult", ["fighter"])
    assert w.c("fighter").mood["anger"] == 20 and w.c("fighter").rel["player"].affinity == -10
    w.say("player", "taunt", ["fighter"])
    assert w.c("fighter").mood["anger"] == 40
    evs = w.say("player", "lie", ["guest"], fact_refs=["F_bout"], gist="说自己是武馆的人")
    assert evs[0].params["lie"] is True and evs[0].speech.fact_refs == []


def test_whisper_only_target_hears(w: World) -> None:
    w.fx([{"learn": ["player", "F_girl_on_beam", 1.0, "saw"]}])
    evs = w.say("player", "tell", ["guest"], fact_refs=["F_girl_on_beam"], whisper=True)
    assert "F_girl_on_beam" in w.c("guest").beliefs and "F_girl_on_beam" not in w.c("fighter").beliefs
    m = w.c("fighter").memory[-1]
    assert "低声" in m.text and evs[0].params["overheard"]


def test_speech_options_star_and_facts(w: World) -> None:
    w.fx(
        [
            {
                "step": [
                    "guest",
                    {
                        "id": "g_tell",
                        "text": "告诉伙计",
                        "do": {"verb": "say", "act": "tell", "to": "player", "fact": "F_bout"},
                    },
                ]
            }
        ]
    )
    opts = speech.speech_options(w.s, w.defs, "guest")
    assert opts.star == {"act": "tell", "to": "player", "fact": "F_bout"}
    assert "F_bout" in opts.facts and len(opts.facts) <= 8
    w.say("guest", "tell", ["player"], fact_refs=["F_bout"])
    st = w.c("guest").agenda.steps[0]
    assert st.id == "g_tell" and st.state == "done"
