"""wave 3 的内核修补（K1…K20 里不属于记账效果的那些），在小世界夹具上逐条回归。"""

from __future__ import annotations

from typing import Any

import pytest

from tianlong.kernel import actions, bottom, checks, mood, perception, resolve, rng, speech, timeflow
from tianlong.kernel.defs import BlockDef, BottomLine
from tianlong.kernel.state import Claim, Request, Speech

from .conftest import World


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    seq: list[int] = []

    def roll(state):  # type: ignore[no-untyped-def]
        state.rng_counter += 1
        return seq.pop(0) if seq else 50

    monkeypatch.setattr(rng, "roll", roll)
    return lambda *v: seq.__setitem__(slice(None), list(v))


def own_defs(w: World) -> None:
    """夹具的 Defs 是各测试共用的：要改人物卡先拷一份。"""
    w.defs = w.defs.model_copy(deep=True)


def set_blocks(w: World, cid: str, *blocks: dict[str, Any]) -> None:
    own_defs(w)
    w.defs.cards[cid].bottom_lines = [BottomLine(text="测试", blocks=[BlockDef(**b) for b in blocks])]


def verdict(w: World, frm: str, whom: str, verb: str, /, **params: Any) -> str:
    r = Request(id="Rx", frm=frm, to=whom, action={"verb": verb, "params": params}, action_class="effort")
    speech.score_request(w.s, w.defs, r)
    return r.verdict


def said(evs: list) -> list:  # type: ignore[type-arg]
    return [e for e in evs if e.verb == "say"]


def unhide_girl(w: World) -> None:
    w.fx([{"remove_status": ["girl", "hidden"]}])


# ---------------------------------------------------------------- K1 / K2：底线的匹配


def test_block_act_is_compared_with_attack_intent(w: World) -> None:
    assert not any(a.verb == "attack" for a in w.legal("guest"))  # {verb: attack}：一概不动手
    set_blocks(w, "guest", {"verb": "attack", "act": "kill"})
    assert w.has("guest", "attack", target="fighter", intent="injure")
    assert not w.has("guest", "attack", target="fighter", intent="kill")
    # is_legal（结算前的重新校验）用的是同一套
    kill = w.build("guest", "attack", target="fighter", intent="kill")
    assert kill is not None and actions.is_legal(w.s, w.defs, kill) is None
    # 写成 intent 也一样；缺省的 intent 是 injure
    set_blocks(w, "guest", {"verb": "attack", "intent": "injure"})
    assert w.has("guest", "attack", target="fighter", intent="kill")
    assert not w.has("guest", "attack", target="fighter", intent="injure")
    assert bottom.blocked(w.s, w.defs, w.c("guest"), "attack", {"target": "fighter"})


def test_block_when_target_and_extras(w: World) -> None:
    unhide_girl(w)
    set_blocks(w, "fighter", {"verb": "attack", "act": "kill", "when": {"stat": ["$target", "power", "<=", 0]}})
    assert w.has("fighter", "attack", target="player", intent="injure")
    assert not w.has("fighter", "attack", target="player", intent="kill")  # 不杀无力还手的人
    assert w.has("fighter", "attack", target="girl", intent="kill")  # 有身手的照杀
    set_blocks(w, "fighter", {"verb": "attack", "target": "girl"}, {"verb": "push", "target": "player"})
    assert not w.has("fighter", "attack", target="girl", intent="injure")
    assert w.has("fighter", "attack", target="guest", intent="injure")
    assert not w.has("fighter", "push", target="player") and w.has("fighter", "push", target="guest")
    # 其余的键与动作参数同名；写成列表时任取其一
    w.fx([{"give": ["player", "fighter", "rope"]}])
    set_blocks(w, "fighter", {"verb": "give", "item": ["rope", "poison"]}, {"verb": "practice", "art": "breath"})
    assert not w.has("fighter", "give", item="rope", target="guest")
    assert not w.has("fighter", "give", item="poison", target="guest")
    assert w.has("fighter", "give", item="sword", target="guest")
    assert not w.has("fighter", "practice", art="breath", hours=1)
    assert w.has("fighter", "practice", art="hall_sword", hours=1)


def test_request_blocked_uses_the_same_matcher(w: World) -> None:
    set_blocks(
        w,
        "fighter",
        {"verb": "teach", "art": "hall_sword", "when": {"not": {"role": ["$target", "hall_sect"]}}},
        {"verb": "attack", "act": "kill"},
        {"verb": "give", "when": {"role": ["$requester", "errand_boy"]}},
    )
    assert verdict(w, "player", "fighter", "teach", art="hall_sword", target="player") == "never"
    assert verdict(w, "player", "fighter", "teach", art="hall_sword") == "never"  # 没写对象：教我
    assert verdict(w, "player", "fighter", "teach", art="breath", target="player") != "never"
    assert verdict(w, "player", "fighter", "attack", target="guest", intent="injure") != "never"
    assert verdict(w, "player", "fighter", "attack", target="guest") != "never"
    assert verdict(w, "player", "fighter", "attack", target="guest", intent="kill") == "never"
    assert verdict(w, "player", "fighter", "give", item="sword", target="guest") == "never"  # $requester
    assert verdict(w, "guest", "fighter", "give", item="sword", target="guest") != "never"
    w.fx([{"role": ["player", "hall_sect", "add"]}])
    assert verdict(w, "player", "fighter", "teach", art="hall_sword", target="player") != "never"


def test_say_blocks_in_requests_and_speech(w: World) -> None:
    # 白逸不说家里的事：请他说这一条是 never，说别的不是
    assert verdict(w, "player", "guest", "say", act="tell", fact="F_guest_family") == "never"
    assert verdict(w, "player", "guest", "say", act="tell", fact="F_bout") != "never"
    assert verdict(w, "player", "guest", "say", act="tell") != "never"
    set_blocks(w, "guest", {"verb": "say", "act": "insult", "target": "fighter"})
    assert verdict(w, "player", "guest", "say", act="insult", to="fighter") == "never"
    assert verdict(w, "player", "guest", "say", act="insult", to="girl") != "never"
    assert speech.say_blocked(w.s, w.defs, w.c("guest"), "insult", to=["fighter"])
    assert not speech.say_blocked(w.s, w.defs, w.c("guest"), "insult", to=["player"])
    assert not speech.say_blocked(w.s, w.defs, w.c("guest"), "insult")
    assert "insult" in speech.available_acts(w.s, w.defs, w.c("guest"))
    assert not said(w.say("guest", "insult", ["fighter"]))  # 底线：这句话说不出口
    assert said(w.say("guest", "insult", ["player"]))


# ---------------------------------------------------------------- K3：同门


def test_same_sect_is_data_driven(w: World) -> None:
    w.fx([{"role": ["player", "bout_guest", "add"]}, {"role": ["fighter", "bout_guest", "add"]}])
    assert not mood.same_sect(w.defs, w.c("player"), w.c("fighter"), w.s)  # 同是来客不算同门
    assert mood.band(w.defs, w.s, "fighter", "player") == "stranger"
    assert mood.sect_roles(w.defs, w.s) == {"hall_sect"}  # 没写 sect_roles：占着地盘的身份
    own_defs(w)
    w.defs.world.request.sect_roles = ["bout_guest"]
    assert mood.band(w.defs, w.s, "fighter", "player") == "familiar"
    w.defs.world.request.sect_roles = []
    w.fx([{"role": ["player", "hall_sect", "add"]}])
    assert mood.band(w.defs, w.s, "fighter", "player") == "stranger"
    w.fx([{"superior": ["player", "fighter"]}])  # 师徒、主仆不看身份
    assert mood.band(w.defs, w.s, "fighter", "player") == "familiar"


def test_sect_roles_loads_from_world_yaml(tmp_path, mini_dir) -> None:  # type: ignore[no-untyped-def]
    import shutil

    import yaml

    from tianlong.content.loader import load
    from tianlong.content.validate import validate

    dst = tmp_path / "mini"
    shutil.copytree(mini_dir, dst)
    data = yaml.safe_load((dst / "world.yaml").read_text(encoding="utf-8"))
    data["request"]["sect_roles"] = ["valley_family"]
    (dst / "world.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    defs, state = load(dst)
    assert defs.world.request.sect_roles == ["valley_family"] and validate(defs, state) == []
    assert mood.sect_roles(defs, state) == {"valley_family"}
    data["request"]["sect_roles"] = ["valley_family", 3]
    (dst / "world.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(Exception, match="sect_roles"):
        load(dst)


# ---------------------------------------------------------------- K5 / K6：时间与离场


def test_run_due_scheduled_is_public(w: World) -> None:
    assert timeflow.run_due_scheduled(w.s, w.defs) == [] and len(w.s.scheduled) == 1
    w.s.tick = 40
    w.s.scheduled.append({"at": 40, "effects": [{"set_flag": ["skipped", True]}], "pre": {"flag": "nope"}})
    timeflow.run_due_scheduled(w.s, w.defs)
    assert w.s.flags.get("bell_rang") is True and "skipped" not in w.s.flags and w.s.scheduled == []


def test_apply_offscreen_marks_every_event(w: World) -> None:
    evs = w.act("guest", "go", exit="yard")
    assert evs and not any(e.offscreen for e in evs)
    a = w.build("guest", "go", exit="hall")
    sp = Speech(act="greet", to=[])
    first = w.s.next_event_id
    evs = resolve.apply(w.s, w.defs, a, sp, source="agenda", offscreen=True)
    assert len(evs) >= 2 and all(e.offscreen for e in evs)
    assert all(e.offscreen for e in w.s.events_tail if e.id >= first)
    assert not any(e.offscreen for e in w.s.events_tail if e.id < first)


# ---------------------------------------------------------------- K7 / K8 / K14：视角


def test_view_events_carry_ids(w: World) -> None:
    evs = w.say("fighter", "insult", ["guest"])
    evs += w.act("fighter", "push", target="player")
    view = perception.player_view(w.s, w.defs, evs)
    said, pushed = view.events
    assert said.actor_id == "fighter" and said.target_ids == ["guest"] and said.loc == "hall"
    assert pushed.actor_id == "fighter" and pushed.target_ids == ["player"]
    assert said.actor == "那个黑脸汉子"  # 称呼照旧


def test_follow_move_is_its_own_verb(w: World) -> None:
    start = w.act("player", "follow", target="guest")
    assert [e.verb for e in start] == ["follow"] and w.c("player").loc == "hall"  # 开始跟随：人没动
    evs = w.act("guest", "go", exit="yard")
    moved = [e for e in evs if e.actor == "player"]
    assert [e.verb for e in moved] == ["follow_move"] and w.c("player").loc == "yard"
    assert moved[0].params["target"] == "guest" and moved[0].params["exit"] == "yard"


def test_player_learns_no_names_from_own_speech(w: World) -> None:
    assert "fighter" not in w.s.known_terms and "guest" not in w.s.known_terms
    w.say("player", "request", ["guest"], ask_action={"verb": "attack", "params": {"target": "fighter"}})
    w.say("player", "tell", ["guest"], gist="⟦fighter⟧不好惹")
    assert "fighter" not in w.s.known_terms and "guest" not in w.s.known_terms
    w.say("guest", "tell", ["player"], gist="⟦fighter⟧不好惹")  # 别人说出口的名字才算听到
    assert "fighter" in w.s.known_terms


# ---------------------------------------------------------------- K9：看不见的东西


def test_steal_needs_a_visible_item(w: World) -> None:
    assert not any(a.verb == "steal" for a in w.legal("player"))
    assert w.build("player", "steal", item="token", target="guest") is None
    assert not perception.item_visible(w.s, w.c("player"), "token")
    assert perception.item_visible(w.s, w.c("player"), "rope")  # 自己身上的
    assert perception.item_visible(w.s, w.c("guest"), "token")
    # 细看过那个人：他身上的东西都看在眼里
    w.act("player", "examine", target="guest")
    assert w.has("player", "steal", item="token", target="guest")
    assert w.has("player", "steal", item="antidote", target="guest")
    assert not w.has("player", "steal", item="sword", target="fighter")


def test_item_visibility_rules(w: World) -> None:
    p = w.c("player")
    w.s.items["sword"].props["visible"] = True  # 明摆着的东西
    assert w.has("player", "steal", item="sword", target="fighter")
    w.s.items["poison"].tags.append("mount")
    assert perception.item_visible(w.s, p, "poison")
    # 亲眼见过这件东西（别人把它亮出来）
    assert not perception.item_visible(w.s, p, "antidote")
    w.act("guest", "show", item="antidote", target="fighter")
    assert perception.item_visible(w.s, p, "antidote")
    # 相信一条同时提到主人与这件东西的事实
    assert not perception.item_visible(w.s, p, "token")
    w.fx([{"fact": {"id": "F_token", "text": "⟦guest⟧身上带着⟦token⟧。", "known_by": ["player"]}}])
    assert perception.item_visible(w.s, p, "token")
    # 地上的看得见，藏在景物里的看不见；人走了也看不见
    assert not perception.item_visible(w.s, p, "old_manual")
    w.fx([{"move": ["guest", "yard"]}])
    assert not perception.item_visible(w.s, p, "token")
    assert not perception.item_visible(w.s, p, None) and not perception.item_visible(w.s, p, "nothing")


def test_npc_item_knowledge(w: World) -> None:
    f = w.c("fighter")
    assert not perception.item_visible(w.s, f, "token")
    assert not any(a.verb == "steal" for a in w.legal("fighter"))
    w.act("fighter", "examine", target="guest")  # 别人细看过谁，看最近的事件
    assert perception.item_visible(w.s, f, "token") and w.has("fighter", "steal", item="token", target="guest")
    # 打算里就有这件东西的人，知道它在谁身上
    g = w.c("girl")
    unhide_girl(w)
    assert not perception.item_visible(w.s, g, "poison")
    w.fx([{"step": ["girl", {"id": "g_steal", "text": "偷药", "do": {"verb": "steal", "item": "poison", "target": "fighter"}}]}])
    assert perception.item_visible(w.s, g, "poison")
    star = next(a for a in w.legal("girl") if a.star)
    assert star.verb == "steal" and star.params == {"item": "poison", "target": "fighter"}


# ---------------------------------------------------------------- K10：图外的出口


def test_edge_exit_text(w: World) -> None:
    from tianlong.kernel import refs

    # 图外那头在词表里有名字，就带上；玩家看到的是它的叫法，不是 id
    w.fx([{"move": ["player", "yard"]}])
    a = w.build("player", "go", exit="out_road")
    assert a is not None and a.desc == "离开这一带，往⟦out_road⟧去"
    assert refs.render(a.desc, w.s, w.defs, "player") == "离开这一带，往一条出山的路去"
    # 词表里没有这一条：只说「离开这一带」，事件的事实句里也不露 id
    w2 = World()
    own_defs(w2)
    del w2.defs.lexicon.terms["out_road"]
    w2.fx([{"move": ["player", "yard"]}])
    a = w2.build("player", "go", exit="out_road")
    assert a is not None and a.desc == "离开这一带"
    evs = resolve.apply(w2.s, w2.defs, a, source="player")
    assert evs[0].fact_text == "⟦player⟧走出了这一带" and w2.s.ended == "left"


# ---------------------------------------------------------------- K11：成了反而坏事的判定


def test_adverse_check_fails_under_lucky(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    def run(adverse: bool) -> bool:
        w.s.flags.pop("out", None)
        chk: dict[str, Any] = {
            "kind": "chance",
            "chance": 5,
            "who": "guest",
            "ok": [{"set_flag": ["out", "ok"]}],
            "fail": [{"set_flag": ["out", "fail"]}],
        }
        if adverse:
            chk["adverse"] = True
        w.fx([{"check": chk}])
        return w.s.flags["out"] == "ok"

    dice(1)
    assert run(True)  # 平时照掷：1 <= 5
    dice(60)
    assert not run(True)
    w.s.flags[checks.LUCKY_FLAG] = True
    before = w.s.rng_counter
    assert run(False)  # --lucky：寻常判定一律成功
    assert not run(True)  # 成了反而坏事的，一律不成
    assert w.s.rng_counter == before + 2  # 仍各消耗一次随机数
    assert checks.roll_against(w.s, "x", 50, adverse=True).ok is False


# ---------------------------------------------------------------- K12 / K13：只说话；致命警示


def test_build_say_gives_the_speech_carrier(w: World) -> None:
    a = w.build("player", "say")
    b = actions.build_speech_wait(w.s, w.defs, "player")
    assert a is not None and b is not None and a.signature == b.signature == "wait:ticks=1"
    assert (a.verb, a.params, a.cost) == ("wait", {"ticks": 1}, 1)
    evs = resolve.apply(w.s, w.defs, a, Speech(act="greet", to=["guest"]), source="player")
    assert [e.verb for e in evs] == ["say"]  # 只记那句话，不另记「等」
    w.fx([{"add_status": ["player", "unconscious"]}])
    assert w.build("player", "say") is None and actions.build_speech_wait(w.s, w.defs, "player") is None


def test_lethal_warning_turn_drops_the_utterance(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    a = w.build("player", "attack", target="fighter", intent="injure")
    assert a is not None and a.lethal
    sp = Speech(act="insult", to=["fighter"])
    evs = resolve.apply(w.s, w.defs, a, sp, source="player")
    assert [e.verb for e in evs] == ["warn_danger"] and evs[0].outcome == "blocked"
    assert "anger" not in w.c("fighter").mood  # 那句话没有结算：局面不变
    dice(100)
    evs = resolve.apply(w.s, w.defs, a, sp, source="player")
    assert [e.verb for e in evs][:2] == ["say", "attack"] and w.c("fighter").mood["anger"] >= 20


# ---------------------------------------------------------------- K15 / K16：请求里夹带的话


def test_relevant_true_detail_is_weak_evidence_once(w: World) -> None:
    w.fx(
        [
            {"fact": {"id": "F_paths", "text": "⟦player⟧熟悉⟦cliff⟧一带的山路。", "known_by": ["player"]}},
            {"fact": {"id": "F_rope", "text": "⟦player⟧打得一手好绳结。", "known_by": ["player"]}},
            {"fact": {"id": "F_far", "text": "远处的集市逢五开市。", "known_by": ["player"]}},
        ]
    )
    ask = {"verb": "follow", "params": {"target": "player"}}
    evs = w.say("player", "request", ["fighter"], ask_action=ask, fact_refs=["F_far"])
    r = w.s.requests[evs[0].speech.request]
    assert r.evidence == [] and r.score == 30  # 不相干的事不算凭据
    evs = w.say("player", "request", ["fighter"], ask_action=ask, fact_refs=["F_paths", "F_rope"])
    assert r.evidence == ["weak:detail:F_paths"] and r.score == 40 and r.attempts == 1  # 只算一次
    w.say("player", "request", ["fighter"], ask_action=ask, fact_refs=["F_rope"])
    assert r.evidence == ["weak:detail:F_paths"] and r.attempts == 2 and r.score == 30  # 换一条细节不算新凭据
    # 说者自己不信的事不算
    assert not speech.relevant_detail(w.s, w.c("guest"), w.c("fighter"), Speech(act="request", to=[]), "F_paths")


def test_claims_are_judged_for_every_act(w: World) -> None:
    w.fx([{"fact": {"id": "F_paths", "text": "⟦player⟧熟悉⟦cliff⟧一带的山路。", "known_by": ["player"]}}])
    ask = {"verb": "follow", "params": {"target": "player"}}
    true_claim = Claim(text="我熟山路", ref="F_paths", verdict="true")
    evs = w.say("player", "request", ["guest"], ask_action=ask, fact_refs=["F_paths"], claims=[true_claim])
    assert w.c("guest").beliefs["F_paths"].confidence == 0.6  # 0.5 + 20/200：请求里夹带的话也照「听人说」更新
    assert evs[0].params["judgements"]["guest"][0].startswith("你的判断：信了")
    # 自称做过没做过的事：在场的人不信，trust -15（不只告知，问话、请求里也一样）
    w.act("player", "look")
    before = w.c("fighter").rel["player"].trust if "player" in w.c("fighter").rel else 0
    lie = Claim(text="自称做过一件事", ref=None, verdict="false_self")
    evs = w.say("player", "ask", ["fighter"], about="girl", claims=[lie])
    assert w.c("fighter").rel["player"].trust == before - 15
    assert {"k": "exposed", "who": "player", "by": "fighter"} in evs[0].kinds
    # 说谎不按断言判
    evs = w.say("player", "lie", ["guest"], claims=[lie])
    assert evs[0].params["judgements"]["guest"] == ["你的判断：半信半疑，他拿不出凭据"]


# ---------------------------------------------------------------- K17：替别人求情


def test_plead_for_someone_else(w: World) -> None:
    p = w.c("player")
    assert "plead" not in speech.available_acts(w.s, w.defs, p)
    w.fx([{"rel": ["fighter", "guest", "affinity", -50]}])  # 对在场的另一个人有敌意
    assert speech.can_plead_to(w.s, p, w.c("fighter")) and not speech.can_plead_to(w.s, p, w.c("guest"))
    assert "plead" in speech.available_acts(w.s, w.defs, p)
    ask = {"verb": "wait", "params": {}}
    evs = w.say("player", "plead", ["fighter"], ask_action=ask)
    assert evs and w.s.requests[evs[0].speech.request].kind == "plead"
    assert not said(w.say("player", "plead", ["guest"], ask_action=ask))  # 对谁也没有敌意的人，求不着


def test_plead_to_a_captor(w: World) -> None:
    p = w.c("player")
    unhide_girl(w)
    w.fx([{"add_status": ["guest", "captive", {"source": "girl"}]}, {"superior": ["girl", "fighter"]}])
    assert speech.holds_captive(w.s, w.c("girl")) and speech.holds_captive(w.s, w.c("fighter"))  # 手下扣的人算他的
    assert not speech.holds_captive(w.s, w.c("guest"))
    assert speech.can_plead_to(w.s, p, w.c("fighter"))
    assert "plead" in speech.available_acts(w.s, w.defs, p)
    evs = w.say("player", "plead", ["fighter"], ask_action={"verb": "release", "params": {"target": "guest"}})
    assert evs and evs[0].speech.gist == "想让对方放了⟦guest⟧"


# ---------------------------------------------------------------- K19：要旨里不露英文动词


def test_request_gist_is_chinese(w: World) -> None:
    cases = {
        ("follow", "target", "player"): "想让对方跟着自己走",
        ("give", "item", "token"): "想让对方把东西交出来",
        ("teach", "art", "breath"): "想让对方传授⟦breath⟧",
        ("teach", "art", "literacy"): "想让对方传授识字",
        ("attack", "target", "fighter"): "想让对方对⟦fighter⟧动手",
        ("rest", "hours", 2): "想让对方歇一歇",
        ("setpiece", "name", "x"): "想让对方做一件事",
        ("push", "feature", "rack"): "想让对方推",
    }
    for (verb, key, value), want in cases.items():
        sp = Speech(act="request", to=["guest"], ask_action={"verb": verb, "params": {key: value}})
        assert speech.gist_for(sp, "player", w.s) == want
    both = {"verb": "give", "params": {"item": "token", "target": "player"}}
    assert speech.ask_phrase(both, "player") == "把⟦token⟧交给自己"
    assert speech.ask_phrase({"verb": "say", "params": {"act": "tell", "about": "girl"}}) == "说一说⟦girl⟧的事"
    assert speech.ask_phrase({"verb": "dance"}) == "做一件事"
    for verb in actions.VERBS:
        text = speech.ask_phrase({"verb": verb, "params": {}}, "player")
        assert text and not any(ch.isascii() and ch.isalpha() for ch in text), (verb, text)
    evs = w.say("player", "request", ["guest"], ask_action={"verb": "follow", "params": {"target": "player"}})
    assert evs[0].fact_text == "⟦player⟧对⟦guest⟧请求：想让对方跟着自己走"
