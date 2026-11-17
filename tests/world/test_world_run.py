"""11.3 基线跑：没有玩家，世界自己走。三日内不抛异常、不违反不变量、没有人停摆超过一日。"""

from __future__ import annotations

import pytest

from tianlong.world import run_days

from .conftest import fresh


async def test_lucky_three_days(llm, capsys):
    defs, state = fresh(0)
    report = await run_days(llm, state, defs, 3, lucky=True)
    print(report.summary())
    assert report.errors == []
    assert report.invariant_failures == []
    assert report.stalls == []
    assert report.events > 100
    assert not state.chars[state.player].onstage
    assert state.flags.get("__lucky__") is True
    # 开局那一串接得上的几个参照点按时成立（全部按时还要等内容调好，见交付说明）
    for mark in ("m_slap", "m_beam_girl", "m_siege", "m_captured_camp"):
        assert report.marks[mark].on_time, mark
    assert len(report.marks) == len(defs.baseline)


@pytest.mark.parametrize("seed", [1, 7])
async def test_real_dice_three_days_liveness(llm, seed):
    defs, state = fresh(seed)
    report = await run_days(llm, state, defs, 3, lucky=False)
    assert report.errors == []
    assert report.invariant_failures == []
    assert report.stalls == []  # 没有哪个有魂、有名角色无事可做超过一日
    assert report.by_source.get("agenda", 0) > 0 and report.by_source.get("situation", 0) > 0


async def test_lucky_run_is_replayable(settings, tmp_path):
    from tianlong.llm import make_llm

    reports = []
    for i in range(2):
        defs, state = fresh(3)
        llm = make_llm(settings, mock=True, run_dir=tmp_path / f"r{i}")
        rep = await run_days(llm, state, defs, 1, lucky=False)
        reports.append((rep.events, state.rng_counter, [e.fact_text for e in state.events_tail]))
    assert reports[0] == reports[1]
