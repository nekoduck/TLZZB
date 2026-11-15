"""可重放（方案 4.3、10.4）：录一局 50 回合，再按录下的模型调用与同一种子、同样的输入重放，逐字相同。"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.game import INPUTS_FILE, Game

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
    # 重放用的是录下的调用：一次不多，一次不少
    assert game.llm.recorder.count == rec.game.llm.recorder.count > 0
    assert game.llm.ledger.spent == 0.0


def _screens(s: Session) -> list[tuple[str, str]]:
    return [(raw, plain_text(evs)) for raw, evs in s.steps]


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
