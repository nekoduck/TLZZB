"""M2 的闸（方案第 12 节）：在真实的 content/ 里用 Mock 完整地玩 30 回合。

每回合都有抬头、叙述、三个选项；不变量成立；玩家看到的字里没有他还不知道的专名；首字时延达标。
"""

from __future__ import annotations

import statistics

import pytest

from tianlong import narrator
from tianlong.config import Settings
from tianlong.kernel import invariants
from tianlong.ui.events import Header, Options, Text

from .conftest import SCRIPT, assert_full_turn, header_of, new_session, options_of, text_of

FIRST_TOKEN_BUDGET_S = 3.0  # 方案 4.3：首字时延的中位数


@pytest.mark.parametrize("seed", [1, 2])
async def test_thirty_turns_in_real_content(real_settings: Settings, seed: int) -> None:
    s = await new_session(real_settings, seed=seed)
    g, st = s.game, s.state
    assert_full_turn(s.steps[0][1])
    played = 0
    replies = 0
    picks = 0
    free = 0
    for raw in SCRIPT:
        if played >= 30:
            break
        tick0, turn0 = st.tick, st.turn
        evs = await s.send(raw)
        rec = g.last_turn
        assert rec is not None and st.ended is None, (raw, st.ended)
        if rec.kind == "command":
            assert st.tick == tick0 and st.turn == turn0  # 命令不耗时
            continue
        # 每一屏：抬头（时辰与玩家所知的地名）、叙述、三个选项
        assert_full_turn(evs)
        h = header_of(evs)
        assert isinstance(h, Header) and h.clock and h.place
        opts = options_of(evs)
        assert opts is not None and len(opts) == 3
        assert sum(isinstance(e, Options) for e in evs) == 1
        assert any(isinstance(e, Text) for e in evs)
        # 视角检查：叙述与选项里没有玩家还不知道的专名，没有禁用词、数字、标记（方案 4.4、7.3）
        ck = narrator.build_checker(st, g.defs, g.last_view, quoted=[raw])
        bad = ck.check(text_of(evs)) + [b for o in opts for b in ck.check(o)]
        assert not bad, (raw, bad, text_of(evs), opts)
        loc = st.locations[st.chars[st.player].loc]
        assert h.place in (loc.name, loc.unknown_alias)
        if loc.id not in st.known_terms:
            assert h.place != loc.name or loc.name == loc.unknown_alias
        # 不变量（方案 11.1）
        assert invariants.check(st, g.defs) == [], raw
        if rec.kind == "reply":
            assert st.tick == tick0 and st.turn == turn0  # 做不到、看不懂、世上没有的东西：不耗时
            replies += 1
            continue
        assert rec.kind == "turn" and st.turn == turn0 + 1 and st.tick >= tick0
        played += 1
        if raw in ("一", "二", "三"):
            picks += 1
        else:
            free += 1
    assert played == 30 and picks >= 10 and free >= 8 and replies >= 2
    # 时延（方案 4.3）：Mock 下首字的中位数远低于预算
    assert len(g.latencies) >= 30
    assert statistics.median(g.latencies) < FIRST_TOKEN_BUDGET_S
    # 日志与存档跟着走
    assert (g.run_dir / "transcript.md").is_file() and (g.run_dir / "events.jsonl").is_file()
    assert (g.save_dir / "state.json").is_file()
    assert st.tick > 32 and st.turn == 30
    await s.close()
