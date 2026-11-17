"""复查 D-347 到 D-366 后的回归修补（整回合一侧）。

- 从要取命的人身边走开：一次输入里不会既用掉缓冲又送命（6.10）。
- 闯出口没闯成：人还站在原地，不耗那条路的时间（6.1、2.5）。
- 侥幸不死之后的那一次逃，要留到玩家下一次能动手的时候（6.5）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings
from tianlong.kernel import actions, danger, rng
from tianlong.kernel.state import Status, has_status

from .conftest import MINI, make_settings, new_session, options_of, text_of

QUIET_YARD = ("一", "二", "我去前院看看")  # 把开场那场事演完，再走到没人的前院


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


def _hunter(s, loc: str = "yard") -> None:  # noqa: ANN001
    """一个本事高出许多、眼下这一步就是要取玩家性命的人。"""
    st = s.state
    kill = {"id": "k1", "text": "杀了他", "do": {"verb": "attack", "target": st.player, "intent": "kill"}, "takes": 1}
    s.fx(
        [
            {"move": ["fighter", loc]},
            {"stat": ["fighter", "base_power", 6]},
            {"agenda": ["fighter", {"goal": "寻仇", "steps": [kill]}]},
        ]
    )


async def _turn(s, verb: str, **params):  # noqa: ANN001, ANN003, ANN202
    """直接走一个回合（不经输入归类）。"""
    st = s.state
    act = actions.build(st, s.game.defs, st.player, verb, params)
    assert act is not None, (verb, params)
    return act, [ev async for ev in s.game._logged(verb, "turn", s.game._play(act, None))]


# ---------------------------------------------------------------- 1. 从要取命的人身边走开


@pytest.mark.parametrize("verb", ["go", "flee"])
async def test_walking_away_from_a_killer_never_dies_in_one_input(tmp_path: Path, verb: str) -> None:
    """同一来源的第一次致命判定降级，再犯才是死（6.10）：走不脱被追上的那一下之后，这一回合里不会再挨第二下。"""
    caught = 0
    for seed in range(12):
        s = await new_session(make_settings(tmp_path / f"s{seed}", MINI), seed=seed)
        await s.play(*QUIET_YARD)
        _hunter(s)
        st = s.state
        me = st.chars[st.player]
        _, evs = await _turn(s, verb, exit="hall")
        assert st.ended is None, (seed, text_of(evs))
        if me.loc == "yard":
            caught += 1
            assert actions.injury_level(me) == 3 and "kill:fighter" in st.warned
            assert "伤得极重" in text_of(evs) or "重伤" in text_of(evs)  # 叙述里把话挑明
        await s.close()
    assert caught >= 6  # 高出 4 以上的追者：多半走不脱（6.4），这条测试才测到了被追上的那条路


# ---------------------------------------------------------------- 2. 闯出口没闯成不耗路上的时间


async def test_failed_contested_go_costs_no_travel_time(real_settings: Settings, dice) -> None:  # type: ignore[no-untyped-def]
    """被拦下来的人还站在原地：三个时辰的山路一刻也没走，身上的期限不该白白烧掉。"""
    s = await new_session(real_settings)
    st, df = s.state, s.game.defs
    pid = st.player
    s.fx([{"move": [pid, "mountain_path"]}])
    here = [c.id for c in st.chars.values() if c.loc == "mountain_path" and c.id != pid and c.onstage]
    assert here
    me = st.chars[pid]
    me.statuses.append(Status(kind="impeded", until=st.tick + 2, source=here[0]))
    me.statuses.append(Status(kind="poisoned_duanchang", until=st.tick + 20))
    assert [o.id for o in danger.blockers(st, pid)] == [here[0]]
    act = actions.build(st, df, pid, "go", {"exit": "crag_foot"})
    assert act is not None and act.cost == 24 and "夺路" in act.desc
    t0 = st.tick
    dice(100, 100, 100, 100)  # 闯不过去
    evs = [ev async for ev in s.game._logged("go", "turn", s.game._play(act, None))]
    assert me.loc == "mountain_path"
    assert st.tick == t0, f"stood still for {st.tick - t0} ticks"
    assert st.ended is None
    assert "拦" in text_of(evs) and options_of(evs)
    await s.close()


async def test_contested_go_that_gets_through_still_costs_the_edge(real_settings: Settings, dice) -> None:  # type: ignore[no-untyped-def]
    s = await new_session(real_settings)
    st, df = s.state, s.game.defs
    pid = st.player
    s.fx([{"move": [pid, "mountain_path"]}])
    here = [c.id for c in st.chars.values() if c.loc == "mountain_path" and c.id != pid and c.onstage]
    st.chars[pid].statuses.append(Status(kind="impeded", until=st.tick + 2, source=here[0]))
    act = actions.build(st, df, pid, "go", {"exit": "crag_foot"})
    assert act is not None and act.cost == 24
    t0 = st.tick
    dice(1)  # 闯过去了
    [ev async for ev in s.game._logged("go", "turn", s.game._play(act, None))]
    assert st.chars[pid].loc == "crag_foot" and st.tick == t0 + 24
    await s.close()


# ---------------------------------------------------------------- 3. 侥幸不死：下一次能动手时还逃得了


@pytest.mark.parametrize(
    ("verb", "params", "cost"),
    [
        ("look", {}, 1),
        ("search", {"feature": "woodpile"}, 2),
        ("wait", {"ticks": 4}, 4),
    ],
)
async def test_reprieve_survives_the_turn_it_was_granted_in(
    settings: Settings, verb: str, params: dict, cost: int
) -> None:
    """杀招落在玩家这一回合的反应轮里；这一回合的动作不论耗几刻，回合结束时「可再逃一次」都还在。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    _hunter(s)
    st = s.state
    me = st.chars[st.player]
    st.flags["__lucky__"] = True  # 侥幸成了（6.5）
    act, evs = await _turn(s, verb, **params)
    st.flags["__lucky__"] = False
    assert act.cost == cost and st.ended is None
    assert actions.injury_level(me) == 3
    assert has_status(me, "reprieve"), f"reprieve gone after a {cost}-tick action"
    flee = [a for a in actions.legal_actions(st, s.game.defs, st.player) if a.verb == "flee"]
    assert flee, "no way left to run"
    assert any("逃" in o for o in options_of(evs) or []), options_of(evs)  # 自保的选项摆出来了
    await s.close()


async def test_reprieve_survives_walking_into_the_blow(settings: Settings) -> None:
    """走进要命的人所在的屋子（赶路不打断、耗时照算）：到了挨那一下，侥幸不死，随后还逃得了。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    _hunter(s, "hall")
    st = s.state
    me = st.chars[st.player]
    for e in st.locations["yard"].exits:
        if e.to == "hall":
            e.cost = 6  # 一段不短的路
    st.flags["__lucky__"] = True
    act, _ = await _turn(s, "go", exit="hall")
    st.flags["__lucky__"] = False
    assert act.cost == 6 and me.loc == "hall" and st.ended is None
    assert actions.injury_level(me) == 3 and has_status(me, "reprieve")
    assert any(a.verb == "flee" for a in actions.legal_actions(st, s.game.defs, st.player))
    await s.close()


async def test_reprieve_still_runs_out(settings: Settings) -> None:
    """留到下一次能动手为止，不是永远留着：之后照 D-352 的两刻过期。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    _hunter(s)
    st = s.state
    me = st.chars[st.player]
    st.flags["__lucky__"] = True
    await _turn(s, "search", feature="woodpile")
    st.flags["__lucky__"] = False
    assert has_status(me, "reprieve")
    s.fx([{"move": ["fighter", "hall"]}])  # 人走了，免得下面再挨一下
    await _turn(s, "wait", ticks=3)
    assert not has_status(me, "reprieve")
    assert not any(a.verb == "flee" for a in actions.legal_actions(st, s.game.defs, st.player))
    await s.close()
