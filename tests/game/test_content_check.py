"""开新局前校验内容库（content.validate）：坏了的内容不开局，问题一条条列出来；读档不再校验。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import tianlong.game as game_mod
from tianlong.content.loader import ContentError
from tianlong.content.validate import ContentInvalid
from tianlong.game import Game

from .conftest import MINI, assert_full_turn, make_settings, new_session


def _broken_mini(tmp_path: Path) -> Path:
    """小世界的一份副本：一件东西写在不存在的人身上，一个出口通向不存在的地方。"""
    dst = tmp_path / "broken"
    shutil.copytree(MINI, dst)
    items = yaml.safe_load((dst / "items.yaml").read_text(encoding="utf-8"))
    items["token"]["holder"] = "nobody_here"
    (dst / "items.yaml").write_text(yaml.safe_dump(items, allow_unicode=True), encoding="utf-8")
    locs = yaml.safe_load((dst / "locations.yaml").read_text(encoding="utf-8"))
    locs["yard"]["exits"].append({"to": "nowhere", "cost": 1})
    (dst / "locations.yaml").write_text(yaml.safe_dump(locs, allow_unicode=True), encoding="utf-8")
    return dst


async def test_new_game_refuses_content_that_fails_validation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, _broken_mini(tmp_path))
    with pytest.raises(ContentInvalid) as ei:
        await Game.new(settings, seed=1, mock=True, run_id="run")
    err = ei.value
    assert isinstance(err, ContentError) and len(err.problems) >= 2
    text = str(err)
    assert "没有通过校验" in text and f"共 {len(err.problems)} 个问题" in text and str(settings.path("content")) in text
    assert "nobody_here" in text and "nowhere" in text
    assert all(f"  - {p}" in text for p in err.problems[:20])
    # 没有开局：日志与存档目录都没有建
    assert not (tmp_path / "runs" / "run").exists() and not (tmp_path / "saves").exists()


async def test_validation_can_be_skipped_for_deliberately_partial_content(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, _broken_mini(tmp_path))
    game = await Game.new(settings, seed=1, mock=True, run_id="run", validate=False)
    assert game.state.items["token"].holder == "nobody_here"
    await game.aclose()


async def test_validation_runs_once_for_a_new_game_and_not_on_load(
    settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    calls: list[str] = []
    real = game_mod.require_valid

    def spy(defs, state, *, where=""):  # noqa: ANN001, ANN202
        calls.append(where)
        return real(defs, state, where=where)

    monkeypatch.setattr(game_mod, "require_valid", spy)
    s = await new_session(settings, save_name="keep")
    assert calls == [str(settings.path("content"))]
    assert_full_turn(await s.send("一"))
    await s.close()
    g2 = await Game.load(settings, "keep", mock=True, run_id="run2")
    evs = [ev async for ev in g2.start()]
    assert evs and g2.state.turn == 1
    await g2.aclose()
    assert len(calls) == 1  # 读档不再校验
