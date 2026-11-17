"""方案 6.7 的信念更新与 6.8 的感知、4.4 的玩家视角、称呼。"""

from __future__ import annotations

from tianlong.kernel import beliefs, memory, perception, refs
from tianlong.kernel.state import Claim, MemoryItem

from .conftest import World


def test_hearsay_formula(w: World) -> None:
    g = w.c("guest")
    assert beliefs.hearsay(w.s, g, "player", "F_girl_on_beam") == 0.6
    assert beliefs.hearsay(w.s, g, "player", "F_girl_on_beam", shown=True) == 0.8
    assert beliefs.hearsay(w.s, g, "player", "F_girl_on_beam", unsourced=True) == 0.4
    f = w.c("fighter")
    assert beliefs.hearsay(w.s, f, "player", "F_girl_sister") == 0.2  # 与确信的事相悖
    assert (
        beliefs.label(0.6) == "believe"
        and beliefs.label(0.3) == "doubt"
        and beliefs.label(0.29) == "disbelieve"
    )


def test_disbelief_of_self_serving_claim_costs_trust(w: World) -> None:
    f = w.c("fighter")
    j = beliefs.hear(w.s, w.defs, f, "player", "F_girl_sister", beneficial=True)
    assert j.label == "disbelieve" and f.rel["player"].trust == -5
    assert j.text.startswith("你的判断：不信")


def test_claim_verdicts(w: World) -> None:
    assert beliefs.claim_verdict(w.s, "player", "F_bout") == "true"
    assert beliefs.claim_verdict(w.s, "player", "F_guest_family") == "unsourced"
    assert beliefs.claim_verdict(w.s, "player", None) == "opinion"
    assert beliefs.claim_verdict(w.s, "player", None, self_claim=True) == "false_self"
    evs = w.act("player", "look")
    assert beliefs.claim_verdict(w.s, "player", f"E{evs[0].id}", self_claim=True) == "true"
    assert beliefs.claim_verdict(w.s, "player", "E999", self_claim=True) == "false_self"


def test_false_self_with_witness_loses_trust(w: World) -> None:
    w.act("player", "look")  # 书生亲眼见过玩家这一日的所作所为
    g = w.c("guest")
    j = beliefs.hear_claim(w.s, w.defs, g, "player", Claim(text="我打赢过黑脸汉子", verdict="false_self"))
    assert j.label == "disbelieve" and g.rel["player"].trust == 20 - 15


def test_unsourced_claims(w: World) -> None:
    g = w.c("guest")
    # 听者本来就知道是真的：信念不变，suspicion +25，记下「他怎么会知道」
    j = beliefs.hear_claim(
        w.s, w.defs, g, "player", Claim(text="你家里富贵", ref="F_guest_family", verdict="unsourced")
    )
    assert j.label == "unchanged" and g.mood["suspicion"] == 25
    assert "怎么会知道" in g.memory[-1].text
    # 听者本来不知道：置信度再减 0.2，suspicion +15
    f = w.c("fighter")
    j = beliefs.hear_claim(
        w.s, w.defs, f, "player", Claim(text="梁上有人", ref="F_girl_on_beam", verdict="unsourced")
    )
    assert f.beliefs["F_girl_on_beam"].confidence == 0.3 and f.mood["suspicion"] == 15
    assert "拿不出凭据" in j.text
    j = beliefs.hear_claim(w.s, w.defs, f, "player", Claim(text="明日要下雨", verdict="opinion"))
    assert j.label == "unchanged"


def test_player_claims_through_speech(w: World) -> None:
    evs = w.say(
        "player",
        "tell",
        ["fighter"],
        fact_refs=[],
        claims=[Claim(text="梁上藏着人", ref="F_girl_on_beam", verdict="unsourced")],
    )
    assert w.c("fighter").beliefs["F_girl_on_beam"].confidence == 0.3
    assert "你的判断" in evs[0].params["judgements"]["fighter"][0]


# ---------------------------------------------------------------- 6.8


def test_witnesses_same_place_conscious(w: World) -> None:
    ws = perception.witnesses(w.s, w.defs, "hall", "player")
    assert set(ws) == {"player", "fighter", "guest", "girl"} and set(ws.values()) == {"saw"}
    w.fx([{"add_status": ["guest", "asleep"]}])
    assert "guest" not in perception.witnesses(w.s, w.defs, "hall", "player")


def test_loud_heard_next_door(w: World) -> None:
    w.fx([{"move": ["guest", "yard"]}])
    evs = w.act("fighter", "attack", target="player", intent="injure")
    assert evs[0].witnesses["guest"] == "heard"
    assert w.c("guest").memory[-1].kind == "heard" and "响动" in w.c("guest").memory[-1].text
    assert "E" + str(evs[0].id) not in w.c("guest").beliefs  # 只听见声响，不知详情


def test_player_view(w: World) -> None:
    w.fx([{"move": ["guest", "yard"]}, {"move": ["fighter", "yard"]}])
    evs = w.act("fighter", "attack", target="guest", intent="injure")  # 在前院：玩家只听见
    w.fx([{"move": ["fighter", "hall"]}])
    evs += w.act("girl", "look")  # 梁上的人躲着：看不见
    evs += w.say("fighter", "greet", ["player"])
    v = perception.player_view(w.s, w.defs, evs)
    assert v.clock == "第一日 辰时" and v.location == "演武堂"
    assert v.location_desc.startswith("堂上地面光滑")
    assert len(v.heard) == 1 and "一处院子" in v.heard[0]
    texts = [e.text for e in v.events]
    assert len(texts) == 1 and "雷猛" in texts[0] and "向你" in texts[0]  # 自报家门之后知道名字
    assert v.names["fighter"] == "雷猛" and v.names["player"] == "你"
    assert v.present == ["雷猛"]
    w.fx(
        [
            {"injure": ["player", 1]},
            {"add_status": ["player", "poisoned_slow", {"ticks": 50}]},
            {"add_status": ["player", "hungry"]},
        ]
    )
    v = perception.player_view(w.s, w.defs, [])
    assert "身上的伤处隐隐作痛" in v.body and "腹中一阵阵绞痛" in v.body and "肚子饿得发慌" in v.body


def test_view_desc_by_time_of_day_and_traces(w: World) -> None:
    w.s.tick = 0
    w.fx([{"trace": ["hall", "地上有一摊血迹"]}])
    v = perception.player_view(w.s, w.defs, [])
    assert v.location_desc.startswith("堂上一片漆黑") and "血迹" in v.location_desc
    w.s.tick = 9 * 8
    assert perception.player_view(w.s, w.defs, []).location_desc.startswith("堂里点起了灯")


def test_names_learned_only_when_heard(w: World) -> None:
    assert refs.render("⟦guest⟧看着⟦girl⟧", w.s, w.defs, "player") == "白公子看着梁上那少女"
    assert refs.render("⟦guest⟧看着⟦girl⟧", w.s, w.defs) == "白逸看着阿翠"
    assert refs.render("⟦player⟧", w.s, w.defs, "player") == "你"
    assert refs.render("⟦player⟧", w.s, w.defs, "guest") == "阿木"
    assert refs.render("⟦hall#beam⟧", w.s, w.defs, "player") == "房梁"
    assert refs.render("⟦out_road⟧", w.s, w.defs, "player") == "一条出山的路"
    # 被直呼其名、听到事实里提到的名字
    w.say("guest", "greet", ["fighter"])
    assert "guest" in w.s.known_terms  # 自报家门：玩家在场听见
    w.fx([{"learn": ["guest", "F_girl_on_beam", 1.0, "saw"]}])
    w.say("guest", "tell", ["fighter"], fact_refs=["F_girl_on_beam"])
    assert "girl" in w.s.known_terms
    # 玩家不在场：听不到
    w2 = World()
    w2.c("player").loc = "cliff"
    w2.say("fighter", "greet", ["guest"])
    assert "fighter" not in w2.s.known_terms


def test_whisper_hides_names_from_bystanders(w: World) -> None:
    w.say("fighter", "greet", ["guest"], whisper=True)
    assert "fighter" not in w.s.known_terms
    v = perception.player_view(w.s, w.defs, w.s.events_tail[-1:])
    assert "低声" in v.events[0].text and v.events[0].speech == {
        "act": "whisper",
        "to": ["白公子"],
        "whisper": True,
    }


def test_learn_terms_from_event_helper(w: World) -> None:
    from tianlong.kernel import events

    ev = events.emit(
        w.s,
        w.defs,
        actor="guest",
        verb="say",
        text="x",
        source="mind",
        speech=__import__("tianlong.kernel.state", fromlist=["Speech"]).Speech(
            act="tell", to=["player"], names=["cliff"]
        ),
    )
    assert "cliff" in w.s.known_terms
    perception.learn_terms_from_event(w.s, w.defs, ev)  # 幂等
    assert w.s.known_terms.count("cliff") == 1


def test_referable_is_what_the_player_character_can_name_or_point_at(w: World) -> None:
    """玩家的角色说得出哪些东西（2.3、8.4）：这是唯一的一份判断，/心愿 的绑定与开口的选项都用它。"""
    st = w.s
    ok = perception.referable(st)
    # 人：看得见的、有来往的、有个叫法的说得出；躲在梁上、没见过也没听说过的说不出
    assert ok("fighter") and ok("guest") and not ok("girl")
    # 物：自己身上的、看得见的人明摆着带着的说得出；别人怀里没见过的、还没上场的说不出
    assert ok("rope") and ok("rations")
    assert not ok("antidote") and not ok("old_manual")
    # 地方：眼下所在与到过的；本事与别的专名：只凭知道名字；不认得的 id 一律说不出
    assert ok("hall") and not ok("cliff")
    assert not ok("cloud_steps") and not ok("no_such_thing") and not ok("")

    # 知道了名字、有了叫法，就说得出（人不在眼前、东西没见过也一样）
    w.fx([{"know_term": "girl"}, {"know_term": "cloud_steps"}, {"know_term": "cliff"}])
    ok = perception.referable(st)
    assert ok("girl") and ok("cloud_steps") and ok("cliff")

    # 细看过的人身上的东西说得出；没去过的地方走到了才说得出
    w2 = World()
    w2.act("player", "examine", target="guest")
    assert perception.referable(w2.s)("antidote")
    assert not perception.referable(w2.s)("yard")
    w2.act("player", "go", exit="yard")
    ok2 = perception.referable(w2.s)
    assert ok2("yard") and ok2("hall") and ok2("guest")  # 到过的地方、有来往的人，走开了照样说得出
    # 只是同处过、没有来往也没留下记忆的人，走开之后说不出；亲见过他做事、听人当面说起过他，才说得出
    assert not ok2("fighter") and not ok2("girl")
    me = w2.c("player")
    memory.write(w2.s, me, MemoryItem(tick=w2.s.tick, kind="saw", text="⟦fighter⟧练了一趟剑", entities=["fighter"], salience=3))
    memory.write(w2.s, me, MemoryItem(tick=w2.s.tick, kind="told", text="有人说起过⟦girl⟧", entities=["girl"], salience=3))
    memory.write(w2.s, me, MemoryItem(tick=w2.s.tick, kind="heard", text="远处一声响", entities=["sword"], salience=3))
    ok2 = perception.referable(w2.s)
    assert ok2("fighter") and ok2("girl") and not ok2("sword")  # 只听见响动的不算
