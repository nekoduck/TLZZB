from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.llm import make_llm

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
CORPUS = FIXTURES / 'corpus.txt'
MINI = REPO / 'tests' / 'fixtures' / 'mini'

KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY')


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """仓库里的 config.toml；没有任何密钥，.env 不读。"""
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    return load_settings(REPO / 'config.toml', env_file=tmp_path / 'none.env')


@pytest.fixture
def content(tmp_path: Path) -> Path:
    """小世界夹具的一份副本（管线会往里写）。"""
    dst = tmp_path / 'content'
    shutil.copytree(MINI, dst)
    return dst


@pytest.fixture
def build(tmp_path: Path) -> Path:
    return tmp_path / 'build'


@pytest.fixture
def out() -> list[str]:
    return []


@pytest.fixture
def mock_llm(settings: Settings):
    return make_llm(settings, mock=True, run_dir=None)


@pytest.fixture
def kw(content: Path, build: Path, out: list[str]) -> dict:
    """pipeline.arun 的公共参数：合成原文、临时 build/ 与 content/。"""
    return {'corpus': CORPUS, 'build': build, 'content': content, 'out': out.append, 'poll_interval': 0.0}
