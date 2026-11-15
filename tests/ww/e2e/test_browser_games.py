"""真浏览器整局：Playwright 驱动界面，打完一局，再用 wire_check 体检（离线服务、确定种子）。"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from werewolf.llm.config import load
from werewolf.server.app import create_app

from .wirehelper import check

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
NODE = shutil.which('node')


def _have_playwright() -> bool:
    if not NODE:
        return False
    r = subprocess.run([NODE, '-e', "require('playwright')"], capture_output=True, cwd=HERE)
    return r.returncode == 0


pytestmark = [
    pytest.mark.slow,
    pytest.mark.e2e,
    pytest.mark.skipif(not _have_playwright(), reason='需要 node 与 playwright'),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path):
    import uvicorn

    port = _free_port()
    settings, ww = load(str(REPO / 'werewolf.toml'))
    ww = ww.model_copy(update={'port': port})
    runs = tmp_path / 'runs'
    app = create_app(settings, ww, offline=True, dev=True, speed='instant', runs_dir=runs)
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield port, runs
    srv.should_exit = True
    th.join(timeout=10)


def _play(port: int, runs: Path, tmp_path: Path, role: str, seed: int, speed: str, extra: tuple[str, ...] = ()) -> dict:
    out = tmp_path / f'out-{role}-{seed}'
    cmd = [NODE, str(HERE / 'driver.mjs'), '--url', f'http://127.0.0.1:{port}', '--role', role, '--seed', str(seed),
           '--speed', speed, '--out', str(out), '--timeout', '600', *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE, timeout=700)
    assert (out / 'result.json').exists(), r.stdout[-800:] + r.stderr[-1500:]
    res = json.loads((out / 'result.json').read_text(encoding='utf-8'))
    assert res['role'] == role
    assert res['console_errors'] == [] and res['page_errors'] == [], (res['console_errors'], res['page_errors'])
    assert res['problems'] == [], res['problems']
    assert res['layout'] == [], res['layout'][:5]
    wc = check(runs / res['game_id'], res)
    assert wc['problems'] == [], wc['problems']
    return res


@pytest.mark.parametrize('role,seed', [('werewolf', 21), ('seer', 22), ('witch', 23), ('hunter', 24), ('villager', 25)])
def test_full_game_each_role_instant(server, tmp_path, role, seed):
    port, runs = server
    res = _play(port, runs, tmp_path, role, seed, 'instant')
    assert res['final']['over'] and res['final']['review']


def test_full_game_mobile_fast(server, tmp_path):
    port, runs = server
    res = _play(port, runs, tmp_path, 'seer', 31, 'fast', ('--mobile',))
    assert res['final']['over']
