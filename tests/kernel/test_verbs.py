"""方案 6.1：每一个动词（含情境动作 setpiece 与挣扎 struggle）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tianlong.kernel import actions, rng
from tianlong.kernel.state import Speech

from .conftest import World


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把 d100 换成给定的点数序列（用完以后一律 50）。"""
    seq: list[int] = []

    def roll(state):  # type: ignore[no-untyped-def]
        state.rng_counter += 1
        return seq.pop(0) if seq else 50

    monkeypatch.setattr(rng, "roll", roll)

    def set_(*vals: int) -> None:
        seq[:] = list(vals)

    return set_


def _verbs(evs) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.verb for e in evs]


def test_every_verb_has_a_spec() -> None:
    assert set(actions.VERBS) >= {
        "go",
        "follow",
        "flee",
        "hide",
        "look",
        "listen",
        "examine",
        "search",
        "say",
        "take",
        "drop",
        "give",
        "show",
        "use",
        "steal",
        "help",
        "restrain",
        "release",
        "attack",
        "block",
        "push",
        "study",
        "practice",
        "teach",
        "wait",
        "rest",
        "setpiece",
        "struggle",
    }


def test_go_and_cost(w: World) -> None:
    a = w.build("player", "go", exit="yard")
    assert a is not None and a.cost == 1 and a.desc == "往⟦yard⟧去"
    evs = w.act("player", "go", exit="yard")
    assert _verbs(evs) == ["go", "arrive"] and w.c("player").loc == "yard"
    assert "fighter" in evs[0].witnesses and "fighter" not in evs[1].witnesses
    assert "yard" in w.s.visited
    assert w.build("player", "go", exit="cliff").cost == 4
    assert w.build("player", "go", exit="moon") is None


def test_go_with_rope_descends(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.c("player").loc = "cliff"
    a = w.build("player", "go", exit="yard")
    assert a is not None and not a.lethal  # 有绳：30 + 45 = 75
    dice(1)
    w.act("player", "go", exit="yard")
    assert w.c("player").loc == "yard" and w.status("player", "injured") is None


def test_descend_fall_and_survive(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.c("player").loc = "cliff"
    dice(100, 1)  # 失手，坠落活下来
    evs = w.act("player", "go", exit="yard")
    assert "fall" in _verbs(evs) and "land" in _verbs(evs)
    assert w.c("player").loc == "yard" and w.status("player", "injured").level == 2


def test_descend_without_rope_is_lethal_warn_then_resolve(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.fx([{"remove": "rope"}])
    w.c("player").loc = "cliff"
    a = w.build("player", "go", exit="yard")
    assert a is not None and a.lethal  # 30 < 50
    evs = w.act("player", "go", exit="yard")
    assert _verbs(evs) == ["warn_danger"] and evs[0].outcome == "blocked"
    assert w.c("player").loc == "cliff" and a.signature in w.s.warned
    dice(100, 100)  # 再做一次才真正结算：失手，坠落也没活下来 → 第一次降级为重伤
    w.act("player", "go", exit="yard")
    assert w.status("player", "dead") is None and w.status("player", "injured").level == 3
    assert "kill:fall:cliff" in w.s.warned


def test_hidden_exit_needs_knowledge(w: World) -> None:
    w.c("player").loc = "cliff"
    assert w.build("player", "go", exit="hall") is None
    w.act("player", "examine", feature="pine")
    assert "feature:cliff#pine" in w.s.examined
    a = w.build("player", "go", exit="hall")
    assert a is not None and a.cost == 3


def test_edge_exit_leaves_the_map(w: World) -> None:
    w.c("player").loc = "yard"
    a = w.build("player", "go", exit="out_road")
    assert a is not None and "离开这一带" in a.desc
    w.act("player", "go", exit="out_road")
    assert w.c("player").onstage is False and w.s.ended == "left"


def test_follow(w: World) -> None:
    w.act("player", "follow", target="guest")
    assert w.c("player").following == "guest"
    assert w.build("player", "follow", target="guest").desc == "不再跟着⟦guest⟧"
    w.act("guest", "go", exit="yard")
    assert w.c("player").loc == "yard"
    w.act("player", "follow", target="guest")
    assert w.c("player").following is None


def test_flee_needs_threat_then_check(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    assert w.build("player", "flee", exit="yard") is None
    w.c("fighter").rel["player"] = w.c("fighter").rel.get("player") or type(w.c("fighter").rel["guest"])()
    w.c("fighter").rel["player"].affinity = -50
    a = w.build("player", "flee", exit="yard")
    assert a is not None and a.cost == 0
    dice(100)
    evs = w.act("player", "flee", exit="yard")
    assert evs[0].outcome == "fail" and w.c("player").loc == "hall"
    assert w.status("player", "injured") is not None  # 被追上：追者有敌意，打伤一级
    dice(1)
    w.act("player", "flee", exit="yard")
    assert w.c("player").loc == "yard"


def test_hide_and_unseen_actions(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    dice(1)
    evs = w.act("player", "hide", feature="beam")
    assert w.status("player", "hidden") is not None
    assert set(evs[0].witnesses) == {"player"}
    evs = w.act("player", "look")
    assert set(evs[0].witnesses) == {"player"}  # 躲着的人的动作别人看不见
    assert not w.has("fighter", "examine", target="player")
    dice(100)
    w.c("player").statuses = []
    evs = w.act("player", "hide", feature="beam")
    assert evs[0].outcome == "fail" and w.c("fighter").mood.get("suspicion", 0) >= 20


def test_look_and_listen(w: World) -> None:
    evs = w.act("player", "look")
    assert evs[0].verb == "look"
    w.fx([{"move": ["guest", "yard"]}])
    evs = w.act("player", "listen")
    assert evs[0].params["heard_lines"] == ["⟦yard⟧那边有人声"]


def test_examine_item_feature_person(w: World) -> None:
    w.fx([{"spawn": ["old_manual", "player"]}])
    w.act("player", "examine", item="old_manual")
    assert "old_manual" in w.s.known_terms and "cloud_steps" in w.s.known_terms
    assert w.c("player").beliefs["F_manual_contents"].confidence == 1.0
    w.act("player", "examine", target="fighter")
    assert "char:fighter" in w.s.examined
    w.act("player", "examine", feature="rack")
    assert "feature:hall#rack" in w.s.examined


def test_search_feature_finds_items(w: World) -> None:
    w.c("player").loc = "yard"
    assert not w.has("player", "take", item="old_manual")
    evs = w.act("player", "search", feature="woodpile")
    assert evs[0].outcome == "ok" and w.s.items["old_manual"].feature is None
    assert w.has("player", "take", item="old_manual")
    evs = w.act("player", "search", feature="well")
    assert evs[0].outcome == "fail"


def test_search_place_finds_hidden_people_and_exits(w: World) -> None:
    assert not w.has("player", "examine", target="girl")
    evs = w.act("player", "search")
    assert w.status("girl", "hidden") is None
    assert any(e.verb == "spot" for e in evs)
    assert w.has("player", "examine", target="girl")
    w.c("player").loc = "cliff"
    w.act("player", "search")
    assert w.c("player").beliefs["exit:cliff:hall"].confidence == 1.0


def test_say_is_speech_with_wait(w: World) -> None:
    evs = w.say("player", "greet", ["guest"])
    assert _verbs(evs) == ["say"] and evs[0].speech.act == "greet"


def test_take_drop_give_show(w: World) -> None:
    w.act("player", "drop", item="rope")
    assert w.s.items["rope"].loc == "hall"
    w.act("fighter", "take", item="rope")
    assert w.s.items["rope"].holder == "fighter"
    assert w.build("player", "take", item="rope") is None  # 东西只有一件
    w.act("player", "give", item="rations", target="guest")
    assert w.s.items["rations"].holder == "guest"
    assert w.c("guest").rel["player"].trust == 30  # 被帮了忙 +10
    w.act("guest", "show", item="token", target="fighter")
    assert w.s.items["token"].holder == "guest"


def test_use_food_antidote_poison(w: World) -> None:
    w.fx([{"add_status": ["player", "hungry"]}])
    w.act("player", "use", item="rations")
    assert w.status("player", "hungry") is None and not w.s.items["rations"].in_world
    w.fx([{"add_status": ["fighter", "poisoned_slow", {"ticks": 50}]}])
    w.act("guest", "use", item="antidote", target="fighter")  # 对清醒的人只能用无害的东西
    assert w.status("fighter", "poisoned_slow") is None
    assert w.c("fighter").rel["guest"].trust == 30  # 救命：+30
    a = w.build("fighter", "use", item="poison")
    assert a is not None and a.lethal
    assert w.build("fighter", "use", item="poison", target="guest") is None  # 不能硬灌清醒的人
    w.fx([{"add_status": ["guest", "bound"]}])
    w.act("fighter", "use", item="poison", target="guest")
    s = w.status("guest", "poisoned_slow")
    assert s is not None and s.source == "fighter" and s.until == w.s.tick + 192


def test_steal_ok_and_caught(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    assert w.build("player", "steal", item="token", target="guest") is None  # 没看见过的东西无从下手（K9）
    w.act("player", "examine", target="guest")
    dice(1)
    evs = w.act("player", "steal", item="token", target="guest")
    assert w.s.items["token"].holder == "player" and set(evs[0].witnesses) == {"player"}
    dice(100)
    evs = w.act("player", "steal", item="antidote", target="guest")
    assert evs[0].outcome == "fail" and w.s.items["antidote"].holder == "guest"
    assert w.c("guest").rel["player"].trust == -10  # 20 - 30
    assert w.c("guest").mood["anger"] == 25


def test_help_and_rest_heal(w: World) -> None:
    assert w.build("player", "help", target="guest") is None
    w.fx([{"injure": ["player", 1]}])
    w.act("guest", "help", target="player")
    assert w.status("player", "tended") is not None
    w.act("player", "rest", hours=2)
    assert w.status("player", "injured") is None and w.status("player", "asleep") is not None


def test_restrain_release_struggle(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    evs = w.act("fighter", "restrain", target="guest")
    assert w.status("guest", "bound") is not None and evs[0].params["winner"] == "fighter"
    assert {a.verb for a in w.legal("guest")} == {"wait", "struggle"}  # 被缚：只能说话、等待、挣扎
    dice(100)
    w.act("guest", "struggle")
    assert w.status("guest", "bound") is not None
    w.act("player", "release", target="guest")
    assert w.status("guest", "bound") is None
    assert w.c("guest").rel["player"].trust == 50  # 20 + 30（得救）
    a = w.build("player", "restrain", target="fighter")
    assert a is not None and a.lethal


def test_attack_block_push(w: World) -> None:
    evs = w.act("fighter", "attack", target="guest", intent="injure")
    assert w.status("guest", "injured").level == 1
    assert evs[0].loud and evs[0].params["winner"] == "fighter"
    assert w.c("player").rel["fighter"].regard == 2  # 亲见动手：regard = 所见的 power
    assert w.c("player").rel["fighter"].fear == 15  # 高出自己 2 以上
    w.act("player", "block", target="fighter")
    assert w.status("fighter", "impeded").source == "player"
    evs = w.act("player", "push", target="fighter")
    assert evs[0].outcome == "fail"  # 推不动比自己强得多的人
    w.act("guest", "push", target="player")
    assert w.status("player", "impeded") is not None


def test_study_practice_teach(w: World) -> None:
    w.fx([{"spawn": ["old_manual", "player"]}])
    w.act("player", "study", item="old_manual", art="cloud_steps", hours=1)
    assert w.c("player").art_progress["cloud_steps"] == 10  # 8 × (1 + 0.25 × 1)
    w.act("player", "study", item="old_manual", art="cloud_steps", hours=1)
    assert w.c("player").arts["cloud_steps"] == 1
    assert w.build("player", "study", item="old_manual", art="cloud_steps", hours=1) is None  # 第一层之后靠练
    w.act("player", "practice", art="cloud_steps", hours=4)
    assert w.c("player").arts["cloud_steps"] == 2
    assert w.build("player", "practice", art="cloud_steps", hours=1) is None  # 到顶了
    # 传授：要么是对方的上司，要么对方求教、已经答应
    assert w.build("fighter", "teach", target="player", art="breath", hours=1) is None
    w.c("player").superior = "fighter"
    evs = w.act("fighter", "teach", target="player", art="breath", hours=1)
    assert w.c("player").art_progress["breath"] == 15  # 有人教 × 1.5
    assert {"k": "taught", "who": "player", "by": "fighter"} in evs[0].kinds
    assert w.c("player").rel["fighter"].trust == 15


def test_wait_rest_and_tense_costs(w: World) -> None:
    assert w.build("player", "wait", ticks=8).cost == 8
    assert w.build("player", "rest", hours=2).cost == 16
    w.fx([{"open": ["bout_laugh"]}])
    assert w.s.scene_mode == "tense"
    assert w.build("player", "wait", ticks=8).cost == 0
    assert w.build("player", "go", exit="yard").cost == 0
    for v in ("rest", "study", "practice", "teach"):
        assert all(a.verb != v for a in w.legal("player"))
    step0 = w.s.step
    w.act("player", "look")
    assert w.s.step == step0 + 1


def test_setpiece(w: World) -> None:
    w.fx([{"open": ["bout_laugh"]}])
    legal = w.legal("fighter")
    sp = [a for a in legal if a.verb == "setpiece"]
    assert [a.params["name"] for a in sp] == ["feint_win"] and sp[0].star and legal[0].star
    assert all(a.verb != "setpiece" for a in w.legal("player"))
    evs = w.act("fighter", "setpiece", situation="bout_laugh#1", name="feint_win")
    assert evs[0].verb == "feint_win" and evs[0].actor == "fighter"
    assert w.s.flags["bout_laugh#1.bout_over"] is True


def test_blocked_action_records_event(w: World) -> None:
    a = w.build("player", "take", item="lucky_charm")
    assert a is None
    a = w.build("player", "go", exit="yard")
    w.c("player").statuses.append(type(w.c("girl").statuses[0])(kind="captive"))
    from tianlong.kernel import resolve

    evs = resolve.apply(w.s, w.defs, a, source="player")
    assert evs[0].outcome == "blocked" and w.c("player").loc == "hall"


def test_legal_actions_order_and_model_cap(w: World) -> None:
    legal = w.legal("fighter")
    assert legal[0].star and legal[0].verb == "wait"  # 当前这一步是 wait
    assert [a.id for a in legal[:3]] == ["a01", "a02", "a03"]
    capped = w.legal("fighter", for_model=True)
    assert len(capped) <= 12 and any(a.verb == "wait" for a in capped)
    p = w.legal("player")
    assert p[0].verb == "wait" and len(p) > 12  # 玩家不压缩


def test_star_go_on_shortest_path(w: World) -> None:
    w.s.tick = 64
    w.fx([{"step_state": ["fighter", "f_watch", "done"]}])
    legal = w.legal("fighter")
    assert legal[0].star and legal[0].verb == "go" and legal[0].params["exit"] == "yard"
    w.c("fighter").loc = "cliff"
    w.c("fighter").beliefs.clear()
    path = actions.path(w.s, w.defs, "fighter", "cliff", "yard")
    assert [e.to for e in path] == ["yard"]


def test_bottom_lines_filter_actions(w: World) -> None:
    assert not any(a.verb == "attack" for a in w.legal("guest"))  # 不对人动手
    w.c("girl").statuses = []
    w.c("girl").rel["guest"].affinity = 40
    w.fx([{"rel": ["fighter", "girl", "affinity", -80]}])
    assert not any(a.verb == "flee" for a in w.legal("girl"))  # 朋友在场，不独自逃命
    w.fx([{"move": ["guest", "yard"]}])
    assert any(a.verb == "flee" for a in w.legal("girl"))


def test_speech_and_action_together(w: World) -> None:
    a = w.build("player", "go", exit="yard")
    from tianlong.kernel import resolve

    evs = resolve.apply(w.s, w.defs, a, Speech(act="greet", to=["guest"]), source="player")
    assert _verbs(evs) == ["say", "go", "arrive"]


def _iter_all(w: World) -> Iterator[str]:
    yield from (a.verb for a in w.legal("player"))


def test_more_verb_corners(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    # 细听：近处的低语片段
    w.fx([{"learn": ["fighter", "F_girl_stranger", 1.0, "saw"]}])
    w.say("fighter", "tell", ["guest"], fact_refs=["F_girl_stranger"], whisper=True)
    evs = w.act("player", "listen")
    assert any("低声" in line for line in evs[0].params["heard_lines"])
    assert w.c("player").beliefs["F_girl_stranger"].confidence == 0.5
    # 被扣押（没被捆）：挣扎脱身；放人
    w.fx([{"add_status": ["guest", "captive", {"source": "fighter"}]}])
    assert w.build("guest", "go", exit="yard") is None
    assert w.build("guest", "flee", exit="yard") is not None
    dice(1)
    w.act("guest", "struggle")
    assert w.status("guest", "captive") is None
    w.fx([{"add_status": ["guest", "captive", {"source": "fighter"}]}])
    w.act("player", "release", target="guest")
    assert w.status("guest", "captive") is None
    # 推景物：有 push 效果的才推得动
    from tianlong.kernel.state import Feature

    w.s.locations["hall"].features["rack"] = Feature(
        id="rack", name="兵器架", push=[{"set_flag": ["rack_down", True]}]
    )
    w.act("player", "push", feature="rack")
    assert w.s.flags["rack_down"] is True
    # 用东西：on_use 里 $target 指对象
    w.s.items["rope"].props = {"on_use": [{"add_status": ["$target", "bound", {"source": "self"}]}]}
    w.fx([{"add_status": ["fighter", "unconscious"]}])
    w.act("player", "use", item="rope", target="fighter")
    assert w.status("fighter", "bound") is not None and w.status("fighter", "bound").source == "player"


def test_flee_through_check_exit(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.c("player").loc = "cliff"
    w.c("fighter").loc = "cliff"
    w.fx([{"rel": ["fighter", "player", "affinity", -60]}])
    a = w.build("player", "flee", exit="yard")
    assert a is not None and not a.lethal
    dice(1, 1)  # 逃脱，缒下崖也成
    w.act("player", "flee", exit="yard")
    assert w.c("player").loc == "yard"
