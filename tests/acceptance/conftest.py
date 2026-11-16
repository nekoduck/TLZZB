"""验收测试的夹具（方案 11.2、11.4）：真实的 content/，Mock 的模型，日志与存档写进临时目录。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tianlong.config import Settings
from tianlong.testing import make_settings


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    """世界推演的日志很多（每个时辰几十行），验收测试里关掉。"""
    logging.disable(logging.WARNING)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def real_settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)
