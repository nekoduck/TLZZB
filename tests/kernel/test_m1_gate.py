"""M1 的闸（方案 12）：不经模型，只用合法动作把 bout_laugh 玩通；能学会一门本事。"""

from __future__ import annotations

from tianlong.kernel import actions, effects, perception, predicates, resolve, timeflow

from .conftest import World


def open_if_due(w: World) -> None:
    """代替 world.tick 的第 2 步：条件成立而同类情境不在进行的，开场（只经效果）。"""
    for d in w.defs.situations.values():
        if d.when is None or any(s.kind == d.id for s in w.s.situations.values()):
            continue
        if predicates.holds(w.s, w.defs, d.when):
            resolve.run_effects(w.s, w.defs, [{"open": [d.id]}], source="situation")


def pick(w: World, actor: str, verb: str, name: str | None = None) -> actions.ActionInst:
    for a in actions.legal_actions(w.s, w.defs, actor):
        if a.verb == verb and (name is None or a.params.get("name") == name):
            return a
    raise AssertionError(
        f"{actor} has no {verb} {name}: {[x.desc for x in actions.legal_actions(w.s, w.defs, actor)]}"
    )


def test_bout_laugh_slap_path() -> None:
    w = World()
    open_if_due(w)
    assert list(w.s.situations) == ["bout_laugh#1"] and w.s.scene_mode == "tense"
    # 胜者：卖个破绽（★）
    a = pick(w, "fighter", "setpiece", "feint_win")
    assert a.star and a.cost == 0
    resolve.apply(w.s, w.defs, a, source="mind")
    # 客人：笑出声（★，出声）；赔礼不在 lean 里，也还不合法
    legal = actions.legal_actions(w.s, w.defs, "guest")
    laugh = next(x for x in legal if x.params.get("name") == "laugh")
    assert laugh.star and laugh.loud
    assert not any(x.params.get("name") == "apologize" for x in legal)
    evs = resolve.apply(w.s, w.defs, laugh, source="mind")
    assert evs[0].verb == "laugh" and w.c("fighter").mood["anger"] >= 20
    # 玩家眼里：介入的候选
    assert any("intervene" in x.tags for x in actions.legal_actions(w.s, w.defs, "player"))
    # 胜者：一掌（★）
    slap = pick(w, "fighter", "setpiece", "slap")
    assert slap.star
    evs = resolve.apply(w.s, w.defs, slap, source="mind")
    assert evs[0].verb == "slap" and w.status("guest", "injured").level == 1
    assert {"k": "harmed", "who": "guest", "by": "fighter"} in evs[0].kinds
    # 情境了结：exit_when 成立就散场，场面恢复平静
    assert w.s.situations == {} and w.s.scene_mode == "calm"
    assert w.s.flags["bout_slapped"] is True and w.s.flags["bout_running"] is False
    open_if_due(w)
    assert w.s.situations == {}  # 前提没了，不再开场
    view = perception.player_view(w.s, w.defs, w.s.events_tail[-3:])
    assert [e.text for e in view.events] == [
        "那个黑脸汉子卖个破绽，一招逼得对手认输",
        "白公子忍不住笑出了声",
        "那个黑脸汉子一掌打在白公子脸上",
    ]
    assert f"E{evs[0].id}" in w.c("girl").beliefs  # 可以转述的事
    assert w.s.step == 3


def test_bout_laugh_apology_path_after_warning() -> None:
    w = World()
    open_if_due(w)
    resolve.apply(w.s, w.defs, pick(w, "fighter", "setpiece", "feint_win"), source="mind")
    resolve.apply(w.s, w.defs, pick(w, "guest", "setpiece", "laugh"), source="mind")
    # 玩家扯了扯书生：低声示警
    w.say("player", "warn", ["guest"], gist="劝他快赔个不是", whisper=True)
    assert w.status("guest", "alerted") is not None
    legal = actions.legal_actions(w.s, w.defs, "guest")
    apo = next(x for x in legal if x.params.get("name") == "apologize")
    assert not apo.star  # 他本不想赔礼；有人劝过，才可能选它
    evs = resolve.apply(w.s, w.defs, apo, source="mind")
    assert evs[0].verb == "apologize"
    assert w.s.situations == {} and w.status("guest", "injured") is None
    assert "bout_slapped" not in w.s.flags


def test_bout_laugh_ends_when_parties_part() -> None:
    w = World()
    open_if_due(w)
    resolve.apply(w.s, w.defs, pick(w, "guest", "go"), source="mind")  # 客人走开：exit_when 的第三条
    assert w.s.situations == {}


def test_bout_laugh_max_turns() -> None:
    w = World()
    open_if_due(w)
    for _ in range(5):
        effects.tick_situations(w.s, w.defs)
    assert "bout_laugh#1" in w.s.situations
    effects.tick_situations(w.s, w.defs)
    assert w.s.situations == {}  # 到时还没了结就散场，不替任何人做决定
    assert w.status("guest", "injured") is None


def test_bout_laugh_offscreen() -> None:
    w = World()
    w.c("player").loc = "yard"
    evs = resolve.settle_offscreen(w.s, w.defs, "bout_laugh")
    assert w.s.situations == {} and w.status("guest", "injured").level == 1
    assert all(e.offscreen for e in evs) and evs
    assert "耳光" in w.s.locations["hall"].traces[0]
    assert all("player" not in e.witnesses for e in evs)
    # 玩家日后到场，只能从痕迹得知
    resolve.apply(w.s, w.defs, pick(w, "player", "go"), source="player")
    view = perception.player_view(w.s, w.defs, [])
    assert "耳光" in view.location_desc


def test_player_learns_an_art_by_study() -> None:
    w = World()
    resolve.apply(w.s, w.defs, pick(w, "player", "go"), source="player")
    a = next(
        x
        for x in actions.legal_actions(w.s, w.defs, "player")
        if x.verb == "search" and x.params.get("feature") == "woodpile"
    )
    resolve.apply(w.s, w.defs, a, source="player")
    a = next(x for x in actions.legal_actions(w.s, w.defs, "player") if x.verb == "take")
    resolve.apply(w.s, w.defs, a, source="player")
    assert w.s.items["old_manual"].holder == "player"
    study = next(
        x
        for x in actions.legal_actions(w.s, w.defs, "player")
        if x.verb == "study" and x.params["hours"] == 1
    )
    assert study.cost == 8
    for _ in range(2):
        resolve.apply(w.s, w.defs, study, source="player")
        timeflow.advance_time(w.s, w.defs, study.cost)
    assert w.c("player").arts["cloud_steps"] == 1
    assert w.s.tick == 32 + 16  # 两个时辰的研读
    assert any(x.verb == "practice" for x in actions.legal_actions(w.s, w.defs, "player"))


def test_onstage_wait_step_needs_takes() -> None:
    w = World()
    st = w.c("guest").agenda.steps[0]
    assert st.do == {"verb": "wait"} and st.takes == 4
    for i in range(4):
        a = pick(w, "guest", "wait")
        assert a.star
        evs = resolve.apply(w.s, w.defs, a, source="mind")
        assert evs[0].step == "g_watch"
        assert st.state == ("done" if i == 3 else "pending")
    assert w.c("guest").agenda.status == "done" and "guest" in w.s.replan_queue
