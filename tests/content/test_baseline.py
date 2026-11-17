"""基线跑（方案 11.3，M5 的门槛）：不放玩家，世界自己走 12 日。

- 走运（判定一律取成功、重新打算只用规则分）：每个参照点都在它的 by 之前成立。
- 真骰子：不要求全中（骰子本来就会让世界走岔），要求跑得完——没有不变量失败、没有人停摆、没有异常。

baseline.yaml 只在这里拿来对照；运行时没有任何规则读它来决定走向。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.content.loader import load
from tianlong.content.validate import validate
from tianlong.kernel import predicates
from tianlong.kernel.state import TICKS_PER_DAY, Ctx
from tianlong.llm import make_llm
from tianlong.world import RunReport, run_days

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO / "content"
DAYS = 12


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


async def run(settings: Settings, *, seed: int, lucky: bool, days: int = DAYS) -> RunReport:
    defs, state = load(CONTENT, seed=seed)
    llm = make_llm(settings, mock=True, run_dir=None)
    try:
        return await run_days(llm, state, defs, days, lucky=lucky)
    finally:
        await llm.aclose()


def describe(rep: RunReport) -> str:
    return rep.summary()


def test_content_validates_with_zero_problems() -> None:
    defs, state = load(CONTENT)
    assert validate(defs, state) == []


def test_no_mark_holds_before_anything_happens() -> None:
    """参照点说的是「发生了一件事」：开局那一刻一条都不该成立（C8）。"""
    defs, state = load(CONTENT)
    already = [m.id for m in defs.baseline if predicates.holds(state, defs, m.holds, Ctx())]
    assert already == []
    bys = [m.by for m in defs.baseline]
    assert all(b is not None and b > state.tick for b in bys)
    assert max(bys) <= state.tick + DAYS * TICKS_PER_DAY  # 12 日之内都该见分晓


async def test_lucky_baseline_hits_every_mark_on_time(settings: Settings) -> None:
    rep = await run(settings, seed=0, lucky=True)
    assert rep.errors == [], describe(rep)
    assert rep.invariant_failures == [], describe(rep)
    assert rep.stalls == [], describe(rep)
    assert rep.missed == [] and rep.late == [], describe(rep)
    assert len(rep.on_time) == len(rep.marks) == 19
    assert rep.end_tick == rep.start_tick + DAYS * TICKS_PER_DAY


async def test_lucky_baseline_does_not_depend_on_the_seed(settings: Settings) -> None:
    """走运模式没有骰子：换一个种子，参照点成立的时刻一模一样。"""
    a = await run(settings, seed=0, lucky=True, days=5)
    b = await run(settings, seed=7, lucky=True, days=5)
    assert {k: m.reached for k, m in a.marks.items()} == {k: m.reached for k, m in b.marks.items()}


@pytest.mark.parametrize("seed", [0, 1, 2])
async def test_real_dice_baseline_runs_clean(settings: Settings, seed: int) -> None:
    rep = await run(settings, seed=seed, lucky=False)
    assert rep.errors == [], describe(rep)
    assert rep.invariant_failures == [], describe(rep)
    assert rep.stalls == [], describe(rep)
    assert rep.end_tick == rep.start_tick + DAYS * TICKS_PER_DAY
    assert rep.events > 200  # 世界确实在走
    # 开头几件事不靠骰子：哪个种子都该按时
    for mid in ("m_slap", "m_beam_girl", "m_siege"):
        assert rep.marks[mid].on_time, describe(rep)


async def test_real_dice_runs_are_reproducible(settings: Settings) -> None:
    a = await run(settings, seed=3, lucky=False, days=4)
    b = await run(settings, seed=3, lucky=False, days=4)
    assert {k: m.reached for k, m in a.marks.items()} == {k: m.reached for k, m in b.marks.items()}
    assert (a.events, a.replans, a.by_source) == (b.events, b.replans, b.by_source)
