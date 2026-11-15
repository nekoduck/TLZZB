"""断言语言（evals/README.md 5.2）的单元测试：在真实内容的场景上手工改出「之后」的状态，逐种断言验一遍。"""

from __future__ import annotations

import pytest

from tianlong.evalrun import assertions
from tianlong.evalrun.classify import resolve_claims
from tianlong.evalrun.common import EvalConfig, apply_setup
from tianlong.kernel.state import Claim, Event, Request, Speech


def ev(i: int, actor: str, verb: str, *, outcome: str = "ok", speech: Speech | None = None) -> Event:
    return Event(id=i, tick=32, loc="x", actor=actor, verb=verb, params={}, outcome=outcome, fact_text="", speech=speech,
                 witnesses={}, source="player")


@pytest.fixture
def world(cfg: EvalConfig):  # noqa: ANN201
    defs, before, _ = cfg.build(cfg.scene_setup("hall"))
    return defs, before, before.model_copy(deep=True)


def check(world, assertion: dict, events: list[Event] | None = None) -> bool:  # noqa: ANN001
    defs, before, after = world
    return assertions.evaluate(defs, before, after, events or [], assertion)[0]


def test_state_assertions(world) -> None:  # noqa: ANN001
    defs, before, after = world
    someone = next(c for c in before.chars.values() if c.id != before.player and c.loc == before.chars[before.player].loc)
    assert check(world, {"holds": {"alive": someone.id}})
    assert not check(world, {"holds": {"not": {"alive": someone.id}}})
    assert check(world, {"unchanged": "chars.player.inventory"}) and check(world, {"unchanged": "flags.no_such_flag"})
    assert check(world, {"tick_delta": ["==", 0]}) and check(world, {"len_delta": ["requests", "==", 0]})
    apply_setup(after, defs, [{"set_flag": ["eval_test_flag", True]}])
    after.tick += 3
    assert not check(world, {"unchanged": "flags.eval_test_flag"}) and check(world, {"unchanged": "chars.player.arts"})
    assert check(world, {"tick_delta": [">", 0]}) and not check(world, {"tick_delta": ["==", 0]})
    # 情绪、关系、信念：没有的记 0
    after.chars[someone.id].mood["suspicion"] = before.chars[someone.id].mood.get("suspicion", 0) + 15
    assert check(world, {"mood_delta": [someone.id, "suspicion", ">=", 15]})
    assert not check(world, {"mood_delta": [someone.id, "anger", ">", 0]})
    assert check(world, {"rel_delta": [someone.id, "player", "trust", "==", 0]})
    fact = next(f for f in before.facts if f not in before.chars[someone.id].beliefs)
    assert check(world, {"belief": [someone.id, fact, "<", 0.6]}) and not check(world, {"belief": [someone.id, fact, ">", 0]})
    with pytest.raises(ValueError):
        assertions.evaluate(defs, before, after, [], {"no_such_assertion": 1})
    with pytest.raises(ValueError):
        assertions.evaluate(defs, before, after, [], {"unchanged": "chars.player.no_such_field"})
    failed = assertions.evaluate_all(defs, before, after, [], [{"tick_delta": ["==", 0]}, {"no_such": 1},
                                                              {"tick_delta": ["==", 3]}])
    assert [next(iter(f["assertion"])) for f in failed] == ["tick_delta", "no_such"]


def test_request_assertions(world) -> None:  # noqa: ANN001
    defs, before, after = world
    to = next(c.id for c in before.chars.values() if c.id != before.player and c.loc == before.chars[before.player].loc)
    assert check(world, {"request": {"to": to, "absent": True}})
    assert not check(world, {"request": {"to": to, "verdict_in": ["no"]}})  # 找不到请求算不通过
    after.requests["R1"] = Request(id="R1", frm="player", to=to, action={"verb": "wait", "params": {}},
                                   action_class="costly", verdict="no", status="refused", attempts=3)
    after.requests["R2"] = Request(id="R2", frm=to, to="player", action={"verb": "wait", "params": {}},
                                   action_class="trivial", verdict="may")  # 别人对玩家的请求不算
    req = assertions.find_request(before, after, to)
    assert req is not None and req.id == "R1"
    assert check(world, {"request": {"to": to, "verdict_in": ["no", "never"], "status_in": ["refused"], "attempts_ge": 3,
                                     "evidence_max": 0, "verdict_not_in": ["may"], "status_not_in": ["agreed"]}})
    assert not check(world, {"request": {"to": to, "verdict_in": ["never"]}})
    assert not check(world, {"request": {"to": to, "attempts_ge": 4}})
    assert not check(world, {"request": {"to": to, "absent": True}})
    # 取编号最大的那条；回合之前就有、又没改动的不算
    after.requests["R10"] = Request(id="R10", frm="player", to=to, action={"verb": "wait", "params": {}},
                                    action_class="effort", verdict="may", evidence=["token"])
    assert assertions.find_request(before, after, to).id == "R10"
    assert not check(world, {"request": {"to": to, "evidence_max": 0}})
    before.requests["R10"] = after.requests["R10"].model_copy()
    assert assertions.find_request(before, after, to).id == "R1"


def test_claim_and_event_assertions(world) -> None:  # noqa: ANN001
    say = Speech(act="tell", to=[], claims=[Claim(text="a", ref="F1", verdict="unsourced"),
                                             Claim(text="b", ref=None, verdict="opinion")])
    events = [ev(1, "player", "say", speech=say), ev(2, "someone", "attack", outcome="fail"),
              ev(3, "someone", "say", speech=Speech(act="refuse", to=["player"], claims=[Claim(text="c", ref="F1",
                                                                                               verdict="true")]))]
    assert check(world, {"claim": {"ref": "F1", "any_in": ["unsourced"]}}, events)
    assert not check(world, {"claim": {"ref": "F1", "any_in": ["true"]}}, events)  # 别人的话不算玩家的断言
    assert check(world, {"claim": {"none_in": ["true", "false_self"]}}, events)
    assert not check(world, {"claim": {"none_in": ["opinion"]}}, events)
    assert not check(world, {"claim": {"ref": "F9", "any_in": ["unsourced"]}}, events)
    assert check(world, {"event": {"actor": "someone", "verb": "attack", "outcome": "fail"}}, events)
    assert check(world, {"event": {"actor": "player", "verb_in": ["attack", "steal"], "absent": True}}, events)
    assert not check(world, {"event": {"verb_in": ["attack"], "absent": True}}, events)
    assert not check(world, {"event": {"actor": "player", "verb": "attack"}}, events)


def test_new_events_and_self_claims(cfg: EvalConfig) -> None:
    defs, st, evs = cfg.build(cfg.scene_setup("hall_gift"))
    did = [e for e in evs if e.actor == st.player]
    assert did, "hall_gift 的 setup 里玩家做过一件事"
    ref = resolve_claims([f"self:{did[-1].verb}", "self_unlisted", "F_x", "self:no_such_verb"], evs, st.player)
    assert ref == [f"event:{did[-1].id}", "self_unlisted", "F_x", "self:no_such_verb"]
    before = st.model_copy(deep=True)
    apply_setup(st, defs, [{"set_flag": ["eval_test_flag", True]}])
    assert all(e.id >= before.next_event_id for e in assertions.new_events(before, st))
    assert len(assertions.new_events(before, before)) == 0
    assert assertions.compare(1, "!=", 2) and not assertions.compare(1, ">=", 2)
    assert assertions.resolve_path(st, "chars.player.loc") == st.chars[st.player].loc
