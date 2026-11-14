from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings
from tianlong.llm import make_llm
from tianlong.llm.errors import JevError, LLMRefusal, ReplayMismatch
from tianlong.llm.mock import STAR_PREFIX
from tianlong.llm.record import Recorder, Replayer, read_calls
from tianlong.llm.schemas import Decision

Q: dict[str, Any] = {
    'action': {'type': 'choice', 'instructions': '做什么？',
               'criteria': {'a01': '走', 'a02': STAR_PREFIX + '留', 'none': '什么也不做'}},
    'needs_thought': {'type': 'noul', 'instructions': '拿不定主意。'},
}
DECISION = Decision(inner='留下看看', action='a02', speech=None, display='沉吟')


async def play(llm: Any) -> list[Any]:
    """一小段确定的调用序列：Jev、Claude JSON、Claude 流式。"""
    out: list[Any] = []
    a = await llm.jev.ask('J2', '【你是谁】某人', Q, hints={'needs_thought': 0.2})
    out.append(dict(a['action']))
    obj, meta = await llm.claude.call_json('C2_soul', ['共用规则', '人物卡'], '处境', Decision,
                                           mock_default=DECISION)
    out.append(obj.model_dump())
    llm.recorder.annotate(meta.seq, escalated=True)
    pieces = [p async for p in llm.claude.stream_text('C3', ['叙述规则'], '发生的事', mock_text='一句。两句。')]
    out.append(pieces)
    a2 = await llm.jev.ask('J2', '【你是谁】某人', Q)
    out.append(dict(a2['action']))
    return out


async def test_record_then_replay_round_trip(settings: Settings, tmp_path: Path) -> None:
    run1 = tmp_path / 'run1'
    llm = make_llm(settings, mock=True, run_dir=run1)
    first = await play(llm)
    calls = read_calls(run1 / 'calls.jsonl')
    assert [c['kind'] for c in calls] == ['J2', 'C2_soul', 'C3', 'J2']
    assert [c['seq'] for c in calls] == [1, 2, 3, 4]
    assert calls[1]['escalated'] is True  # 补记行已合并
    for c in calls:
        assert {'kind', 'provider', 'model', 'request', 'response', 'usage', 'latency_ms', 'cost', 'escalated',
                'violations', 'seq'} <= set(c)

    run2 = tmp_path / 'run2'
    rep = make_llm(settings, mock=False, run_dir=run2, replay_dir=run1)
    second = await play(rep)
    assert second == first
    assert rep.replay and rep.jev_available
    calls2 = read_calls(run2 / 'calls.jsonl')
    assert [c['provider'] for c in calls2] == ['replay-jev', 'replay-claude', 'replay-claude', 'replay-jev']
    assert [c['request_fp'] for c in calls2] == [c['request_fp'] for c in calls]


async def test_replay_mismatch_is_clear(settings: Settings, tmp_path: Path) -> None:
    run1 = tmp_path / 'run1'
    await play(make_llm(settings, mock=True, run_dir=run1))
    rep = make_llm(settings, mock=False, run_dir=None, replay_dir=run1)
    with pytest.raises(ReplayMismatch) as ei:
        await rep.jev.ask('J2', '【你是谁】另一个人', Q)
    msg = str(ei.value)
    assert "'J2'" in msg and 'seq=1' in msg and '.state' in msg

    rep2 = make_llm(settings, mock=False, run_dir=None, replay_dir=run1)
    with pytest.raises(ReplayMismatch) as ei2:
        await rep2.jev.ask('J4', 's', Q)
    assert 'no more recorded calls' in str(ei2.value)


async def test_replay_reraises_recorded_errors(settings: Settings, tmp_path: Path) -> None:
    run1 = tmp_path / 'run1'
    llm = make_llm(settings, mock=True, run_dir=run1)
    req = llm.jev.build_request(llm.jev.spec('J2'), 's', Q)
    llm.recorder.record(kind='J2', provider='jev', model='jev-1.13.0', request=req,
                        error=JevError('Jev request failed after retry: timeout'))
    llm.recorder.record(kind='C2_named', provider='claude', model='claude-haiku-5-5',
                        request={}, error=LLMRefusal('general_harms'))
    rep = make_llm(settings, mock=False, run_dir=None, replay_dir=run1, strict_replay=False)
    with pytest.raises(JevError, match='timeout'):
        await rep.jev.ask('J2', 's', Q)
    with pytest.raises(LLMRefusal) as ei:
        await rep.claude.call_json('C2_named', ['x'], 'y', Decision)
    assert ei.value.category == 'general_harms'


def test_recorder_lines_and_annotate(tmp_path: Path) -> None:
    path = tmp_path / 'r' / 'calls.jsonl'
    rec = Recorder(path)
    s1 = rec.record(kind='J5', provider='jev', model='m', request={'a': 1}, response={'b': 2}, cost=1e-6)
    rec.annotate(s1, violations=['mind'])
    lines = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
    assert lines[0]['seq'] == 1 and lines[1] == {'note_for': 1, 'violations': ['mind']}
    merged = read_calls(path)
    assert len(merged) == 1 and merged[0]['violations'] == ['mind']


def test_replayer_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ReplayMismatch):
        Replayer(tmp_path / 'nope')
