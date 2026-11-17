"""真实 content/ 上的回归：前提写漏的两处（钟灵去营地、私逃被撞见）。

- 钟灵去神农帮营地是「陪着段誉」：段誉死了、或不去了，她不该独自走进营地被扣、放貂咬人。
- 私逃被撞见的结算是「逼到断崖、失足坠落」：只在后山小径与断崖开场；渡口、大道上的人不会被挪到崖边。
- 「撞见的人」不取躲着的、被拿住的、被缚的人。

Mock 模型，不联网；真骰子。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.kernel import resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, TICKS_PER_HOUR, Ctx, Status, WorldState, has_status, is_alive
from tianlong.llm import LLM, make_llm
from tianlong.world import clock, run_days, situations

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO / "content"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


@pytest.fixture
async def llm(settings: Settings) -> Any:
    m = make_llm(settings, mock=True, run_dir=None)
    yield m
    await m.aclose()


def fx(st: WorldState, defs: Defs, effs: list[dict]) -> None:
    resolve.run_effects(st, defs, effs, Ctx(), source="rule")


async def hour(llm: LLM, st: WorldState, defs: Defs) -> None:
    timeflow.advance_time(st, defs, TICKS_PER_HOUR)
    await clock.tick(llm, st, defs)
    situations.end_of_turn(st, defs)


# ---------------------------------------------------------------- 钟灵去营地，是陪着段誉去

STOP_DUAN = {
    "duan_yu_killed": [{"kill": ["duan_yu", "debug"]}],
    "duan_yu_not_going": [{"step_state": ["duan_yu", "dy_plead_shennong", "void"]}],
}


async def _run_camp(llm: LLM, perturb: list[dict] | None) -> tuple[WorldState, list[tuple[int, str]]]:
    """玩家不在图上，世界自己走；「梁上少女」了结的那一刻插手（或不插手），再走到第二日过半。"""
    defs, st = load(CONTENT, seed=1)
    fx(st, defs, [{"leave": st.player}])
    seen: list[tuple[int, str]] = []
    done = False
    while st.tick < TICKS_PER_DAY + TICKS_PER_DAY // 2:
        await hour(llm, st, defs)
        if not done and st.flags.get("beam_girl_done"):
            done = True
            if perturb:
                fx(st, defs, perturb)
        seen.append((st.tick, st.chars["zhong_ling"].loc))
    assert done, "beam_girl never finished: the script's premise is gone"
    return st, seen


@pytest.mark.parametrize("case", sorted(STOP_DUAN))
async def test_zhong_ling_does_not_walk_into_the_camp_without_duan_yu(llm: LLM, case: str) -> None:
    st, seen = await _run_camp(llm, STOP_DUAN[case])
    zl, boss = st.chars["zhong_ling"], st.chars["sikong_xuan"]
    assert not [t for t, loc in seen if loc == "shennong_camp"], seen
    assert not has_status(zl, "captive")
    assert not has_status(boss, "poisoned_diao")
    assert not st.flags.get("diao_bite_done")


async def test_zhong_ling_still_goes_to_the_camp_with_duan_yu(llm: LLM) -> None:
    """不插手：照旧跟着段誉进营地，被扣，放貂。"""
    st, seen = await _run_camp(llm, None)
    assert [t for t, loc in seen if loc == "shennong_camp"], seen
    assert st.flags.get("diao_bite_done")
    assert has_status(st.chars["sikong_xuan"], "poisoned_diao")


# ---------------------------------------------------------------- 私逃被撞见：只在后山小径与断崖

LOVERS = ("gan_guanghao", "ge_guangpei")


def _eloping(seed: int, where: str) -> tuple[Defs, WorldState]:
    """两人比完剑、溜出了门，正走在 ``where``。"""
    defs, st = load(CONTENT, seed=seed)
    for cid in LOVERS:
        ag = st.chars[cid].agenda
        assert ag is not None
        for s in ag.steps:
            if s.id.endswith(("_bout", "_slip_out")):
                s.state = "done"
        st.chars[cid].loc = where
    return defs, st


def _caught(st: WorldState) -> list[Any]:
    return [s for s in st.situations.values() if s.kind == "caught_eloping"]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_bystander_at_the_ferry_is_not_dragged_to_the_cliff(seed: int) -> None:
    defs, st = _eloping(seed, "ferry_bridge")
    fx(st, defs, [{"leave": st.player}])
    m = st.chars["merchant_1"]
    assert m.loc == "ferry_bridge"
    situations.check(st, defs)
    assert _caught(st) == []
    assert is_alive(m) and m.loc == "ferry_bridge"
    assert not st.flags.get("lovers_witnessed")


@pytest.mark.parametrize("where", ["back_trail", "cliff_edge"])
def test_caught_eloping_still_opens_by_the_cliff(where: str) -> None:
    defs, st = _eloping(1, where)
    st.chars[st.player].loc = where
    situations.check(st, defs)
    (sit,) = _caught(st)
    assert sit.roles["witness"] == st.player


async def test_nobody_dies_at_the_cliff_when_the_lovers_reach_the_ferry_unseen(settings: Settings) -> None:
    """段誉不在图上：没有人在后山小径撞见那两人，他们一路走到渡口。渡口的客商不该死在断崖。"""
    defs, st = load(CONTENT, seed=1)
    fx(st, defs, [{"leave": "duan_yu"}])
    m = make_llm(settings, mock=True, run_dir=None)
    try:
        rep = await run_days(m, st, defs, 4)
    finally:
        await m.aclose()
    assert rep.errors == []
    # 脚本的前提：那两人没在后山小径被别人撞见，已经走完全程出了图
    assert not st.flags.get("lovers_witnessed"), "someone met the pair on the back trail: the script's premise is gone"
    assert not any(st.chars[cid].onstage for cid in LOVERS)
    merchant = st.chars["merchant_1"]
    assert is_alive(merchant), merchant.statuses
    assert merchant.loc not in ("cliff_edge", "valley_floor")


# ---------------------------------------------------------------- 撞见的人：躲着的、被拿住的、被缚的不算


@pytest.mark.parametrize("kind", ["hidden", "captive", "bound"])
def test_hidden_or_held_bystander_is_not_the_witness(kind: str) -> None:
    defs, st = _eloping(1, "back_trail")
    p = st.chars[st.player]
    p.loc = "back_trail"
    p.statuses.append(Status(kind=kind))
    situations.check(st, defs)
    assert _caught(st) == []
    assert not st.flags.get("lovers_witnessed")


@pytest.mark.parametrize("kind", ["hidden", "captive", "bound"])
def test_hidden_player_does_not_take_the_scene_from_someone_in_plain_sight(kind: str) -> None:
    defs, st = _eloping(1, "back_trail")
    p = st.chars[st.player]
    p.loc = "back_trail"
    p.statuses.append(Status(kind=kind))
    st.chars["duan_yu"].loc = "back_trail"
    situations.check(st, defs)
    (sit,) = _caught(st)
    assert sit.roles["witness"] == "duan_yu"
