"""一局的日志：events.jsonl 与 transcript.md（方案 10.5）。"""

from __future__ import annotations

import json
from pathlib import Path

from tianlong.kernel import actions, resolve
from tianlong.kernel.state import Event
from tianlong.store.log import RunLog, read_events
from tianlong.ui.events import Break, Header, Options, Text
from tianlong.ui.text import plain_text


def _player_events(defs, state):
    acts = actions.legal_actions(state, defs, state.player)
    act = next(a for a in acts if a.verb == "look")
    return resolve.apply(state, defs, act, source="player")


def _offscreen(state) -> Event:
    return Event(
        id=state.next_event_id + 100,
        tick=state.tick,
        loc="shennong_camp",
        actor="sikong_xuan",
        verb="wait",
        params={},
        outcome="ok",
        fact_text="⟦sikong_xuan⟧在营里等着",
        witnesses={},
        source="agenda",
        offscreen=True,
    )


def test_log_events_full_dumps_incl_offscreen(world, tmp_path: Path) -> None:
    defs, state = world
    evs = _player_events(defs, state)
    assert evs
    off = _offscreen(state)
    log = RunLog(tmp_path / "run1")
    n = log.log_events([*evs, off], turn=3, state=state, defs=defs)
    assert n == len(evs) + 1
    rows = read_events(tmp_path / "run1")
    assert [r["id"] for r in rows] == [e.id for e in evs] + [off.id]
    assert all(r["turn"] == 3 for r in rows)
    last = rows[-1]
    assert last["offscreen"] is True and last["source"] == "agenda"
    # 完整的事件字段都在，且能读回成 Event
    assert Event.model_validate(last) == off
    assert last["text"] == "司空玄在营里等着"  # 全知视角：真名
    # 每行一个 JSON，UTF-8 中文不转义
    raw = (tmp_path / "run1" / "events.jsonl").read_text(encoding="utf-8")
    assert len(raw.strip().splitlines()) == len(rows)
    assert "司空玄" in raw
    # 同一条不重复写；reset_seen 之后可以再写
    assert log.log_events([off]) == 0
    log.reset_seen()
    assert log.log_events([off.model_dump()]) == 1
    assert len(read_events(tmp_path / "run1")) == len(rows) + 1


def test_log_transcript_exact(tmp_path: Path) -> None:
    log = RunLog(tmp_path / "r")
    log.log_transcript("〔第一日 辰时 · 某处〕\n\n")
    log.log_transcript("一段话。\n")
    assert (tmp_path / "r" / "transcript.md").read_text(
        encoding="utf-8"
    ) == "〔第一日 辰时 · 某处〕\n\n一段话。\n"


def test_log_turn_writes_events_transcript_and_review(world, tmp_path: Path) -> None:
    defs, state = world
    log = RunLog(tmp_path / "r", review_keep=2)
    ui = [
        Header("第一日 辰时", "练武厅"),
        Text("厅上两人"),
        Text("斗剑正酣。"),
        Break(),
        Options(["劝一句", "抬头看看", "往门口挪"]),
    ]
    shown = plain_text(ui)
    log.log_turn(state, [], shown)  # 开场：没有输入
    evs = _player_events(defs, state)
    shown2 = plain_text(
        [Header("第一日 辰时", "练武厅"), Text("你四下看了看。"), Break(), Options(["甲", "乙", "丙"])]
    )
    log.log_turn(state, evs, shown2, player_input="二", defs=defs)
    t = (tmp_path / "r" / "transcript.md").read_text(encoding="utf-8")
    assert t.startswith(
        "〔第一日 辰时 · 练武厅〕\n\n厅上两人斗剑正酣。\n\n一、劝一句\n二、抬头看看\n三、往门口挪\n\n"
    )
    assert "也可以直接写下你想说的话、想做的事：" in t
    assert "\n> 二\n\n〔第一日 辰时 · 练武厅〕\n\n你四下看了看。" in t
    assert t.index("劝一句") < t.index("> 二") < t.index("你四下看了看")
    rows = read_events(tmp_path / "r")
    assert [r["id"] for r in rows] == [e.id for e in evs]
    assert all(r["turn"] == state.turn for r in rows)
    # 回顾缓冲：最近的在后；带上玩家的输入
    recent = log.recent(5)
    assert len(recent) == 2
    assert recent[-1].startswith("> 二") and "你四下看了看。" in recent[-1]
    log.log_turn(state, [], plain_text([Text("第三回。")]))
    assert len(log.turns) == 2 and log.recent(1) == ["第三回。"]
    assert log.recent(0) == []


def test_calls_file_untouched(tmp_path: Path) -> None:
    log = RunLog(tmp_path / "r")
    log.log_transcript("x")
    assert not log.calls_path.exists()
    assert json.loads(json.dumps({"ok": 1}))  # sanity


def test_transcript_equals_screen(world, tmp_path: Path) -> None:
    """transcript.md 与纯文本界面上显示的一模一样（含输入行、抬头、选项）。"""
    import io

    from tianlong.ui.events import Notice
    from tianlong.ui.render import Renderer

    defs, state = world
    turns = [
        (None, [Header("第一日 辰时", "厅"), Text("开场。"), Break(), Options(["甲", "乙", "丙"])]),
        ("二", [Header("第一日 辰时", "厅"), Text("你走了一步"), Text("。"), Break(), Options(["丁", "戊", "己"])]),
        ("/状态", [Notice("【状态】\n时辰　第一日 辰时")]),
        ("你好", [Header("第一日 巳时", "厅"), Text("他点点头。"), Break(), Options(["庚", "辛", "壬"])]),
    ]
    screen = io.StringIO()
    r = Renderer(plain=True, out=screen, width=None)
    log = RunLog(tmp_path / "r")
    for raw, ui in turns:
        if raw is not None:
            r.echo_input(raw)
        r.render_all(ui)
        r.finish()
        log.log_turn(state, [], plain_text(ui), player_input=raw)
    assert (tmp_path / "r" / "transcript.md").read_text(encoding="utf-8") == screen.getvalue()
