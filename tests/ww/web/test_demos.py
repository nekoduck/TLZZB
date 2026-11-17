"""演示文件：每条消息都能被 TypeAdapter(ServerMsg) 读回，作答合乎请求的选项，编号连贯；生成脚本可复现。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from werewolf.protocol import RequestMsg, ServerMsg

ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / 'src' / 'werewolf' / 'web' / 'demo'
NAMES = ['villager', 'wolf', 'seer', 'witch', 'hunter', 'resume']
ADAPTER = TypeAdapter(ServerMsg)


def _load(name: str) -> list[dict]:
    data = json.loads((DEMO_DIR / f'{name}.json').read_text(encoding='utf-8'))
    assert data['version'] == 1
    return data['steps']


def test_all_required_demos_exist() -> None:
    for name in NAMES:
        assert (DEMO_DIR / f'{name}.json').is_file(), name


@pytest.mark.parametrize('name', NAMES)
def test_every_message_validates(name: str) -> None:
    steps = _load(name)
    assert steps
    for i, st in enumerate(steps):
        assert set(st) <= {'msg', 'answer'}, (name, i)
        ADAPTER.validate_python(st['msg'])


@pytest.mark.parametrize('name', NAMES)
def test_answers_match_open_request(name: str) -> None:
    steps = _load(name)
    n_answers = 0
    for i, st in enumerate(steps):
        m = ADAPTER.validate_python(st['msg'])
        req = m if isinstance(m, RequestMsg) else getattr(m, 'open_request', None)
        ans = st.get('answer')
        if req is not None:
            assert ans is not None, f'{name}[{i}]: request without answer'
        if ans is None:
            continue
        assert req is not None, f'{name}[{i}]: answer on a non-request'
        n_answers += 1
        if req.kind == 'speak':
            assert ans.get('text'), (name, i)
        else:
            assert ans['option_id'] in {o.id for o in req.options}, (name, i, ans)
    assert n_answers


@pytest.mark.parametrize('name', NAMES)
def test_seq_continuous_and_refs(name: str) -> None:
    steps = _load(name)
    last = 0
    open_req = None
    for st in steps:
        m = st['msg']
        if m['t'] == 'event':
            assert m['seq'] == last + 1 and m['ev']['id'] == m['seq']
            last = m['seq']
        elif m['t'] == 'reaction':
            assert m['seq'] == last, 'reaction must refer to the event on stage'
        elif m['t'] == 'parsed' and not m['preview']:
            assert m['seq'] <= last
        elif m['t'] == 'request':
            assert open_req is None, 'at most one open request'
            open_req = m['id']
        elif m['t'] == 'accepted':
            assert m['request_id'] == open_req
            open_req = None
        elif m['t'] == 'state' and m.get('open_request'):
            assert open_req is None
            open_req = m['open_request']['id']


def test_other_players_never_expose_hidden_info_in_villager_demo() -> None:
    """人类是平民：游戏进行中任何消息都不含别人的身份（终局的 game_over 与 review 除外）。"""
    steps = _load('villager')
    over = next(i for i, s in enumerate(steps) if s['msg']['t'] == 'event' and s['msg']['ev']['type'] == 'game_over')
    for st in steps[:over]:
        m = st['msg']
        if m['t'] == 'event':
            assert m['ev']['type'] not in ('wolf_kill', 'wolf_propose', 'death_cause', 'seer_result', 'witch_notice')
            if m['ev']['type'] == 'role_dealt':
                assert m['ev']['actor'] == 4


def test_resume_shape() -> None:
    steps = _load('resume')
    msgs = [s['msg'] for s in steps]
    assert msgs[0]['t'] == 'hello' and msgs[0]['resumable'] is True
    idx = next(i for i, m in enumerate(msgs) if m['t'] == 'state')
    assert msgs[idx]['open_request']['kind'] == 'vote'
    assert all(m.get('replay') for m in msgs[:idx] if m['t'] in ('game', 'event', 'narration', 'private', 'parsed'))
    assert not any(m['t'] in ('thinking', 'reaction', 'cost', 'accepted') for m in msgs[:idx])
    assert any(m['t'] == 'event' and not m['replay'] for m in msgs[idx + 1:])


def test_roles_exercise_panels() -> None:
    kinds = {n: {s['msg']['kind'] for s in _load(n) if s['msg']['t'] == 'request'} for n in NAMES}
    assert {'speak', 'vote', 'hunter_shoot'} <= kinds['hunter']
    assert 'wolf_kill' in kinds['wolf'] and 'seer_check' in kinds['seer'] and 'witch_act' in kinds['witch']
    assert any(s['msg']['t'] == 'god' for s in _load('hunter'))
    witch = [s['msg'] for s in _load('witch') if s['msg']['t'] == 'request' and s['msg']['kind'] == 'witch_act']
    assert len(witch) == 2 and witch[0]['context']['antidote'] and not witch[1]['context']['antidote']
    vil = _load('villager')
    assert any(s['msg']['t'] == 'parsed' and s['msg']['preview'] and s['msg']['ignored'] for s in vil)
    assert any(s['msg']['t'] == 'review' for s in vil)


def test_builder_is_reproducible(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location('ww_build_demos', ROOT / 'scripts' / 'ww_build_demos.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUT = tmp_path
    assert mod.main([]) == 0
    for name in NAMES:
        assert (tmp_path / f'{name}.json').read_text(encoding='utf-8') == (DEMO_DIR / f'{name}.json').read_text(encoding='utf-8'), name
