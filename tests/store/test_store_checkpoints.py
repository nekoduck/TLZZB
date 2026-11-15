"""检查点：一日一个、大险之前一个、只留最近 10 个（方案 2.6、8.7）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.kernel.state import TICKS_PER_DAY
from tianlong.store.checkpoints import KEEP, CheckpointManager
from tianlong.store.save import ContentMismatch, SaveError


def _at(state, tick: int):
    return state.model_copy(update={"tick": tick})


def test_rotation_keeps_newest_ten(world, tmp_path: Path) -> None:
    defs, state = world
    cm = CheckpointManager(tmp_path / "game")
    paths = [cm.save(_at(state, state.tick + i), f"step{i}") for i in range(11)]
    items = cm.list()
    assert KEEP == 10 and len(items) == 10
    assert [i.seq for i in items] == list(range(11, 1, -1))  # 最新的在前
    assert not paths[0].exists() and all(p.exists() for p in paths[1:])
    assert items[0].label == "step10" and items[0].tick == state.tick + 10
    assert len(list((tmp_path / "game" / "checkpoints").glob("*.json"))) == 10
    # 序号接着往上数，不因删除而回退
    cm.save(state, "more")
    assert cm.latest().seq == 12 and len(cm.list()) == 10


def test_day_start_once_per_day(world, tmp_path: Path) -> None:
    defs, state = world
    cm = CheckpointManager(tmp_path / "g")
    p1 = cm.maybe_day_start(state)
    assert p1 is not None and p1.exists()
    assert cm.maybe_day_start(_at(state, state.tick + 8)) is None  # 同一日
    assert cm.maybe_day_start(_at(state, TICKS_PER_DAY - 1)) is None
    p2 = cm.maybe_day_start(_at(state, TICKS_PER_DAY + 3))
    assert p2 is not None and cm.latest().label == "day2" and cm.latest().day == 2
    assert cm.maybe_day_start(_at(state, TICKS_PER_DAY + 50)) is None
    # 换一个管理器（例如重新开游戏）也认得已经存过的日子
    cm2 = CheckpointManager(tmp_path / "g")
    assert cm2.maybe_day_start(_at(state, TICKS_PER_DAY + 60)) is None
    assert cm2.maybe_day_start(_at(state, 2 * TICKS_PER_DAY)) is not None
    assert [i.label for i in cm2.list()] == ["day3", "day2", "day1"]


def test_before_danger_once_per_moment(world, tmp_path: Path) -> None:
    defs, state = world
    cm = CheckpointManager(tmp_path / "g")
    assert cm.before_danger(state) is not None
    assert cm.before_danger(state) is None  # 同一刻不重复
    assert cm.before_danger(_at(state, state.tick + 1)) is not None
    assert [i.label for i in cm.list()] == ["danger", "danger"]


def test_restore(world, tmp_path: Path) -> None:
    defs, state = world
    cm = CheckpointManager(tmp_path / "g")
    cm.maybe_day_start(state)
    day2 = _at(state, TICKS_PER_DAY + 5)
    cm.maybe_day_start(day2)
    cm.before_danger(_at(state, TICKS_PER_DAY + 9))
    latest = cm.restore(content_hash=defs.content_hash)
    assert latest.tick == TICKS_PER_DAY + 9
    by_label = cm.restore("day1", content_hash=defs.content_hash)
    assert by_label.model_dump() == state.model_dump()
    info = cm.find("day2")
    assert info is not None
    assert cm.restore(info.name, content_hash=defs.content_hash).tick == day2.tick
    assert cm.restore(info.name.removesuffix(".json"), content_hash=None).tick == day2.tick
    assert cm.restore(str(info.seq), content_hash=None).tick == day2.tick
    with pytest.raises(SaveError):
        cm.restore("没有", content_hash=None)
    with pytest.raises(ContentMismatch):
        cm.restore("day1", content_hash="f" * 64)


def test_restore_resets_day_tracking(world, tmp_path: Path) -> None:
    """回到第一日的检查点之后，再过到第二日要重新存一个（新的时间线）。"""
    defs, state = world
    cm = CheckpointManager(tmp_path / "g")
    cm.maybe_day_start(state)
    cm.maybe_day_start(_at(state, TICKS_PER_DAY + 1))
    cm.restore("day1", content_hash=defs.content_hash)
    again = cm.maybe_day_start(_at(state, TICKS_PER_DAY + 7))
    assert again is not None
    assert [i.label for i in cm.list()] == ["day2", "day2", "day1"]


def test_empty_manager(tmp_path: Path) -> None:
    cm = CheckpointManager(tmp_path / "none")
    assert cm.list() == [] and cm.latest() is None and cm.find() is None
    with pytest.raises(SaveError):
        cm.restore()
