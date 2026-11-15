"""8.6 分岔：每个时辰对照；by 加一日宽限；起因追溯到玩家的事件；参照点从不左右走向。"""

from __future__ import annotations

from tianlong.kernel import actions, resolve
from tianlong.world import baseline

from .conftest import fx, hours


def _player_act(state, defs, verb, **params):
    a = actions.build(state, defs, state.player, verb, params)
    assert a is not None, (verb, params)
    return resolve.apply(state, defs, a, source="player")


async def test_player_takes_the_scroll_first(llm, world):
    defs, state = world
    p = state.player
    # 玩家先跟段誉打个招呼（只是沾了边的事件），再下到谷底取走帛卷
    fx(state, defs, [{"learn": [p, "exit:valley_floor:valley_cave", 1.0, "saw"]}, {"move": [p, "valley_cave"]}])
    _player_act(state, defs, "search", feature="cushion")
    evs = _player_act(state, defs, "take", item="silk_scroll")
    take = next(e for e in evs if e.verb == "take")
    assert state.items["silk_scroll"].holder == p
    by = next(m.by for m in defs.baseline if m.id == "m_scroll")
    while state.tick <= by + baseline.GRACE:
        await hours(llm, state, defs, 1)
        assert state.items["silk_scroll"].holder != "duan_yu"
    d = next(d for d in state.divergences if d.mark == "m_scroll")
    assert d.cause_event == take.id
    assert d.known_to_player is True
    assert d.tick > by + baseline.GRACE - 8
    rows = baseline.divergences_report(state, defs)
    row = next(r for r in rows if r["mark"] == "m_scroll")
    assert row["cause_event"] == take.id and row["known_to_player"]
    # 段誉之后仍有打算在推进（世界不停摆）
    dy = state.chars["duan_yu"]
    assert dy.agenda is not None and (dy.agenda.status == "waiting" or any(s.state == "pending" for s in dy.agenda.steps))


def test_no_divergence_before_the_grace_day(world):
    defs, state = world
    m = next(m for m in defs.baseline if m.id == "m_slap")
    fx(state, defs, [])
    from tianlong.kernel import timeflow

    timeflow.advance_time(state, defs, m.by + baseline.GRACE - state.tick)
    assert baseline.check(state, defs) == [] or all(d.mark != "m_slap" for d in state.divergences)
    timeflow.advance_time(state, defs, 1)
    new = baseline.check(state, defs)
    assert "m_slap" in [d.mark for d in new]
    # 同一参照点只记一次
    timeflow.advance_time(state, defs, 8)
    assert "m_slap" not in [d.mark for d in baseline.check(state, defs)]
    assert [d.mark for d in state.divergences].count("m_slap") == 1


def test_reached_once_counts(world):
    """一时的状态（被擒）成立过一次就算达成，之后解除也不记分岔（D-075）。"""
    defs, state = world
    from tianlong.kernel import timeflow

    fx(state, defs, [{"add_status": ["duan_yu", "captive", {"source": "sikong_xuan"}]}])
    baseline.check(state, defs)
    assert "m_captured_camp" in baseline.reached(state)
    fx(state, defs, [{"remove_status": ["duan_yu", "captive"]}])
    timeflow.advance_time(state, defs, 400)
    baseline.check(state, defs)
    assert "m_captured_camp" not in [d.mark for d in state.divergences]


def test_baseline_is_never_read_to_decide(world):
    """参照点删光，世界照样走：world 的推进不依赖 baseline。"""
    defs, state = world
    from tianlong.world import goals

    stripped = defs.model_copy(update={"baseline": []})
    a = [c.rule for c in goals.candidates(state, defs, "duan_yu", allow_keep=True)]
    b = [c.rule for c in goals.candidates(state, stripped, "duan_yu", allow_keep=True)]
    assert a == b
    assert baseline.check(state, stripped) == []
