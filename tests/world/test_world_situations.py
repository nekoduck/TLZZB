"""9.5 情境：在场开场、离场结算、绑不上不吭声、同种同角色不重开、每回合计数。"""

from __future__ import annotations

from tianlong.kernel import timeflow
from tianlong.world import situations

from .conftest import fx, player_off


def test_opens_in_scene_and_never_twice(world):
    defs, state = world
    situations.check(state, defs)
    situations.check(state, defs)
    bouts = [s for s in state.situations.values() if s.kind == "bout_laugh"]
    assert len(bouts) == 1
    assert bouts[0].roles == {"victor": "gong_guangjie", "guest": "duan_yu"}
    assert not state.flags.get("slapped")


def test_settles_offscreen_without_the_player(world):
    defs, state = world
    player_off(state, defs)
    events = situations.check(state, defs)
    assert state.flags.get("slapped") is True
    assert not any(s.kind == "bout_laugh" for s in state.situations.values())
    assert all(e.offscreen for e in events if e.source == "situation")


def test_failed_bindings_are_silent(world):
    defs, state = world
    player_off(state, defs)
    before = list(state.violations)
    situations.check(state, defs)
    new = state.violations[len(before):]
    assert not any("bindable" in v or "open " in v for v in new), new


def test_running_situation_settles_when_the_player_leaves(world):
    defs, state = world
    situations.check(state, defs)
    assert any(s.kind == "bout_laugh" for s in state.situations.values())
    fx(state, defs, [{"move": [state.player, "jianhu_gate"]}])
    situations.check(state, defs)
    assert state.flags.get("slapped") is True
    assert not any(s.kind == "bout_laugh" for s in state.situations.values())


def test_end_of_turn_counts_and_closes_at_max_turns(world):
    defs, state = world
    situations.check(state, defs)
    sid = next(s.id for s in state.situations.values() if s.kind == "bout_laugh")
    for _ in range(5):
        situations.end_of_turn(state, defs)
    assert state.situations[sid].turns == 5
    closed = situations.end_of_turn(state, defs)
    assert sid in closed and sid not in state.situations


def test_generic_situation_binds_by_pick(world):
    defs, state = world
    player_off(state, defs)
    timeflow.advance_time(state, defs, 8)
    # 马五德在后院受了伤，同在后院、对他有好感的段誉照料他（tending 的两个位都是 pick）
    fx(state, defs, [{"move": ["ma_wude", "jianhu_back"]}, {"move": ["duan_yu", "jianhu_back"]}])
    fx(state, defs, [{"add_status": ["ma_wude", "injured", {"level": 1}]}, {"rel": ["duan_yu", "ma_wude", "affinity", 20]}])
    situations.check(state, defs)
    from tianlong.kernel.state import has_status

    assert has_status(state.chars["ma_wude"], "tended")
