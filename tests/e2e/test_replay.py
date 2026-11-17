"""可重放（方案 4.3、10.4）：录一局 50 回合，再按录下的模型调用与同一种子、同样的输入重放，逐字相同。"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.game import INPUTS_FILE, TEXT_REPLAY_DIVERGED, Game
from tianlong.ui.events import Notice

from .conftest import SCRIPT, Session, new_session, plain_text

TURNS = 50


async def test_fifty_turns_replay_verbatim(real_settings: Settings) -> None:
    rec = await new_session(real_settings, seed=5, run_id="rec", save_name="rec")
    used: list[str] = []
    played = 0
    for raw in SCRIPT:
        await rec.send(raw)
        used.append(raw)
        assert rec.state.ended is None
        if rec.game.last_turn is not None and rec.game.last_turn.kind == "turn":
            played += 1
        if played >= TURNS:
            break
    assert played == TURNS and rec.state.turn == TURNS
    await rec.close()
    run1 = rec.game.run_dir
    assert (run1 / INPUTS_FILE).read_text(encoding="utf-8").splitlines() == used

    game = await Game.new(
        real_settings, seed=5, mock=True, player_name="阿青", run_id="rep", save_name="rep", replay_dir=run1
    )
    assert game.llm.replay
    rep = Session(game)
    await rep.start()
    for raw in used:
        await rep.send(raw)
    await rep.close()
    run2 = game.run_dir
    assert run2 != run1

    # 玩家看到的每一个字都相同；世界里发生的每一件事也相同
    t1 = (run1 / "transcript.md").read_text(encoding="utf-8")
    t2 = (run2 / "transcript.md").read_text(encoding="utf-8")
    assert len(t1) > 5000 and t1 == t2
    assert (run1 / "events.jsonl").read_text(encoding="utf-8") == (run2 / "events.jsonl").read_text(encoding="utf-8")
    assert [plain for _, plain in _screens(rec)] == [plain for _, plain in _screens(rep)]
    assert rep.state.model_dump_json() == rec.state.model_dump_json()
    # 重放用的是录下的调用：一次不多，一次不少；没有一处对不上，也就没有那句提示（D-354）
    assert game.llm.recorder.count == rec.game.llm.recorder.count > 0
    assert game.llm.ledger.spent == 0.0
    assert game.llm.replayer is not None
    assert game.llm.replayer.mismatches == []
    assert game.llm.replayer.remaining() == {}
    assert not _diverged_notices(rep)


def _screens(s: Session) -> list[tuple[str, str]]:
    return [(raw, plain_text(evs)) for raw, evs in s.steps]


def _diverged_notices(s: Session) -> list[Notice]:
    """「重放对不上了」的提示（按那句话固定的开头认）。"""
    head = TEXT_REPLAY_DIVERGED.split("（", 1)[0]
    return [e for _, evs in s.steps for e in evs if isinstance(e, Notice) and e.text.startswith(head)]


async def test_diverging_inputs_are_reported_once(settings: Settings) -> None:
    """重放时输入与录下的不同：对不上的调用照旧兜底、游戏走完，但要提示玩家一次，并记进回合的记账（D-354）。"""
    rec = await new_session(settings, seed=5, run_id="rec", save_name="rec")
    for raw in ("一", "我四下看看", "二"):
        await rec.send(raw)
    await rec.close()

    game = await Game.new(
        settings, seed=5, mock=True, player_name="阿青", run_id="rep", save_name="rep", replay_dir=rec.game.run_dir
    )
    rep = Session(game)
    await rep.start()
    assert game.llm.replayer is not None and game.llm.replayer.mismatches == []  # 开场与录下的一样
    notes: list[list[str]] = []
    for raw in ("二", "我在这里等上半个时辰", "一", "三"):
        await rep.send(raw)  # 不抛异常
        assert game.last_turn is not None
        notes.append(list(game.last_turn.notes))
    await rep.close()
    assert rep.state.turn >= 3 and rep.state.ended is None

    found = game.llm.replayer.mismatches
    assert found
    shown = _diverged_notices(rep)
    assert len(shown) == 1 and shown[0].level == "warn"
    # 提示在头一个对不上的回合末尾：说了处数，引了头一处是哪一次调用，不带请求的内容
    first_step = next(i for i, (_, evs) in enumerate(rep.steps) if shown[0] in evs)
    assert first_step == 1 and rep.steps[first_step][1][-1] is shown[0]
    head = found[0].split(": recorded ", 1)[0]
    assert head in shown[0].text and "处" in shown[0].text and "【你是谁】" not in shown[0].text
    assert [sum(1 for n in ns if n.startswith("replay diverged")) for ns in notes] == [1, 0, 0, 0]
    # 提示进了 transcript，玩家事后也查得到
    assert shown[0].text in (game.run_dir / "transcript.md").read_text(encoding="utf-8")


async def test_same_seed_and_inputs_reproduce_without_a_recording(real_settings: Settings) -> None:
    """Mock 本身是确定的：同一种子、同样的输入，两局逐字相同。"""
    outs: list[str] = []
    for i in range(2):
        s = await new_session(real_settings, seed=3, run_id=f"r{i}", save_name=f"r{i}")
        for raw in SCRIPT[:16]:
            await s.send(raw)
        await s.close()
        outs.append((s.game.run_dir / "transcript.md").read_text(encoding="utf-8"))
    assert len(outs[0]) > 1000 and outs[0] == outs[1]
