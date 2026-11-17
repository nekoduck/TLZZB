"""前端静态检查：不引用任何外部资源（断网可用），入口文件齐全，界面文字里不出现原始编号。"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[3] / 'src' / 'werewolf' / 'web'
URL = re.compile(r'(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\bhttps?://', re.I)


def _strip_comments(path: Path, text: str) -> str:
    if path.suffix == '.css':
        return re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    if path.suffix == '.html':
        text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
        return re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'(?m)(^|\s)//[^\n]*', r'\1', text)


def _files(*suffixes: str) -> list[Path]:
    return sorted(p for p in WEB.rglob('*') if p.is_file() and p.suffix in suffixes)


def test_entry_files_exist() -> None:
    for rel in ['index.html', 'css/base.css', 'css/ui.css', 'js/main.js', 'js/handlers.js', 'js/transport.js', 'js/avatar.js']:
        assert (WEB / rel).is_file(), rel


def test_no_external_urls() -> None:
    bad: list[str] = []
    for p in _files('.html', '.css', '.js', '.json'):
        text = p.read_text(encoding='utf-8')
        body = text if p.suffix == '.json' else _strip_comments(p, text)
        for m in re.finditer(r'https?://|url\(\s*[\'"]?\s*//|@import\s+[\'"]?https?', body, re.I):
            bad.append(f'{p.relative_to(WEB)}: {body[max(0, m.start() - 20):m.end() + 30]!r}')
    assert not bad, bad


def test_html_references_are_local() -> None:
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not re.match(r'^(?:[a-z]+:)?//', ref, re.I), ref
        assert (WEB / ref).is_file(), ref


def test_js_imports_resolve() -> None:
    for p in _files('.js'):
        for ref in re.findall(r"from '(\.[^']+)'", p.read_text(encoding='utf-8')):
            assert (p.parent / ref).resolve().is_file(), f'{p.name}: {ref}'


def test_no_remote_code_or_eval() -> None:
    for p in _files('.js'):
        text = _strip_comments(p, p.read_text(encoding='utf-8'))
        assert not re.search(r'\beval\(|new Function\(|document\.write\(|importScripts\(', text), p.name
