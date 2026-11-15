"""方案 5.3：每一个效果。"""

from __future__ import annotations

import pytest

from tianlong.kernel import effects, timeflow
from tianlong.kernel.effects import EFFECT_SHAPES, EffectError

from .conftest import World


def test_set_flag_and_local(w: World) -> None:
    w.fx([{"set_flag": ["a", 1]}])
    assert w.s.flags["a"] == 1
    w.fx([{"set_flag": [".x", True]}], situation="s#1")
    assert w.s.flags["s#1.x"] is True


def test_move_enter_leave(w: World) -> None:
    evs = w.fx([{"move": ["guest", "yard"]}])
    assert w.c("guest").loc == "yard"
    assert evs[0].verb == "arrive" and evs[0].loc == "yard"
    w.fx([{"leave": "guest"}])
    assert w.c("guest").onstage is False
    evs = w.fx([{"enter": ["guest", "cliff"]}])
    assert w.c("guest").onstage and w.c("guest").loc == "cliff" and evs[0].verb == "enter"
    with pytest.raises(EffectError):
        w.fx([{"move": ["guest", "moon"]}])


def test_move_takes_followers_along(w: World) -> None:
    w.fx([{"follow": ["player", "guest"]}])
    evs = w.fx(
        [{"move": ["guest", "yard"]}, {"emit": {"actor": "guest", "verb": "go", "text": "⟦guest⟧走了"}}]
    )
    assert w.c("player").loc == "yard"
    assert "yard" in w.s.visited
    assert any(e.verb == "follow" and e.actor == "player" for e in evs)


def test_give_spawn_remove(w: World) -> None:
    evs = w.fx([{"give": ["player", "guest", "rope"]}])
    assert w.s.items["rope"].holder == "guest" and "rope" in w.c("guest").inventory
    assert "rope" not in w.c("player").inventory
    kinds = {k["k"] for e in evs for k in e.kinds}
    assert {"gift", "item_change"} <= kinds
    # 不在 from 手里：跳过并记一笔
    w.fx([{"give": ["player", "fighter", "rope"]}])
    assert w.s.items["rope"].holder == "guest" and w.s.violations
    w.fx([{"give": ["guest", "hall", "rope"]}])
    assert w.s.items["rope"].loc == "hall" and w.s.items["rope"].holder is None
    w.fx([{"give": [None, "player", "rope"]}])
    assert w.s.items["rope"].holder == "player"
    w.fx([{"spawn": ["lucky_charm", "yard"]}])
    assert w.s.items["lucky_charm"].in_world and w.s.items["lucky_charm"].loc == "yard"
    w.fx([{"remove": "rope"}])
    it = w.s.items["rope"]
    assert not it.in_world and it.holder is None and "rope" not in w.c("player").inventory


def test_on_obtain_runs_once(w: World) -> None:
    luck = w.c("player").luck
    w.fx([{"spawn": ["lucky_charm", "player"]}])
    assert w.c("player").luck == luck + 2
    w.fx([{"give": ["player", "guest", "lucky_charm"]}, {"give": ["guest", "player", "lucky_charm"]}])
    assert w.c("player").luck == luck + 2


def test_status_effects_and_immunity(w: World) -> None:
    w.fx([{"add_status": ["guest", "poisoned_slow", {"ticks": 10, "source": "fighter"}]}])
    s = w.status("guest", "poisoned_slow")
    assert s.until == w.s.tick + 10 and s.source == "fighter"
    assert "guest" in w.s.replan_queue
    w.fx([{"remove_status": ["guest", "poisoned_slow"]}])
    assert w.status("guest", "poisoned_slow") is None
    w.fx(
        [
            {"add_status": ["guest", "immune_poison"]},
            {"add_status": ["guest", "poisoned_slow", {"until": 99}]},
        ]
    )
    assert w.status("guest", "poisoned_slow") is None
    w.fx([{"add_status": ["player", "bound"]}])
    assert w.status("player", "bound") is not None


def test_role_rel_learn(w: World) -> None:
    w.fx([{"role": ["player", "hall_sect", "add"]}])
    assert "hall_sect" in w.c("player").roles
    w.fx([{"role": ["player", "hall_sect", "remove"]}])
    assert "hall_sect" not in w.c("player").roles
    w.fx([{"rel": ["fighter", "player", "trust", 150]}])
    assert w.c("fighter").rel["player"].trust == 100  # 夹在范围之内
    w.fx([{"learn": ["player", "F_girl_on_beam", 0.7, "told:guest"]}])
    b = w.c("player").beliefs["F_girl_on_beam"]
    assert b.confidence == 0.7 and b.source == "told:guest"
    assert "girl" in w.s.known_terms  # 玩家相信的事实里提到的专名
    w.fx([{"learn": ["player", "F_girl_on_beam", 0.4, "told:x"]}])
    assert w.c("player").beliefs["F_girl_on_beam"].confidence == 0.7  # 只升不降
    with pytest.raises(EffectError):
        w.fx([{"learn": ["player", "F_nothing"]}])


def test_train_art_stat(w: World) -> None:
    w.fx([{"train": ["player", "cloud_steps", 16]}])
    assert w.c("player").arts["cloud_steps"] == 1
    w.fx([{"train": ["player", "cloud_steps", 31]}])
    assert w.c("player").arts["cloud_steps"] == 1 and w.c("player").art_progress["cloud_steps"] == 31
    w.fx([{"train": ["player", "cloud_steps", 1]}])
    assert w.c("player").arts["cloud_steps"] == 2
    w.fx([{"art": ["guest", "hall_sword", 2]}])
    assert w.c("guest").arts["hall_sword"] == 2
    w.fx([{"stat": ["player", "neili", 30]}, {"stat": ["player", "luck", 100]}])
    assert w.c("player").neili == 30 and w.c("player").luck == 20
    with pytest.raises(EffectError):
        w.fx([{"stat": ["player", "charm", 1]}])


def test_step_and_replan(w: World) -> None:
    w.fx([{"step": ["fighter", {"id": "x1", "text": "去前院", "at": "yard", "do": {"verb": "wait"}}]}])
    ag = w.c("fighter").agenda
    assert ag.steps[ag.cursor].id == "x1" and ag.steps[ag.cursor].origin == "rule"
    w.fx([{"step": ["player", {"text": "逃命", "do": {"verb": "flee"}}]}])
    assert w.c("player").agenda is not None and w.c("player").agenda.steps[0].do == {"verb": "flee"}
    w.fx([{"replan": "guest"}])
    assert "guest" in w.s.replan_queue
    w.fx([{"step_state": ["fighter", "x1", "void"]}])
    assert ag.steps[0].state == "void" and ag.cursor == 1


def test_open_close_situation_with_roles(w: World) -> None:
    w.fx([{"open": ["bout_laugh"]}])
    s = w.s.situations["bout_laugh#1"]
    assert s.loc == "hall" and s.roles == {"victor": "fighter", "guest": "guest"}
    assert w.s.scene_mode == "tense"
    w.fx([{"open": ["bout_laugh"]}])  # 同一场不重复开
    assert len(w.s.situations) == 1
    w.s.flags["bout_laugh#1.x"] = 1
    w.fx([{"close": "bout_laugh#1"}])
    assert w.s.situations == {} and "bout_laugh#1.x" not in w.s.flags and w.s.scene_mode == "calm"
    # pick 角色位：在那个地点的活人里挑第一个使 where 成立的人
    w.fx([{"open": ["scuffle"]}])
    sc = next(iter(w.s.situations.values()))
    assert sc.roles == {"a": "fighter", "b": "player"}
    w.fx([{"close": "scuffle"}])  # 按种类散场
    assert w.s.situations == {}
    # 显式绑定角色位
    w.fx([{"open": ["scuffle", "hall", {"b": "guest"}]}])
    assert next(iter(w.s.situations.values())).roles["b"] == "guest"


def test_open_fails_softly_when_roles_unbindable(w: World) -> None:
    w.c("fighter").loc = "yard"
    w.fx([{"open": ["scuffle"]}])  # 堂上没有 hall_sect 的人
    assert w.s.situations == {} and any("not bindable" in v for v in w.s.violations)


def test_thread_and_close_thread(w: World) -> None:
    w.fx([{"thread": {"kind": "lead", "text": "{x}知道一条小路", "refs": ["$x"]}}], roles={"x": "girl"})
    t = [t for t in w.s.threads.values() if t.kind == "lead"][0]
    assert t.text == "⟦girl⟧知道一条小路" and t.refs == ["girl"]
    w.fx([{"close_thread": t.id}])
    assert t.closed


def test_emit_fills_roles(w: World) -> None:
    evs = w.fx(
        [{"emit": {"actor": "$v", "verb": "shout", "text": "{v}朝{g}大喝一声", "loud": True}}],
        roles={"v": "fighter", "g": "guest"},
    )
    e = evs[0]
    assert e.fact_text == "⟦fighter⟧朝⟦guest⟧大喝一声" and e.actor == "fighter" and e.loud
    assert e.id == 1 and w.s.next_event_id == 2
    assert e.params["sound"]


def test_injure_and_kill(w: World) -> None:
    evs = w.fx([{"injure": ["guest", 2]}], self_id="fighter")
    assert w.status("guest", "injured").level == 2
    assert evs[0].verb == "injured" and evs[0].kinds[0] == {"k": "harmed", "who": "guest", "by": "fighter"}
    w.fx([{"injure": ["guest", 5]}])
    assert w.status("guest", "injured").level == 3
    w.s.requests  # noqa: B018
    w.fx([{"kill": "guest"}], self_id="fighter")
    g = w.c("guest")
    assert w.status("guest", "dead") is not None
    assert g.inventory == [] and w.s.items["token"].loc == "hall"
    assert all(st.state != "pending" for st in g.agenda.steps)
    assert "player" in w.s.replan_queue or "fighter" in w.s.replan_queue or "girl" in w.s.replan_queue


def test_kill_player_buffer(w: World) -> None:
    w.fx([{"kill": "player"}], self_id="fighter")
    assert w.status("player", "dead") is None
    assert w.status("player", "injured").level == 3
    assert "kill:fighter" in w.s.warned
    w.fx([{"kill": "player"}], self_id="fighter")
    assert w.status("player", "dead") is not None and w.s.ended == "dead"


def test_check_effect_branches(w: World) -> None:
    w.fx(
        [
            {
                "check": {
                    "kind": "chance",
                    "chance": 100,
                    "who": "player",
                    "ok": [{"set_flag": ["ok", 1]}],
                    "fail": [{"set_flag": ["bad", 1]}],
                }
            }
        ]
    )
    assert w.s.flags.get("ok") == 1 and "bad" not in w.s.flags
    w.fx(
        [
            {
                "check": {
                    "kind": "death_save",
                    "who": "player",
                    "ok": [{"set_flag": ["saved", 1]}],
                    "fail": [{"set_flag": ["lost", 1]}],
                }
            }
        ]
    )
    assert ("saved" in w.s.flags) != ("lost" in w.s.flags)
    with pytest.raises(EffectError):
        w.fx([{"check": {"kind": "dance", "who": "player"}}])


def test_schedule_with_pre(w: World) -> None:
    w.fx(
        [
            {"schedule": [2, [{"set_flag": ["later", 1]}], {"alive": "guest"}]},
            {"schedule": [2, [{"set_flag": ["never", 1]}], {"alive": "nobody"}]},
        ]
    )
    timeflow.advance_time(w.s, w.defs, 1)
    assert "later" not in w.s.flags
    timeflow.advance_time(w.s, w.defs, 1)
    assert w.s.flags.get("later") == 1 and "never" not in w.s.flags


def test_schedule_keeps_roles(w: World) -> None:
    w.fx([{"schedule": [1, [{"injure": ["$g", 1]}], None]}], roles={"g": "guest"})
    timeflow.advance_time(w.s, w.defs, 1)
    assert w.status("guest", "injured") is not None


def test_scene_trace_end(w: World) -> None:
    w.fx([{"scene": "tense"}])
    assert w.s.scene_mode == "tense"
    w.fx([{"trace": ["hall", "{g}留下了一摊血迹"]}], roles={"g": "guest"})
    assert w.s.locations["hall"].traces == ["⟦guest⟧留下了一摊血迹"]
    w.fx([{"end": "closed"}])
    assert w.s.ended == "closed"
    with pytest.raises(EffectError):
        w.fx([{"end": "whatever"}])


def test_kernel_extras(w: World) -> None:
    w.fx(
        [
            {"mood": ["guest", "anger", 30]},
            {"warn": "k1"},
            {"know_term": "cliff"},
            {"mark": ["examined", "x"]},
        ]
    )
    assert w.c("guest").mood["anger"] == 30 and "k1" in w.s.warned and "cliff" in w.s.known_terms
    assert "x" in w.s.examined
    w.fx([{"add_status": ["player", "hungry"]}, {"eat": "player"}])
    assert w.status("player", "hungry") is None and w.c("player").last_meal == w.s.tick
    w.fx([{"injure": ["player", 2]}, {"add_status": ["player", "tended"]}, {"rest": ["player", 16]}])
    assert w.status("player", "injured").level == 1


def test_implicit_events_merge_into_emit(w: World) -> None:
    evs = w.fx(
        [
            {"injure": ["guest", 1]},
            {"emit": {"actor": "fighter", "verb": "slap", "text": "⟦fighter⟧打了⟦guest⟧"}},
        ],
        self_id="fighter",
    )
    assert len(evs) == 1
    assert {"k": "harmed", "who": "guest", "by": "fighter"} in evs[0].kinds
    assert evs[0].salience == 9


def test_unknown_effect_raises(w: World) -> None:
    with pytest.raises(EffectError):
        effects.apply_effects(w.s, w.defs, [{"fly": "player"}], source="rule")


def test_every_effect_key_is_tested() -> None:
    import inspect
    from pathlib import Path

    src = inspect.getsource(inspect.getmodule(test_every_effect_key_is_tested))  # type: ignore[arg-type]
    src += (Path(__file__).parent / "test_bookkeeping_effects.py").read_text(encoding="utf-8")
    for key in EFFECT_SHAPES:
        assert f"'{key}'" in src or f'"{key}"' in src, key
