from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tianlong.config import Settings
from werewolf.llm.config import WerewolfSettings, load

REPO = Path(__file__).resolve().parents[3]
KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY', 'CLAUDE_KEY', 'CLAUDE_API_KEY')


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    """仓库的 werewolf.toml 拷到临时目录（不读 .env）；密钥全清掉，所有测试都离线。"""
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    shutil.copy(REPO / 'werewolf.toml', tmp_path / 'werewolf.toml')
    return load(tmp_path / 'werewolf.toml')
