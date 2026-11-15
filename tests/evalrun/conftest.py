"""评测跑手的测试夹具：真实的 content/ 与 evals/data，Mock 的模型，结果写进临时目录；全程不联网。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.evalrun.common import EvalConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(REPO / "config.toml", env_file=tmp_path / "none.env")


@pytest.fixture
def cfg(settings: Settings, tmp_path: Path) -> EvalConfig:
    lines: list[str] = []
    c = EvalConfig(settings=settings, mock=True, limit=5, out_dir=tmp_path / "results", echo=lines.append)
    c.lines = lines  # type: ignore[attr-defined]
    return c
