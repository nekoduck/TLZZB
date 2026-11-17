"""存档：往返、版本迁移、内容指纹、复制与列表（方案 10.5）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tianlong.kernel import actions, resolve
from tianlong.kernel.state import WorldState
from tianlong.store import save as save_mod
from tianlong.store.save import (
    SAVE_VERSION,
    ContentMismatch,
    SaveError,
    SaveVersionError,
    copy_save,
    list_saves,
    load_save,
    load_state,
    save_dir,
    save_state,
)


def _played(world):
    """开局后让玩家做两件事，状态里有事件、记忆、随机数计数。"""
    defs, state = world
    for _ in range(2):
        acts = actions.legal_actions(state, defs, state.player)
        act = next(a for a in acts if a.verb in ("look", "listen", "wait") and not a.lethal)
        resolve.apply(state, defs, act, source="player")
    return defs, state


def test_round_trip_real_state(world, tmp_path: Path) -> None:
    defs, state = _played(world)
    assert state.events_tail, "应当已经有事件"
    path = save_state(state, tmp_path / "game")
    assert path == tmp_path / "game" / "state.json"
    loaded = load_state(tmp_path / "game", content_hash=defs.content_hash)
    assert isinstance(loaded, WorldState)
    assert loaded.model_dump(mode="json") == state.model_dump(mode="json")
    assert loaded.model_dump_json() == state.model_dump_json()


def test_envelope_carries_version_and_hash(world, tmp_path: Path) -> None:
    defs, state = world
    path = save_state(state, tmp_path / "a.json", extra={"label": "x", "state": "ignored"})
    env = json.loads(path.read_text(encoding="utf-8"))
    assert env["format"] == "tianlong-save"
    assert env["version"] == SAVE_VERSION
    assert env["content_hash"] == defs.content_hash == state.content_hash
    assert env["tick"] == state.tick and env["clock"] == "第一日 辰时"
    assert env["player"] == state.chars[state.player].name
    assert env["label"] == "x"
    assert isinstance(env["state"], dict) and env["state"]["version"] == SAVE_VERSION
    # 中文原样写（UTF-8），不转义
    assert "第一日" in path.read_text(encoding="utf-8")


def test_overwrite_is_atomic_and_leaves_no_temp(world, tmp_path: Path) -> None:
    defs, state = world
    d = tmp_path / "game"
    save_state(state, d)
    st2 = state.model_copy(update={"tick": state.tick + 8})
    save_state(st2, d)
    assert sorted(p.name for p in d.iterdir()) == ["state.json"]
    assert load_state(d, content_hash=defs.content_hash).tick == state.tick + 8


def test_content_mismatch(world, tmp_path: Path) -> None:
    defs, state = world
    save_state(state, tmp_path / "g")
    with pytest.raises(ContentMismatch) as ei:
        load_state(tmp_path / "g", content_hash="0" * 64)
    assert ei.value.saved == defs.content_hash
    assert "allow_mismatch" in str(ei.value)
    # 强行读入：状态改记当前的指纹
    got = load_save(tmp_path / "g", content_hash="0" * 64, allow_mismatch=True)
    assert got.mismatch and got.state.content_hash == "0" * 64
    # 不核对
    assert load_state(tmp_path / "g", content_hash=None).content_hash == defs.content_hash


def test_migration_hook_applied_in_order(world, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = world
    save_state(state, tmp_path / "old")  # 第 1 版
    calls: list[int] = []

    def v1_to_v2(data: dict) -> dict:
        calls.append(1)
        data["flags"] = {**data.get("flags", {}), "migrated_v2": True}
        return data

    def v2_to_v3(data: dict) -> dict:
        calls.append(2)
        assert data["flags"]["migrated_v2"]
        data["flags"]["migrated_v3"] = True
        return data

    monkeypatch.setattr(save_mod, "SAVE_VERSION", 3)
    monkeypatch.setattr(save_mod, "MIGRATIONS", {1: v1_to_v2, 2: v2_to_v3})
    got = load_save(tmp_path / "old", content_hash=defs.content_hash)
    assert calls == [1, 2]
    assert got.migrated_from == 1
    assert got.state.version == 3
    assert got.state.flags["migrated_v2"] and got.state.flags["migrated_v3"]


def test_register_migration_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(save_mod, "MIGRATIONS", {})

    @save_mod.register_migration(7)
    def fn(data: dict) -> dict:
        return data

    assert save_mod.MIGRATIONS == {7: fn}


def test_missing_migration_and_newer_save(world, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = world
    save_state(state, tmp_path / "s")
    monkeypatch.setattr(save_mod, "SAVE_VERSION", 2)
    monkeypatch.setattr(save_mod, "MIGRATIONS", {})
    with pytest.raises(SaveVersionError):
        load_state(tmp_path / "s", content_hash=defs.content_hash)
    # 比程序新的存档
    save_state(state, tmp_path / "new")
    monkeypatch.setattr(save_mod, "SAVE_VERSION", 1)
    with pytest.raises(SaveVersionError):
        load_state(tmp_path / "new", content_hash=defs.content_hash)


def test_bare_state_json_and_corrupt_files(world, tmp_path: Path) -> None:
    defs, state = world
    bare = tmp_path / "bare.json"
    bare.write_text(state.model_dump_json(), encoding="utf-8")
    assert load_state(bare, content_hash=defs.content_hash).tick == state.tick
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SaveError):
        load_state(bad, content_hash=None)
    with pytest.raises(SaveError):
        load_state(tmp_path / "nowhere", content_hash=None)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(SaveError):
        load_state(wrong, content_hash=None)


def test_copy_save_and_names(world, tmp_path: Path) -> None:
    defs, state = world
    root = tmp_path / "saves"
    save_state(state, save_dir(root, "当前"))
    cp = save_dir(root, "当前") / "checkpoints"
    cp.mkdir()
    (cp / "0001_day1_t32.json").write_text("{}", encoding="utf-8")
    # 目标原有的检查点被清掉
    old_cp = root / "甲" / "checkpoints"
    old_cp.mkdir(parents=True)
    (old_cp / "0009_day9_t900.json").write_text("{}", encoding="utf-8")
    dst = copy_save("当前", "甲", root=root)
    assert dst == root / "甲"
    assert load_state(dst, content_hash=defs.content_hash).model_dump() == state.model_dump()
    assert sorted(p.name for p in (dst / "checkpoints").iterdir()) == ["0001_day1_t32.json"]
    with pytest.raises(SaveError):
        copy_save("没有这个", "乙", root=root)
    for bad in ["", " ", "..", "a/b", "x:y", ".hidden"]:
        with pytest.raises(SaveError):
            save_dir(root, bad)


def test_list_saves(world, tmp_path: Path) -> None:
    import os

    defs, state = world
    root = tmp_path / "saves"
    assert list_saves(root) == []
    save_state(state, root / "早")
    later = state.model_copy(update={"tick": state.tick + 96, "turn": 5})
    save_state(later, root / "晚")
    os.utime(root / "早" / "state.json", (1, 1))
    (root / "坏").mkdir()
    (root / "坏" / "state.json").write_text("oops", encoding="utf-8")
    (root / "空").mkdir()  # 没有 state.json，不算存档
    os.utime(root / "坏" / "state.json", (0, 0))
    infos = list_saves(root)
    assert [i.name for i in infos] == ["晚", "早", "坏"]
    assert infos[0].ok and infos[0].clock == "第二日 辰时" and infos[0].turn == 5
    assert infos[0].content_hash == defs.content_hash and infos[0].player == "阿福"
    assert not infos[2].ok and infos[2].error
