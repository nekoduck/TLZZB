"""前端行为体检（需要 node + playwright；没有就跳过）：演示页上用真浏览器量几个布局与交互事实。"""

from __future__ import annotations

import functools
import http.server
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[3] / 'src' / 'werewolf' / 'web'
PROBE = Path(__file__).with_name('ui_probe.mjs')


@pytest.fixture(scope='module')
def base():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    handler = functools.partial(Quiet, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


def _run(base: str, w: int, h: int) -> dict:
    if not shutil.which('node'):
        pytest.skip('没有 node')
    p = subprocess.run(['node', str(PROBE), base, str(w), str(h)], capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        if 'Cannot find module' in p.stderr or 'Executable doesn' in p.stderr:
            pytest.skip('没有 playwright')
        raise AssertionError(p.stderr[-800:])
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('size', [(1280, 800), (390, 844)])
def test_bubbles_dialogs_log(base, size):
    r = _run(base, *size)
    assert r['bubbleOverlap'] and not any(r['bubbleOverlap']), '反应气泡不应盖住中央面板'
    assert (r['stacked'], r['afterOneEsc'], r['afterTwoEsc']) == (2, 1, 0), 'Esc 一次只关最上层对话框'
    assert r['focusBack'] == '规则', '关闭对话框后焦点应回到触发按钮'
    assert r['logOpen'] and r['logClosed'], 'Esc 应收起发言记录抽屉'
    assert r['godToasts'] == [0, 1, '3'], '重放的 god 只静默还原身份，现场的 god 才提示'
