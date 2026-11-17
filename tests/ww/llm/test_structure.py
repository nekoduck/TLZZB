"""结构性防漏：speak/check/parse 不得引用私有族类型（ARCHITECTURE 2）。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import werewolf.llm as llm_pkg

FORBIDDEN = {'View', 'MindSnapshot', 'SpeechPlan', 'PlannedAct', 'Decision'}
PKG = Path(llm_pkg.__file__).parent


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split('.')[-1])
            if node.asname:
                found.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(w for w in FORBIDDEN if w in node.value.split())  # 字符串形式的类型注解
    return found


@pytest.mark.parametrize('name', ['speak.py', 'check.py', 'parse.py'])
def test_no_private_types(name: str) -> None:
    assert not (_names(PKG / name) & FORBIDDEN)


def test_no_private_types_even_in_text() -> None:
    for name in ('speak.py', 'check.py', 'parse.py'):
        src = (PKG / name).read_text(encoding='utf-8')
        for w in FORBIDDEN:
            assert f'{w}' not in src.replace('PublicView', '').replace('PublicPlan', ''), (name, w)


def test_speak_does_not_import_voice_modules_at_top_level() -> None:
    tree = ast.parse((PKG / 'speak.py').read_text(encoding='utf-8'))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    mods = {getattr(n, 'module', None) or '' for n in top}
    assert not {m for m in mods if m.startswith('werewolf.voice.') and m != 'werewolf.voice'} - {'werewolf.voice'}
    assert 'werewolf.voice.compose' not in mods and 'werewolf.voice.digest' not in mods
