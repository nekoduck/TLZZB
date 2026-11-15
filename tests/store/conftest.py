"""store 的测试夹具：用真内容 content/ 读出的开局状态。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.content.loader import load
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState

ROOT = Path(__file__).resolve().parents[2]
_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def real() -> tuple[Defs, WorldState]:
    if "content" not in _CACHE:
        _CACHE["content"] = load(ROOT / "content")
    defs, state = _CACHE["content"]
    return defs, state.model_copy(deep=True)


@pytest.fixture
def world() -> tuple[Defs, WorldState]:
    return real()
