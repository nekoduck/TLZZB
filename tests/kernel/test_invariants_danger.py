"""方案 6.11 的不变量与回滚；6.10 的危险、致命动作与对玩家的缓冲。"""

from __future__ import annotations

import pytest

from tianlong.kernel import danger, invariants, resolve, rng
from tianlong.kernel.state import Agenda, Belief, Request, Status, Step, WorldState

from .conftest import World


def test_clean_at_start(w: World) -> None:
    assert invariants.check(w.s, w.defs) == []
    assert invariants.liveness(w.s, w.defs) == []


@pytest.mark.parametrize(
    "breaker, needle",
    [
        (lambda s: setattr(s.chars["guest"], "loc", "moon"), "unknown location"),
        (lambda s: s.chars["guest"].inventory.append("rope"), "held by both"),
        (lambda s: setattr(s.items["rope"], "loc", "hall"), "exactly one of holder/loc"),
        (
            lambda s: s.chars["guest"].beliefs.__setitem__("F_x", Belief(confidence=1, source="saw", tick=0)),
            "unknown fact",
        ),
        (lambda s: setattr(s.chars["guest"].rel["player"], "trust", 300), "out of range"),
        (lambda s: setattr(s.chars["guest"], "neili", -1), "out of range"),
        (lambda s: s.chars["guest"].mood.__setitem__("anger", 101), "out of range"),
        (lambda s: s.chars["guest"].statuses.append(Status(kind="injured", level=4)), "injured level"),
        (lambda s: setattr(s.items["lucky_charm"], "loc", "hall"), "out of the world but placed"),
    ],
)
def test_each_invariant_detected(w: World, breaker, needle: str) -> None:  # type: ignore[no-untyped-def]
    breaker(w.s)
    assert any(needle in p for p in invariants.check(w.s, w.defs))


def test_event_and_request_invariants(w: World) -> None:
    w.act("player", "look")
    w.act("player", "look")
    w.s.events_tail.pop(0)
    assert invariants.check(w.s, w.defs) == []
    w.s.next_event_id += 1
    assert any("next_event_id" in p for p in invariants.check(w.s, w.defs))
    w.s.next_event_id -= 1
    w.s.requests["R9"] = Request(
        id="R9", frm="player", to="guest", action={"verb": "wait"}, action_class="trivial"
    )
    w.c("guest").statuses.append(Status(kind="dead"))
    assert any("is open but guest is dead" in p for p in invariants.check(w.s, w.defs))
    assert any("went back" in p for p in invariants.check(w.s, w.defs, tick_before=w.s.tick + 1))


def test_new_event_invariants(w: World) -> None:
    evs = w.say("guest", "tell", ["player"], fact_refs=["F_bout"])
    w.c("guest").beliefs.pop("F_bout")
    assert any("without believing" in p for p in invariants.check(w.s, w.defs, new_events=evs))
    ev = evs[0].model_copy(update={"step": "x9", "speech": None})
    assert any(
        "finished or void step" in p
        for p in invariants.check(w.s, w.defs, new_events=[ev], pending_before=set())
    )
    assert any(
        "dead or off the map" in p for p in invariants.check(w.s, w.defs, new_events=[ev], able_before=set())
    )


def test_rollback_on_effect_error(w: World) -> None:
    before = w.s.model_dump_json()
    evs = resolve.run_effects(
        w.s,
        w.defs,
        [{"set_flag": ["x", 1]}, {"move": ["guest", "yard"]}, {"learn": ["player", "F_nope"]}],
        source="rule",
    )
    assert evs == []
    after = WorldState.model_validate_json(w.s.model_dump_json())
    after.violations = []
    assert after.model_dump_json() == WorldState.model_validate_json(before).model_dump_json()
    assert any("rollback" in v for v in w.s.violations)


def test_rollback_restores_everything_on_invariant_failure(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(3):
        w.act("player", "look")
    w.act("fighter", "attack", target="guest", intent="injure")
    snap = w.s.model_copy(deep=True)
    real = invariants.check
    monkeypatch.setattr(invariants, "check", lambda *a, **k: ["boom"])
    a = w.build("fighter", "attack", target="guest", intent="kill")
    evs = resolve.apply(w.s, w.defs, a, source="mind")
    assert evs == []
    monkeypatch.setattr(invariants, "check", real)
    w.s.violations = snap.violations
    assert w.s == snap  # 状态逐字段还原：记忆、情绪、事件、随机数计数器、物品
    assert real(w.s, w.defs) == []


def test_rollback_keeps_object_identity(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    guest = w.c("guest")
    rel = guest.rel["player"]
    monkeypatch.setattr(invariants, "check", lambda *a, **k: ["boom"])
    w.say("player", "insult", ["guest"])
    assert w.c("guest") is guest and guest.rel["player"] is rel and rel.affinity == 20


def test_dead_do_not_act(w: World) -> None:
    w.fx([{"kill": "guest"}])
    assert w.legal("guest") == []
    a = w.build("player", "look")
    a2 = a.model_copy(update={"actor": "guest"})
    assert resolve.apply(w.s, w.defs, a2, source="mind") == []


def test_liveness(w: World) -> None:
    w.fx([{"step_state": ["girl", "gi_watch", "void"]}])
    assert invariants.liveness(w.s, w.defs) == ["girl"]
    w.c("girl").agenda = Agenda(goal="等人", status="waiting", waiting_for="等书生回来")
    assert invariants.liveness(w.s, w.defs) == []
    w.c("girl").agenda = None
    assert invariants.liveness(w.s, w.defs) == ["girl"]
    w.c("girl").agenda = Agenda(goal="g", steps=[Step(id="s", text="t", do={"verb": "wait"})])
    assert invariants.liveness(w.s, w.defs) == []


# ---------------------------------------------------------------- 6.10


def test_danger_levels(w: World) -> None:
    assert danger.danger(w.s, w.defs) == 0
    w.fx([{"rel": ["guest", "girl", "affinity", -50]}])
    assert danger.danger(w.s, w.defs) == 1  # 有敌意者在场，但不冲着玩家
    w.fx([{"rel": ["fighter", "player", "affinity", -50]}])
    assert danger.danger(w.s, w.defs) == 2  # 高出玩家 2 以上的人冲着玩家来
    w.c("fighter").agenda.steps[0].do = {"verb": "attack", "target": "player", "intent": "kill"}
    assert danger.danger(w.s, w.defs) == 3


def test_danger_rises_as_a_lethal_deadline_nears(w: World) -> None:
    """T3：身上到期致死的毒，期限不足一日 danger 至少 1，只剩两个时辰至少 2。"""
    assert danger.danger(w.s, w.defs) == 0 and danger.lethal_deadline(w.s, w.c("player")) is None
    w.fx([{"add_status": ["player", "poisoned_x", {"ticks": 3 * 96}]}])
    st = w.status("player", "poisoned_x")
    assert st is not None and st.until == w.s.tick + 3 * 96
    assert danger.lethal_deadline(w.s, w.c("player")) == 3 * 96
    assert danger.danger(w.s, w.defs) == 0  # 还有三日：不算险
    st.until = w.s.tick + 96
    assert danger.danger(w.s, w.defs) == 0  # 整一日
    st.until = w.s.tick + 95
    assert danger.danger(w.s, w.defs) == 1  # 不足一日
    st.until = w.s.tick + 17
    assert danger.danger(w.s, w.defs) == 1
    st.until = w.s.tick + 16
    assert danger.danger(w.s, w.defs) == 2  # 只剩两个时辰
    st.until = w.s.tick + 2
    assert danger.danger(w.s, w.defs) == 2  # 几刻之后就发作：原来这里是 0
    # 别的带期限的状态不算；别人身上的毒不算玩家的险
    st.kind = "alerted"
    assert danger.danger(w.s, w.defs) == 0
    w.fx([{"add_status": ["guest", "poisoned_x", {"ticks": 4}]}])
    assert danger.danger(w.s, w.defs) == 0
    # 与眼前的敌意取大：有人要取性命仍是 3；期限近而有人敌视取 2
    st.kind = "poisoned_x"
    st.until = w.s.tick + 4
    w.fx([{"rel": ["guest", "girl", "affinity", -50]}])
    assert danger.danger(w.s, w.defs) == 2
    w.fx([{"rel": ["fighter", "player", "affinity", -50]}])
    w.c("fighter").agenda.steps[0].do = {"verb": "attack", "target": "player", "intent": "kill"}
    assert danger.danger(w.s, w.defs) == 3


def test_danger_from_fighting_nearby(w: World) -> None:
    w.act("fighter", "attack", target="guest", intent="injure")
    assert danger.danger(w.s, w.defs) == 2


def test_hostility_definition(w: World) -> None:
    f = w.c("fighter")
    assert not danger.is_hostile(w.s, f, "player")
    f.agenda.steps[0].do = {"verb": "restrain", "target": "player"}
    assert danger.is_hostile(w.s, f, "player")
    f.agenda.steps[0].do = {"verb": "wait"}
    f.rel["player"] = f.rel["guest"].model_copy(update={"affinity": -31})
    assert danger.is_hostile(w.s, f, "player")


def test_player_lethal_attack_warn_then_resolve(w: World) -> None:
    a = w.build("player", "attack", target="fighter", intent="injure")
    assert a.lethal
    evs = w.act("player", "attack", target="fighter", intent="injure")
    assert [e.verb for e in evs] == ["warn_danger"]
    assert set(evs[0].witnesses) == {"player"} and w.status("player", "injured") is None
    w.act("player", "attack", target="fighter", intent="injure")
    assert any(e.verb == "attack" for e in w.s.events_tail)


def test_kill_downgrade_buffer_in_fight(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rng, "roll", lambda s: 100)
    w.act("fighter", "attack", target="player", intent="kill")
    assert w.status("player", "dead") is None and w.status("player", "injured").level == 3
    assert "kill:fighter" in w.s.warned
    w.act("fighter", "attack", target="player", intent="kill")
    assert w.status("player", "dead") is not None and w.s.ended == "dead"


def test_lethal_item_and_bottom_line_protects_player(w: World) -> None:
    assert danger.item_lethal(w.s, "poison") and not danger.item_lethal(w.s, "rations")
    # 人物底线照样约束他对玩家的做法
    from tianlong.kernel.defs import BlockDef, BottomLine

    w.defs = w.defs.model_copy(deep=True)
    w.defs.cards["fighter"].bottom_lines.append(
        BottomLine(
            text="不杀无力还手的人",
            blocks=[BlockDef(verb="attack", intent="kill", when={"stat": ["$target", "power", "<=", 0]})],
        )
    )
    assert not any(
        a.verb == "attack" and a.params.get("intent") == "kill" and a.params["target"] == "player"
        for a in w.legal("fighter")
    )
    assert any(
        a.verb == "attack" and a.params.get("intent") == "injure" and a.params["target"] == "player"
        for a in w.legal("fighter")
    )
    w.c("player").arts["hall_sword"] = 1
    assert any(
        a.verb == "attack" and a.params.get("intent") == "kill" and a.params["target"] == "player"
        for a in w.legal("fighter")
    )
