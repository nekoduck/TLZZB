"""一回合的主循环（方案 4.2、2.4 至 2.7）：在小世界里逐项验。全部用 Mock，不联网。"""

from __future__ import annotations

import json
from pathlib import Path

from tianlong import store
from tianlong.config import Settings
from tianlong.game import INPUTS_FILE, META_FILE, Game
from tianlong.kernel import invariants
from tianlong.kernel.state import has_status
from tianlong.ui.events import FREE_INPUT_HINT, WARN_PREFIX

from .conftest import (
    Session,
    assert_full_turn,
    ended_of,
    header_of,
    kinds,
    new_session,
    notices,
    options_of,
    plain_text,
    text_of,
)

PHONE = "我掏出手机打电话报警"
ATTACK = "我一拳打向那个黑脸汉子"


async def test_start_gives_header_narration_and_three_options(settings: Settings) -> None:
    s = await new_session(settings)
    evs = s.steps[0][1]
    assert_full_turn(evs)
    h = header_of(evs)
    assert h is not None and h.clock == "第一日 辰时" and h.place == "演武堂"
    st = s.state
    assert st.turn == 0 and st.tick == 32
    # 开局那一刻条件已经成立的情境，在玩家眼前就开场
    assert "bout_laugh" in [x.kind for x in st.situations.values()] and st.scene_mode == "tense"
    # 选项存进状态（存档里也有），每条带文字
    assert [d["text"] for d in st.pending_options] == options_of(evs)
    # 自动存档、第一日的检查点、日志
    g = s.game
    assert (g.save_dir / "state.json").is_file()
    assert [c.label for c in g.checkpoints.list()] == ["day1"]
    meta = json.loads((g.run_dir / META_FILE).read_text(encoding="utf-8"))
    assert meta["seed"] == 1 and meta["player_name"] == "阿青" and meta["mock"] is True
    transcript = (g.run_dir / "transcript.md").read_text(encoding="utf-8")
    assert transcript.startswith("〔第一日 辰时 · 演武堂〕") and FREE_INPUT_HINT in transcript
    await s.close()


async def test_option_pick_plays_a_turn(settings: Settings) -> None:
    s = await new_session(settings)
    first = options_of(s.steps[0][1])
    assert first is not None
    sig = s.game.options[0].action.signature
    evs = await s.send("一")
    assert_full_turn(evs)
    st = s.state
    assert st.turn == 1
    assert s.game.last_turn is not None and s.game.last_turn.kind == "turn"
    # 玩家的动作确实结算了：事件日志里有他这一步
    mine = [ev for ev in s.game.last_turn.events if ev.actor == st.player and ev.source == "player"]
    assert mine and st.storyteller["last_chosen"]["sig"] == sig
    # 在场的人有反应（微轮）：胜者卖破绽，客人笑出声
    verbs = [ev.verb for ev in s.game.last_turn.events]
    assert "feint_win" in verbs and "laugh" in verbs
    # 上回合选中的那条不再原样给出
    new = options_of(evs)
    assert new is not None and first[0] not in new
    # 数字也认；全角由界面换成半角
    evs = await s.send("2")
    assert_full_turn(evs)
    assert s.state.turn == 2
    # 日志：事件全量、玩家看到的全文、输入
    g = s.game
    events = store.read_events(g.run_dir)
    assert events and all("turn" in e and "text" in e for e in events)
    assert [e["id"] for e in events] == sorted({e["id"] for e in events})
    transcript = (g.run_dir / "transcript.md").read_text(encoding="utf-8")
    assert "> 一\n" in transcript and "> 2\n" in transcript
    assert (g.run_dir / INPUTS_FILE).read_text(encoding="utf-8").splitlines() == ["一", "2"]
    assert (g.run_dir / "calls.jsonl").is_file()
    assert invariants.check(st, g.defs) == []
    await s.close()


async def test_out_of_range_option(settings: Settings) -> None:
    s = await new_session(settings)
    s.game.options = s.game.options[:2]
    evs = await s.send("三")
    assert kinds(evs) == ["Notice"] and s.state.turn == 0
    await s.close()


async def test_free_input_is_parsed_and_resolved(settings: Settings) -> None:
    s = await new_session(settings)
    evs = await s.send("我去前院看看")
    assert_full_turn(evs)
    st = s.state
    assert st.chars[st.player].loc == "yard" and st.turn == 1
    h = header_of(evs)
    assert h is not None and h.place == "一处院子"  # 玩家还不知道这地方的名字
    assert "演武堂" in text_of(await s.send("回演武堂去")) or st.chars[st.player].loc == "hall"
    await s.close()


async def test_phone_takes_no_time_and_keeps_options(settings: Settings) -> None:
    """2.4：提到世界里不存在的东西，不耗时，一句话带过，原选项再给一遍。"""
    s = await new_session(settings)
    before = s.state.model_dump_json()
    opts = options_of(s.steps[0][1])
    evs = await s.send(PHONE)
    assert kinds(evs) == ["Header", "Text", "Break", "Options"]
    assert "恍惚" in text_of(evs) and "手机" not in text_of(evs)
    assert options_of(evs) == opts
    assert s.game.last_turn is not None and s.game.last_turn.kind == "reply"
    assert s.state.model_dump_json() == before  # 世界一点没变
    await s.close()


async def test_bracket_results_are_ignored(settings: Settings) -> None:
    """2.4：括号里写结果不起作用。只有括号的输入不耗时；带括号的动作只按动作结算。"""
    s = await new_session(settings)
    before = s.state.model_dump_json()
    evs = await s.send("（我一掌把那个黑脸汉子打晕了，夺了他的长剑）")
    assert s.game.last_turn is not None and s.game.last_turn.kind == "reply"
    assert s.state.model_dump_json() == before and options_of(evs) == options_of(s.steps[0][1])
    evs = await s.send("我去前院看看（路上捡到一柄长剑，武功大进，那个黑脸汉子当场拜我为师）")
    assert_full_turn(evs)
    st = s.state
    me = st.chars[st.player]
    assert me.loc == "yard"
    assert "sword" not in me.inventory and st.items["sword"].holder == "fighter"
    assert me.base_power == 0 and me.arts == {} and st.chars["fighter"].superior != st.player
    assert "长剑" not in text_of(evs) and "拜" not in text_of(evs)
    await s.close()


async def test_lethal_action_warns_first_then_resolves(settings: Settings) -> None:
    """2.4、6.10：第一次尝试致命的举动，写明危险、局面不变；再做一次才真正结算。"""
    s = await new_session(settings)
    st = s.state
    opts = options_of(s.steps[0][1])
    tail = st.next_event_id
    evs = await s.send(ATTACK)
    assert kinds(evs) == ["Header", "Text", "Break", "Options"]
    assert s.game.last_turn is not None and s.game.last_turn.kind == "warn"
    assert "性命" in text_of(evs) and options_of(evs) == opts
    assert st.turn == 0 and st.tick == 32
    assert any(w.startswith("attack:") for w in st.warned)
    new = [ev for ev in st.events_tail if ev.id >= tail]
    assert [ev.verb for ev in new] == ["warn_danger"]  # 只有一条警示，没有人动手，也没有人反应
    assert "danger" in [c.label for c in s.game.checkpoints.list()]  # 性命之忧之前存了检查点
    # 坚持再做：这回真的动手了
    evs = await s.send(ATTACK)
    assert_full_turn(evs)
    assert st.turn == 1
    attacks = [ev for ev in s.game.last_turn.events if ev.verb == "attack" and ev.actor == st.player]
    assert attacks and attacks[0].outcome != "blocked"
    await s.close()


async def test_edge_exit_asks_before_leaving(settings: Settings) -> None:
    """2.6：走出地图之前先问是否收笔；「是」才走出去，收场为 left。"""
    s = await new_session(settings)
    await s.send("我去前院看看")
    st = s.state
    evs = await s.send("我要离开这一带")
    assert kinds(evs) == ["Notice"] and "是否收笔" in notices(evs)[0]
    assert st.ended is None and st.chars[st.player].loc == "yard"
    turn = st.turn
    evs = await s.send("不")
    assert st.ended is None and st.turn == turn and options_of(evs) is not None
    # 问过之后输入别的：照常当作这一回合的输入
    await s.send("我要离开这一带")
    evs = await s.send("我四下看看")
    assert st.ended is None and st.turn == turn + 1 and st.chars[st.player].loc == "yard"
    assert "没有走出去" in notices(evs)[0]
    assert_full_turn(evs[1:])
    await s.send("我要离开这一带")
    evs = await s.send("是")
    end = ended_of(evs)
    assert end is not None and end.reason == "left" and st.ended == "left"
    assert not st.chars[st.player].onstage
    ks = kinds(evs)
    assert ks[0] == "Header" and ks.index("Ended") > ks.index("Break")
    after = evs[ks.index("Ended") + 1 :]
    assert text_of(after).strip() and "与原著的分岔" in text_of(after)  # 后事，再加规则列出的分岔清单
    assert options_of(evs) is None and st.pending_options == []
    # 收场之后不再接受动作
    evs = await s.send("一")
    assert kinds(evs) == ["Notice"] and "收场" in notices(evs)[0]
    await s.close()


async def test_close_command_writes_epilogue(settings: Settings) -> None:
    """2.6：/收笔 任何时候都可以，写一段后事。"""
    s = await new_session(settings)
    await s.play("一", "二")
    evs = await s.send("/收笔")
    end = ended_of(evs)
    assert end is not None and end.reason == "closed" and s.state.ended == "closed"
    body = text_of(evs)
    assert "你在这里收了笔" in body and "与原著的分岔" in body
    # 存档里也是收场的样子；年谱此后显示完整的三栏
    saved = store.load_state(s.game.save_dir, content_hash=s.game.defs.content_hash)
    assert saved.ended == "closed"
    assert "当时不知道的" in notices(await s.send("/年谱"))[0]
    assert "收场" in notices(await s.send("/收笔"))[0]
    await s.close()


async def test_death_ends_the_game_with_epilogue(settings: Settings) -> None:
    """2.6、6.9：中毒到期即死；死亡即收场，同样写后事；之后可以从检查点重来。"""
    s = await new_session(settings)
    st = s.state
    await s.send("我去前院看看")
    s.fx([{"add_status": [st.player, "poisoned_slow", {"ticks": 12}]}])
    s.game._save()  # 布景之后的样子进自动存档（平时每回合末自动存）
    evs = await s.send("我在这里等上两个时辰")
    end = ended_of(evs)
    assert end is not None and end.reason == "dead" and st.ended == "dead"
    assert 32 + 12 <= st.tick <= 32 + 16  # 毒发在 12 刻之后（内核把这一个时辰走完）
    assert has_status(st.chars[st.player], "dead")
    ks = kinds(evs)
    assert ks[0] == "Header" and ks.index("Ended") > ks.index("Break")
    assert "你的这一程，到这里就断了" in text_of(evs[ks.index("Ended") :])
    # 死前的那一刻留了检查点，可以读回来接着玩
    cps = s.game.checkpoints.list()
    assert cps[0].label == "danger"
    evs = await s.send("/读档 检查点")
    assert s.state.ended is None and has_status(s.state.chars[s.state.player], "poisoned_slow")
    assert_full_turn([e for e in evs if type(e).__name__ != "Notice"])
    evs = await s.send("一")
    assert_full_turn(evs)
    await s.close()


async def test_commands_take_no_time(settings: Settings) -> None:
    s = await new_session(settings)
    await s.send("一")
    st = s.state
    before = st.model_dump_json()
    heads = {
        "/状态": "【状态】",
        "/物品": "【物品】",
        "/人物": "【人物】",
        "/本事": "【本事】",
        "/牵挂": "【牵挂】",
        "/年谱": "【年谱】",
        "/回顾": "【回顾】",
        "/帮助": "【帮助】",
        "/help": "【帮助】",
    }
    for cmd, head in heads.items():
        evs = await s.send(cmd)
        assert kinds(evs) == ["Notice"], cmd
        assert notices(evs)[0].startswith(head), cmd
        assert s.game.last_turn is not None and s.game.last_turn.kind == "command"
    assert "演武堂" in notices(await s.send("/回顾 1"))[0]  # 回顾里是玩家看到过的文字
    assert "没有" in notices(await s.send("/胡说"))[0]
    assert "--debug" in notices(await s.send("/debug"))[0]  # 没开 --debug 时不可用
    assert st.model_dump_json() == before
    transcript = (s.game.run_dir / "transcript.md").read_text(encoding="utf-8")
    assert "> /状态\n" in transcript and "【状态】" in transcript
    await s.close()


async def test_wish_command(settings: Settings) -> None:
    s = await new_session(settings)
    st = s.state
    evs = await s.send("/心愿 学会云步")
    assert "记下了你的心愿" in notices(evs)[0]
    wishes = [t for t in st.threads.values() if t.kind == "wish" and t.id.startswith("wish:")]
    assert len(wishes) == 1 and "云步" in wishes[0].text and not wishes[0].closed
    assert st.turn == 0 and st.tick == 32
    assert "云步" in notices(await s.send("/牵挂"))[0]
    assert "云步" in notices(await s.send("/心愿"))[0]
    assert "撤销" in notices(await s.send("/心愿 撤销"))[0]
    assert wishes[0].closed
    await s.close()


async def test_save_and_load_round_trip(settings: Settings, tmp_path: Path) -> None:
    """10.5：/存档 把此刻复制成一个档；/读档 读回；另开一局也读得回来，选项原样。"""
    s = await new_session(settings)
    await s.play("一", "二")
    st = s.state
    evs = await s.send("/存档 甲")
    assert "已存档：甲" in notices(evs)[0]
    saved_json = st.model_dump_json()
    saved_opts = list(s.game.option_texts)
    assert (tmp_path / "saves" / "甲" / "state.json").is_file()
    assert (tmp_path / "saves" / "甲" / "checkpoints").is_dir()  # 检查点一并复制
    await s.play("三", "我去前院看看", "一")
    assert s.state.turn == 5 and s.state.model_dump_json() != saved_json
    evs = await s.send("/读档 甲")
    assert "已读档：甲" in notices(evs)[0]
    assert s.state.model_dump_json() == saved_json  # 读回来与存下时一模一样
    assert options_of(evs) == saved_opts and header_of(evs) is not None
    # 接着玩，走的是存档那一刻往后的路；原档不动
    evs = await s.send("一")
    assert_full_turn(evs)
    assert s.state.turn == 3
    kept = store.load_state(tmp_path / "saves" / "甲", content_hash=s.game.defs.content_hash)
    assert kept.model_dump_json() == saved_json
    await s.close()
    # 另开进程接着玩（--load）
    g2 = await Game.load(settings, "甲", mock=True, run_id="run2")
    s2 = Session(g2)
    evs = await s2.start()
    assert_full_turn(evs)
    assert options_of(evs) == saved_opts and g2.state.model_dump_json() == saved_json
    evs = await s2.send("一")
    assert_full_turn(evs)
    assert g2.state.turn == 3 and g2.save_name == "甲"
    await s2.close()
    # 不存在的档、不合规矩的名字：一句提示，不出错
    g3 = await Game.new(settings, seed=1, mock=True, run_id="run3")
    s3 = Session(g3)
    await s3.start()
    for bad in ("/读档 没有这个档", "/读档 ../x", "/存档 a/b"):
        evs = await s3.send(bad)
        assert kinds(evs) == ["Notice"] and evs[0].level == "warn", bad  # type: ignore[union-attr]
    assert "用法" in notices(await s3.send("/存档"))[0]
    assert "甲" in notices(await s3.send("/读档"))[0]
    await s3.close()


async def test_checkpoint_at_day_start(settings: Settings) -> None:
    """2.6、8.7：每个游戏日开始时存一个检查点。"""
    s = await new_session(settings, debug=True)
    await s.play("一", "二")  # 把开场那场事演完
    await s.send("我去前院看看")
    st = s.state
    assert [c.label for c in s.game.checkpoints.list()] == ["day1"]
    evs = await s.send("/debug tick 96")
    assert st.tick >= 32 + 96 and header_of(evs) is not None
    labels = [c.label for c in s.game.checkpoints.list()]
    assert "day2" in labels and "day1" in labels
    cp = s.game.checkpoints.find("day2")
    assert cp is not None and cp.day == 2
    # 读回第二日的检查点
    await s.send("一")
    evs = await s.send(f"/读档 检查点 {cp.seq}")
    assert s.state.tick == cp.tick and options_of(evs) is not None
    await s.close()


def test_plain_text_of_a_turn_matches_transcript(settings: Settings) -> None:
    """transcript 与纯文本界面逐字一致（D-190）。"""
    import asyncio

    async def run() -> None:
        s = await new_session(settings)
        evs = await s.send("一")
        shown = plain_text(s.steps[0][1]) + "> 一\n\n" + plain_text(evs)
        transcript = (s.game.run_dir / "transcript.md").read_text(encoding="utf-8")
        assert transcript == shown
        assert WARN_PREFIX not in transcript
        await s.close()

    asyncio.run(run())
