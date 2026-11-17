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


def _persuade(state, defs):
    """玩家先同客人打个招呼（只沾了边），再低声劝他赔个不是（想改变他做什么）。返回那句劝的事件。"""
    from tianlong.kernel.state import Speech

    p = state.player
    fx(state, defs, [{"open": ["bout_laugh"]}, {"set_flag": ["bout_over", True]}, {"set_flag": ["guest_laughed", True]}])
    sid = next(s.id for s in state.situations.values() if s.kind == "bout_laugh")
    wait = actions.build(state, defs, p, "wait", {"ticks": 1})
    greet = resolve.apply(state, defs, wait, Speech(act="greet", to=["duan_yu"]), source="player")
    assert greet and greet[0].outcome == "ok"
    ask = {"verb": "setpiece", "params": {"situation": sid, "name": "apologize"}, "desc": "拱手赔个不是"}
    sp = Speech(act="request", to=["duan_yu"], whisper=True, gist="劝对方拱手赔个不是", ask_action=ask)
    evs = resolve.apply(state, defs, wait, sp, source="player")
    said = next(e for e in evs if e.speech is not None and e.speech.act == "request")
    sorry = actions.build(state, defs, "duan_yu", "setpiece", {"situation": sid, "name": "apologize"})
    assert sorry is not None
    resolve.apply(state, defs, sorry, source="mind")
    assert state.flags.get("apologized") and not state.flags.get("slapped")
    return said


async def test_one_record_per_mark_traced_to_what_the_player_said(llm, world):
    """玩家劝了一句，耳光没有落下：world.tick 每个时辰对照，过了宽限认定一条分岔，起因追到那句劝。

    这是全项目唯一认定分岔的地方（经 diverge 效果）；讲述者的年谱只读 state.divergences。
    """
    from tianlong.storyteller import chronicle

    defs, state = world
    said = _persuade(state, defs)
    by = next(m.by for m in defs.baseline if m.id == "m_slap")
    assert baseline.mark_entities(state, next(m for m in defs.baseline if m.id == "m_slap")) >= {
        "gong_guangjie", "duan_yu"
    }  # 只写旗标的参照点，靠那句话里叫得出名字的人追起因
    seen: list[int] = []
    while state.tick <= by + baseline.GRACE + 8:
        await hours(llm, state, defs, 1)
        chronicle.record(state, defs, [])  # 讲述者每回合照记年谱：不会多出分岔
        seen.append(sum(1 for d in state.divergences if d.mark == "m_slap"))
    assert not state.flags.get("slapped")
    assert seen[0] == 0 and seen[-1] == 1 and sorted(seen) == seen
    (d,) = [d for d in state.divergences if d.mark == "m_slap"]
    assert d.cause_event == said.id  # 那句劝（想改变别人做什么）先于只是沾了边的招呼
    assert d.known_to_player is True and by + baseline.GRACE < d.tick <= by + baseline.GRACE + 8
    marks = [x.mark for x in state.divergences]
    assert len(marks) == len(set(marks))  # 每个参照点至多一条
    # 再对照、再记年谱，都不再添
    n = len(state.divergences)
    assert baseline.check(state, defs) == []
    chronicle.record(state, defs, [])
    assert len(state.divergences) == n
    entries = [e for e in state.chronicle if "⟦gong_guangjie⟧当众掴了⟦duan_yu⟧一记耳光" in e.text]
    assert len(entries) == 1 and entries[0].event == said.id and entries[0].known_to_player
    # 两边的清单说的是同一条记录
    row = next(r for r in baseline.divergences_report(state, defs) if r["mark"] == "m_slap")
    assert row["cause_event"] == said.id and row["known_to_player"] and "劝" in row["cause_text"]
    lines = chronicle.divergence_lines(state, defs, final=True)
    assert sum("一记耳光" in ln for ln in lines) == 1
    assert any("一记耳光" in ln and "起因" in ln and "劝" in ln for ln in lines)
    assert not hasattr(chronicle, "check_divergences")


def test_divergence_is_written_through_the_diverge_effect(world):
    """认定的结果只经内核的 diverge 效果写入：同一参照点写两次也只留一条；记账的旗标只有本模块读。"""
    defs, state = world
    from tianlong.kernel import timeflow
    from tianlong.world.common import BASELINE_FLAG

    m = next(m for m in defs.baseline if m.id == "m_slap")
    timeflow.advance_time(state, defs, m.by + baseline.GRACE + 1 - state.tick)
    new = baseline.check(state, defs)
    assert [d.mark for d in new] == ["m_slap"]
    assert new[0].cause_event is None and new[0].known_to_player is False  # 玩家什么也没做：追不到起因
    assert state.divergences[0] == new[0]
    fx(state, defs, [{"diverge": {"mark": "m_slap", "tick": state.tick + 5, "known_to_player": True}}])
    assert [d.mark for d in state.divergences] == ["m_slap"] and state.divergences[0] == new[0]
    assert set(state.flags[BASELINE_FLAG]) == {"reached", "cause", "scan"}
