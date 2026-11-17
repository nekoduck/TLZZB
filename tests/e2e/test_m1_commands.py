"""M1 的闸，从命令行这一头再验一遍（方案第 12 节）：只用选项与 /debug，不经模型，把 ``bout_laugh`` 玩通，并学会一门本事。"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.kernel import invariants

from .conftest import Session, assert_full_turn, new_session, notices


async def _play_bout(s: Session, flag_prefix: str = "") -> int:
    """一直选第一项，直到那场比剑的事了结。返回用了几回合。"""
    st = s.state
    assert any(x.kind == "bout_laugh" for x in st.situations.values())
    n = 0
    while any(x.kind == "bout_laugh" for x in st.situations.values()):
        evs = await s.send("一")
        assert_full_turn(evs)
        assert s.game.last_turn is not None and s.game.last_turn.kind == "turn"
        assert invariants.check(st, s.game.defs) == []
        n += 1
        assert n <= 8, "bout_laugh did not finish"
    return n


async def test_bout_laugh_by_options_without_any_model_call(settings: Settings) -> None:
    s = await new_session(settings, debug=True)
    g, st = s.game, s.state
    g._use_rules_only()  # 不经模型：归类、抉择、选项、叙述全用规则与模板
    calls = g.llm.recorder.count
    n = await _play_bout(s)
    assert 1 <= n <= 6
    # 这场事有了结果：要么挨了一掌，要么赔了不是
    assert st.flags.get("bout_slapped") or st.flags.get("bout_apologized") or st.chars["guest"].loc != "hall"
    assert st.counters.get("bout_laugh#") == 1
    chron = notices(await s.send("/年谱"))[0]
    assert chron.startswith("【年谱】") and "第一日 辰时" in chron  # 亲见的事记进了年谱
    # 学一门本事：/debug 给一本图谱，选项里就有「研读」；读够了就会
    evs = await s.send("/debug give old_manual")
    assert "已生效" in notices(evs)[0]
    me = st.chars[st.player]
    assert "old_manual" in me.inventory and "cloud_steps" not in me.arts
    studied = 0
    for _ in range(12):
        idx = next((i for i, o in enumerate(g.options) if o.action.verb == "study"), None)
        if idx is None:
            # 这一回合没有摆出「研读」：随便做点不挪地方的事，选项会轮换
            idx = next(i for i, o in enumerate(g.options) if o.action.verb not in ("go", "flee"))
        else:
            assert "研读" in g.option_texts[idx]
            studied += 1
        evs = await s.send("一二三"[idx])
        assert_full_turn(evs)
        if me.arts.get("cloud_steps", 0) >= 1:
            break
    assert studied >= 2
    assert me.arts.get("cloud_steps", 0) >= 1
    assert st.tick >= 32 + 16  # 至少两个时辰的研读
    assert g.llm.recorder.count == calls  # 从头到尾一次模型调用也没有
    assert invariants.check(st, g.defs) == []
    await s.close()


async def test_bout_laugh_in_real_content_by_options(real_settings: Settings) -> None:
    s = await new_session(real_settings, debug=True)
    st = s.state
    n = await _play_bout(s)
    assert 1 <= n <= 6
    assert st.flags.get("slapped") or st.flags.get("apologized")
    assert st.flags.get("bout_over") and st.flags.get("guest_laughed")
    await s.close()
