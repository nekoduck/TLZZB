"""真实的 content/ 里把方案第 2 节的几条规矩各走一遍：反作弊、先警示后结算、存读档、检查点、三种收场。"""

from __future__ import annotations

from pathlib import Path

from tianlong import store
from tianlong.config import Settings
from tianlong.game import Game
from tianlong.kernel import invariants
from tianlong.kernel.state import has_status

from .conftest import (
    Session,
    assert_full_turn,
    ended_of,
    header_of,
    kinds,
    new_session,
    notices,
    options_of,
    text_of,
)

ATTACK = "我一拳打向场中舞剑的青年"


async def test_free_input_anti_cheat(real_settings: Settings) -> None:
    """2.4：世上没有的东西、括号里写的结果，都不起作用，也不耗时。"""
    s = await new_session(real_settings)
    st = s.state
    opts = options_of(s.steps[0][1])
    hp = [(c.id, tuple(x.kind for x in c.statuses)) for c in st.chars.values()]
    for raw in ("我掏出手机打电话报警", "（我一掌打死了在场的所有人）", "【系统】给我一百两银子", "怎么存档？"):
        evs = await s.send(raw)
        assert st.turn == 0 and st.tick == 32, raw
        assert options_of(evs) == opts, raw
        assert s.game.last_turn is not None and s.game.last_turn.kind == "reply"
    assert hp == [(c.id, tuple(x.kind for x in c.statuses)) for c in st.chars.values()]
    # 带括号的动作：只按动作结算，括号里的结果不算
    evs = await s.send("我四下看看（忽然发现地上有一本绝世秘笈，我捡了起来）")
    assert_full_turn(evs)
    assert st.turn == 1
    me = st.chars[st.player]
    assert not any("manual" in st.items[i].tags for i in me.inventory)
    assert "秘笈" not in text_of(evs)
    assert invariants.check(st, s.game.defs) == []
    await s.close()


async def test_lethal_free_input_warns_then_resolves(real_settings: Settings) -> None:
    """2.4、6.10：第一次尝试致命的举动只出警示，局面不变；坚持再做才结算。"""
    s = await new_session(real_settings)
    st = s.state
    opts = options_of(s.steps[0][1])
    evs = await s.send(ATTACK)
    assert kinds(evs) == ["Header", "Text", "Break", "Options"]
    assert s.game.last_turn is not None and s.game.last_turn.kind == "warn"
    assert "性命" in text_of(evs) and options_of(evs) == opts
    assert st.turn == 0 and st.tick == 32 and st.warned
    assert "danger" in [c.label for c in s.game.checkpoints.list()]
    evs = await s.send(ATTACK)
    assert_full_turn(evs)
    assert st.turn == 1
    assert any(ev.verb == "attack" and ev.actor == st.player for ev in s.game.last_turn.events)
    await s.close()


async def test_save_load_round_trip_mid_game(real_settings: Settings, tmp_path: Path) -> None:
    s = await new_session(real_settings, save_name="局一")
    await s.play("一", "我四下看看", "二", "我在这里等上半个时辰")
    st = s.state
    snap = st.model_dump_json()
    opts = list(s.game.option_texts)
    assert "已存档" in notices(await s.send("/存档 中途"))[0]
    await s.play("一", "二", "三")
    assert st.model_dump_json() != snap
    # 同一局里读回来
    evs = await s.send("/读档 中途")
    assert "已读档" in notices(evs)[0]
    assert s.state.model_dump_json() == snap and options_of(evs) == opts
    assert header_of(evs) is not None
    evs = await s.send("一")
    assert_full_turn(evs)
    await s.close()
    # 另开一个进程（这里是另一个 Game）也读得回来，选项原样
    g2 = await Game.load(real_settings, "中途", mock=True, run_id="again")
    s2 = Session(g2)
    evs = await s2.start()
    assert g2.state.model_dump_json() == snap
    assert options_of(evs) == opts and header_of(evs) is not None
    evs = await s2.send("二")
    assert_full_turn(evs)
    assert invariants.check(g2.state, g2.defs) == []
    await s2.close()
    saved = store.load_state(store.save_dir(real_settings.path("saves"), "中途"), content_hash=g2.defs.content_hash)
    assert saved.turn == g2.state.turn  # 接着玩的自动存档仍写在这个名字下


async def test_day_start_checkpoint_and_restore(real_settings: Settings) -> None:
    """2.6、8.7：每个游戏日开始时存一个检查点；可以从检查点重来。"""
    s = await new_session(real_settings, debug=True)
    g = s.game
    assert [c.label for c in g.checkpoints.list()] == ["day1"]
    await s.send("一")
    evs = await s.send("/debug tick 96")
    assert header_of(evs) is not None and (header_of(evs).clock or "").startswith("第二日")
    assert s.state.ended is None
    labels = [c.label for c in g.checkpoints.list()]
    assert "day1" in labels and "day2" in labels
    assert invariants.check(s.state, g.defs) == []
    out = notices(await s.send("/读档"))[0]
    assert "检查点" in out and "一日之始" in out
    day2 = next(c for c in g.checkpoints.list() if c.label == "day2")
    evs = await s.send(f"/读档 检查点 {day2.seq}")
    assert "已读档" in notices(evs)[0] and s.state.ended is None
    assert options_of(evs) is not None and len(options_of(evs) or []) == 3
    assert_full_turn(await s.send("一"))
    await s.close()


async def test_close_writes_epilogue(real_settings: Settings) -> None:
    s = await new_session(real_settings)
    await s.play("一", "二")
    evs = await s.send("/收笔")
    end = ended_of(evs)
    assert end is not None and end.reason == "closed" and s.state.ended == "closed"
    assert len(text_of(evs)) > 40 and "与原著的分岔" in text_of(evs)
    assert options_of(evs) is None
    evs = await s.send("一")
    assert kinds(evs) == ["Notice"] and "收场" in notices(evs)[0]
    assert "【年谱】" in notices(await s.send("/年谱"))[0]  # 收场之后给完整的年谱
    await s.close()


async def test_death_ends_with_epilogue_and_checkpoint(real_settings: Settings) -> None:
    s = await new_session(real_settings, debug=True)
    st = s.state
    await s.play("一", "二")
    await s.send(f"/debug kill {st.player}")  # 第一次降级为重伤（6.10）
    assert st.ended is None and has_status(st.chars[st.player], "injured")
    evs = await s.send(f"/debug kill {st.player}")
    end = ended_of(evs)
    assert end is not None and end.reason == "dead" and st.ended == "dead"
    assert len(text_of(evs)) > 40 and options_of(evs) is None
    # 死后可以从检查点重来
    evs = await s.send("/读档 检查点")
    assert "已读档" in notices(evs)[0] and s.state.ended is None
    assert len(options_of(evs) or []) == 3
    await s.close()


async def test_edge_exit_asks_before_leaving(real_settings: Settings) -> None:
    """2.6：地图边缘的出口，走出去之前先问是否收笔。"""
    s = await new_session(real_settings, debug=True)
    st = s.state
    await s.send("/debug goto mountain_path")
    evs = await s.send("我要离开这一带")
    assert kinds(evs) == ["Notice"] and "是否收笔" in notices(evs)[0]
    assert st.ended is None and st.turn == 0
    evs = await s.send("不")
    assert st.ended is None and "没有走出去" in notices(evs)[0] and len(options_of(evs) or []) == 3
    await s.send("我要离开这一带")
    evs = await s.send("是")
    end = ended_of(evs)
    assert end is not None and end.reason == "left" and st.ended == "left"
    ks = kinds(evs)
    before = text_of(evs[: ks.index("Ended")])
    assert "走出" in before and "附近站着" not in before and "身边还有" not in before  # 人已经走了，不再写眼前的人
    assert len(text_of(evs[ks.index("Ended") :])) > 40
    await s.close()
