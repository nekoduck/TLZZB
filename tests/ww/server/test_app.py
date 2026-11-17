"""Starlette 应用：静态页、健康检查、WebSocket 握手（Origin）、消息校验、新局与重连；演示文件转换。"""

from __future__ import annotations

import importlib.util
import json

import pytest
from pydantic import TypeAdapter
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from werewolf.__main__ import main as cli_main
from werewolf.kernel.types import Role
from werewolf.protocol import ServerMsg
from werewolf.server.app import create_app
from werewolf.server.store import read_jsonl, wire_to_demo

from .helpers import ScriptedClient, play, random_factory

SERVER: TypeAdapter = TypeAdapter(ServerMsg)
ORIGIN = {'origin': 'http://127.0.0.1:8765'}


@pytest.fixture
def app_factory(cfg, tmp_path):
    settings, ww = cfg

    def make(dev: bool = True):
        return create_app(settings, ww, offline=True, dev=dev, speed='instant', runs_dir=tmp_path / 'runs',
                          player_factory=random_factory)
    return make


def recv(ws) -> dict:
    data = ws.receive_json()
    SERVER.validate_python(data)
    return data


def test_static_and_health(app_factory):
    with TestClient(app_factory()) as client:
        r = client.get('/')
        assert r.status_code == 200 and '<html' in r.text.lower()
        assert client.get('/js/main.js').status_code == 200
        h = client.get('/api/health').json()
        assert h['ok'] is True and h['llm']['offline'] is True and h['game'] is False


def test_ws_new_game_and_resume(app_factory, tmp_path):
    app = app_factory()
    with TestClient(app) as client:
        with client.websocket_connect('/ws', headers=ORIGIN) as ws:
            hello = recv(ws)
            assert hello['t'] == 'hello' and hello['resumable'] is False and hello['speed'] == 'instant'
            ws.send_json({'t': 'new_game', 'name': '小明', 'role': 'seer', 'seed': 42})
            first = [recv(ws) for _ in range(3)]
            assert [m['t'] for m in first[:2]] == ['game', 'state']
            assert first[0]['your_role'] == 'seer'
            assert first[2]['t'] == 'event' and first[2]['seq'] == 1
            assert any(p['is_human'] and p['name'] == '小明' for p in first[0]['players'])
            got = list(first)
            while not any(m['t'] == 'request' for m in got):
                got.append(recv(ws))
            req = got[-1]
            seed = app.state.hub.session.seed
            assert seed == 42  # dev 下种子生效
        # 重连：hello 说可以续，resume 重放并以带未答请求的 state 结尾
        with client.websocket_connect('/ws', headers=ORIGIN) as ws:
            hello = recv(ws)
            assert hello['resumable'] is True
            ws.send_json({'t': 'resume'})
            replay = []
            while True:
                m = recv(ws)
                if m['t'] == 'state':
                    break
                replay.append(m)
            assert replay[0]['t'] == 'game' and all(m['replay'] for m in replay)
            assert m['open_request']['id'] == req['id']
            ws.send_json({'t': 'action', 'request_id': req['id'], 'option_id': req['options'][0]['id']})
            while True:
                m = recv(ws)
                if m['t'] == 'accepted':
                    break
            assert m['request_id'] == req['id']
    assert (tmp_path / 'runs').exists() and any((tmp_path / 'runs').iterdir())


def test_ws_seed_ignored_without_dev(app_factory):
    app = app_factory(dev=False)
    with TestClient(app) as client, client.websocket_connect('/ws', headers=ORIGIN) as ws:
        recv(ws)
        ws.send_json({'t': 'new_game', 'seed': 42})
        assert recv(ws)['t'] == 'game'
        assert app.state.hub.session.seed != 42  # 种子随机（碰巧相等的概率是 2^-53）
        assert app.state.hub.session.dev is False


def test_ws_origin_check(app_factory):
    with TestClient(app_factory(dev=False)) as client:
        for headers in ({'origin': 'http://evil.example:8765'}, {'origin': 'http://127.0.0.1:9999'}, {}):
            with pytest.raises(WebSocketDisconnect), client.websocket_connect('/ws', headers=headers) as ws:
                ws.receive_json()
        with client.websocket_connect('/ws', headers={'origin': 'http://localhost:8765'}) as ws:
            assert recv(ws)['t'] == 'hello'
    with TestClient(app_factory(dev=True)) as client, client.websocket_connect('/ws') as ws:
        assert recv(ws)['t'] == 'hello'  # dev 下允许没有 Origin


def test_ws_extra_origins(cfg, tmp_path):
    """经反向代理访问时可以显式放行别的来源（精确匹配）；其余来源照样拒绝。"""
    settings, ww = cfg
    app = create_app(settings, ww, offline=True, dev=False, speed='instant', runs_dir=tmp_path / 'runs',
                     player_factory=random_factory, extra_origins=('https://game.example/',))
    with TestClient(app) as client:
        with client.websocket_connect('/ws', headers={'origin': 'https://game.example'}) as ws:
            assert recv(ws)['t'] == 'hello'
        for origin in ('https://other.example', 'https://game.example.evil.test', '*'):
            with pytest.raises(WebSocketDisconnect), client.websocket_connect('/ws', headers={'origin': origin}) as ws:
                ws.receive_json()


def test_ws_bad_messages(app_factory):
    with TestClient(app_factory()) as client, client.websocket_connect('/ws', headers=ORIGIN) as ws:
        recv(ws)
        for raw in ('not json', '{"t": "bogus"}', '{"t": "new_game", "name": "<script>"}', '[1, 2]'):
            ws.send_text(raw)
            m = recv(ws)
            assert m['t'] == 'error' and m['code'] == 'bad_message'
        ws.send_json({'t': 'resume'})
        m = recv(ws)
        assert m['t'] == 'error' and m['code'] == 'no_game'
        ws.send_json({'t': 'action', 'request_id': 'x', 'option_id': 'y'})
        assert recv(ws)['code'] == 'no_game'


def test_single_live_connection(app_factory):
    with TestClient(app_factory()) as client:
        with client.websocket_connect('/ws', headers=ORIGIN) as ws1:
            recv(ws1)
            with client.websocket_connect('/ws', headers=ORIGIN) as ws2:
                assert recv(ws2)['t'] == 'hello'
                with pytest.raises(WebSocketDisconnect) as exc:
                    ws1.receive_json()
                assert exc.value.code == 4000


async def test_demo_converter(cfg, tmp_path):
    runs = tmp_path / 'runs'
    s, c = await play(cfg, seed=9, role=Role.WITCH, run_dir=runs / 'g9', client=ScriptedClient(9))
    demo = wire_to_demo(read_jsonl(runs / 'g9' / 'wire.jsonl'))
    assert demo['version'] == 1
    for step in demo['steps']:
        SERVER.validate_python(step['msg'])
    assert demo['steps'][0]['msg']['t'] == 'hello'
    reqs = [st for st in demo['steps'] if st['msg']['t'] == 'request']
    assert reqs and all('answer' in st for st in reqs)
    for st in reqs:
        ans = st['answer']
        if st['msg']['kind'] == 'speak':
            assert ans.get('text')
        else:
            assert ans['option_id'] in {o['id'] for o in st['msg']['options']}
    # 命令行
    out = tmp_path / 'demo'
    assert cli_main(['demo', 'g9', 'testdemo', '--runs', str(runs), '--out', str(out)]) == 0
    data = json.loads((out / 'testdemo.json').read_text(encoding='utf-8'))
    assert data == demo
    assert cli_main(['demo', 'nope', 'x', '--runs', str(runs), '--out', str(out)]) == 2
    assert cli_main(['demo', 'g9', '../bad', '--runs', str(runs), '--out', str(out)]) == 2


def test_cli_misc(capsys):
    assert cli_main(['--help']) == 0
    assert cli_main(['frobnicate']) == 2
    if importlib.util.find_spec('werewolf.sim.selfplay') is None:
        assert cli_main(['sim']) == 2
        assert '还没装上' in capsys.readouterr().err


def test_cli_serve_port_in_use_and_missing_keys(cfg, tmp_path, monkeypatch, capsys):
    """端口被占：给出清楚的错误、不说「已启动」、不起服务；没有密钥：说明走规则与模板。"""
    import socket

    import uvicorn

    ran: list[int] = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kw: ran.append(kw['port']))
    conf = str(tmp_path / 'werewolf.toml')
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli_main(['serve', '--port', str(port), '--host', '127.0.0.1', '--no-browser', '--config', conf]) == 2
    out = capsys.readouterr()
    assert '已启动' not in out.out and f'端口 {port}' in out.err and not ran
    assert cli_main(['serve', '--port', str(port), '--host', '127.0.0.1', '--no-browser', '--config', conf]) == 0
    out = capsys.readouterr()
    assert ran == [port] and '已启动' in out.out and '规则与模板' in out.out
