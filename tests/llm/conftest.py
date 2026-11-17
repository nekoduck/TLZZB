from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings, load_settings

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / 'tests' / 'fixtures' / 'jev'

KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY')


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """仓库里的 config.toml；密钥换成假的，.env 不读。"""
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('JEV_KEY', 'test-jev-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-anthropic-key')
    return load_settings(REPO / 'config.toml', env_file=tmp_path / 'none.env')


@pytest.fixture
def smoke_request() -> dict[str, Any]:
    return json.loads((FIXTURES / 'smoke_request.json').read_text(encoding='utf-8'))


@pytest.fixture
def smoke_response() -> dict[str, Any]:
    return json.loads((FIXTURES / 'smoke_jev-1.13.0.json').read_text(encoding='utf-8'))
