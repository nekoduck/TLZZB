"""I6：在场的人原地「等」的那一步，跟着玩家动作过去的时间走（world.onscene_elapsed）。"""

from __future__ import annotations

from typing import Any

from tianlong import world
from tianlong.config import Settings
from tianlong.content.loader import load
from tianlong.kernel import danger, invariants, resolve
from tianlong.kernel.state import Ctx

from .conftest import MINI, assert_full_turn, new_session


def _world() -> tuple[Any, Any]:
    defs, state = load(MINI, player_name="阿青", seed=1)
    return state, defs


def _fx(state: Any, defs: Any, effs: list[dict]) -> None:
    resolve.run_effects(state, defs, effs, Ctx(self_id=state.player), source="rule")


def _agenda(steps: list[dict]) -> dict:
    return {"goal": "等人", "steps": steps}


def _wait(sid: str, takes: int, **kw: Any) -> dict:
    return {"id": sid, "text": "在这里等着", "at": "hall", "do": {"verb": "wait"}, "takes": takes, **kw}


def test_wait_step_progresses_and_completes() -> None:
    st, df = _world()
    assert st.chars[st.player].loc == "hall" and st.chars["guest"].loc == "hall"
    _fx(st, df, [{"agenda": ["guest", _agenda([_wait("w1", 12), _wait("w2", 8)])]}])
    guest = st.chars["guest"]
    assert world.onscene_elapsed(st, df, 8) == []  # 只记账，不出事件
    assert guest.agenda.progress == 8 and guest.agenda.steps[0].state == "pending"
    # 再过 8 刻：第一步等够了（12），余下的 4 刻记到下一步头上
    world.onscene_elapsed(st, df, 8)
    assert guest.agenda.steps[0].state == "done"
    step = danger.current_step(guest)
    assert step is not None and step.id == "w2" and guest.agenda.progress == 4
    world.onscene_elapsed(st, df, 40)
    assert [s.state for s in guest.agenda.steps] == ["done", "done"]
    assert danger.current_step(guest) is None
    assert invariants.check(st, df) == []
    # 没有时间过去、或这一局已经收场：什么也不做
    assert world.onscene_elapsed(st, df, 0) == []


def test_credited_ticks_are_not_counted_twice() -> None:
    st, df = _world()
    _fx(st, df, [{"agenda": ["guest", _agenda([_wait("w1", 12)])]}])
    guest = st.chars["guest"]
    _fx(st, df, [{"progress": ["guest", 3]}])  # 微轮里他自己已经等过 3 刻
    world.onscene_elapsed(st, df, 8, credited={"guest": 3})
    assert guest.agenda.progress == 8
    world.onscene_elapsed(st, df, 2, credited={"guest": 5})  # 记过的比过去的还多：不倒扣
    assert guest.agenda.progress == 8


def test_only_ready_wait_steps_of_people_on_scene() -> None:
    st, df = _world()
    cases = {
        "elsewhere": _wait("w1", 12, at="yard"),  # 要去别处等：还没到
        "later": _wait("w1", 12, start=st.tick + 50),  # 还没到时候
        "blocked": _wait("w1", 12, pre={"flag": "no_such_flag"}),  # 前提不成立
        "other_verb": {"id": "w1", "text": "找人说话", "at": "hall", "do": {"verb": "say"}, "takes": 12},
    }
    for name, step in cases.items():
        _fx(st, df, [{"agenda": ["guest", _agenda([step])]}])
        world.onscene_elapsed(st, df, 8)
        ag = st.chars["guest"].agenda
        assert ag.progress == 0 and ag.steps[0].state == "pending", name
    # 不在场的人不归这里管（world.tick 的离场结算管）
    _fx(st, df, [{"agenda": ["guest", _agenda([_wait("w1", 12, at="yard")])]}, {"move": ["guest", "yard"]}])
    world.onscene_elapsed(st, df, 8)
    assert st.chars["guest"].agenda.progress == 0
    # 昏过去的人不算在等
    _fx(st, df, [{"move": ["guest", "hall"]}, {"agenda": ["guest", _agenda([_wait("w1", 12)])]}])
    _fx(st, df, [{"add_status": ["guest", "unconscious", {"ticks": 20}]}])
    world.onscene_elapsed(st, df, 8)
    assert st.chars["guest"].agenda.progress == 0


async def test_game_credits_waiting_people_while_time_passes(settings: Settings) -> None:
    """玩家等上两个时辰，在场的人「等 12 刻」的一步也就等完了（不再是每个微轮只记 1 刻）。"""
    s = await new_session(settings)
    await s.play("一", "二")  # 把开场那场事演完
    st = s.state
    s.fx([{"move": ["guest", "hall"]}, {"agenda": ["guest", _agenda([_wait("w1", 12), _wait("w2", 400)])]}])
    t0 = st.tick
    for _ in range(4):  # 中途有人弄出动静会打断等待：接着等，直到过去 12 刻以上
        evs = await s.send("我在这里等上两个时辰")
        assert_full_turn(evs)
        if st.tick - t0 >= 12:
            break
    passed = st.tick - t0
    assert passed >= 12
    guest = st.chars["guest"]
    assert guest.agenda.steps[0].state == "done"
    step = danger.current_step(guest)
    assert step is not None and step.id == "w2"
    assert guest.agenda.progress <= passed - 12  # 一刻也没有多记
    assert invariants.check(st, s.game.defs) == []
    await s.close()
