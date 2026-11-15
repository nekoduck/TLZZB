"""wave 3 的 llm 修补：L1 限时的 jev.ask、L2 路由键、L3 重放按请求对号、L4 CardChoice 的字段。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tianlong.config import Settings, load_settings
from tianlong.llm import JevTimeout, make_llm
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import JevError, LLMError, ReplayMismatch
from tianlong.llm.jev import JevClient
from tianlong.llm.record import Recorder, Replayer, error_from_payload, error_payload, read_calls
from tianlong.llm.route import CATEGORIES, mode, uses_model
from tianlong.llm.schemas import CardChoice, CardsOut, CharacterCardDraft

Q: dict[str, Any] = {
    'ok': {'type': 'noul', 'instructions': '这句话有没有写多了？'},
}


def jev_response(noul: float = 0.25) -> dict[str, Any]:
    return {'model': 'jev-1.14.0', 'answers': {'ok': {'type': 'noul', 'noul': noul}},
            'usage': {'input_tokens': 10, 'output_tokens': 0}}


def slow_client(settings: Settings, path: Path | None, delay: float = 5.0) -> tuple[JevClient, Recorder, list[int]]:
    hits: list[int] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        hits.append(1)
        state = json.loads(req.content)['state']
        if 'slow' in state:
            await asyncio.sleep(delay)
        return httpx.Response(200, json=jev_response())

    rec = Recorder(path, keep_in_memory=True)
    client = JevClient(settings, recorder=rec, ledger=Ledger.from_settings(settings),
                       transport=httpx.MockTransport(handler), backoff_s=0.0)
    return client, rec, hits


# ---------------------------------------------------------------- L1


async def test_ask_timeout_raises_and_records_an_error_line(settings: Settings) -> None:
    client, rec, hits = slow_client(settings, None)
    with pytest.raises(JevTimeout) as ei:
        await client.ask('J5', 'slow 句子', Q, timeout_s=0.05)
    assert isinstance(ei.value, JevError) and isinstance(ei.value, LLMError)
    assert ei.value.timeout_s == 0.05 and '0.05s' in str(ei.value)
    assert hits == [1] and rec.count == 1
    line = rec.records[0]
    assert line['kind'] == 'J5' and line['response'] is None and line['attempts'] == 0
    assert line['error'] == {'type': 'JevTimeout', 'message': str(ei.value), 'timeout_s': 0.05}
    assert line['request']['state'] == 'slow 句子' and line['cost'] == 0.0
    # 不超时的照常；不给 timeout_s 时不限时
    a = await client.ask('J5', '快的句子', Q, timeout_s=2.0)
    assert a['ok'].noul == 0.25 and rec.records[1]['error'] is None
    await client.aclose()


async def test_cancelled_ask_is_recorded_too(settings: Settings) -> None:
    """调用方自己套 wait_for 取消的那一次也记一行，重放才不会错位。"""
    client, rec, _ = slow_client(settings, None)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client.ask('J5', 'slow 句子', Q), 0.05)
    assert rec.count == 1 and rec.records[0]['error']['type'] == 'JevTimeout'
    assert 'cancelled' in rec.records[0]['error']['message']
    await client.aclose()


async def test_timeout_replays_as_the_same_error(settings: Settings, tmp_path: Path) -> None:
    run1 = tmp_path / 'run1'
    client, rec, _ = slow_client(settings, run1 / 'calls.jsonl')
    out1: list[Any] = []
    for state in ('第一句', 'slow 第二句', '第三句'):
        try:
            out1.append((await client.ask('J5', state, Q, timeout_s=0.05))['ok'].noul)
        except JevTimeout:
            out1.append('timeout')
    await client.aclose()
    assert out1 == [0.25, 'timeout', 0.25]
    assert [c['error'] is not None for c in read_calls(run1 / 'calls.jsonl')] == [False, True, False]

    rep = make_llm(settings, mock=False, run_dir=tmp_path / 'run2', replay_dir=run1)
    out2: list[Any] = []
    for state in ('第一句', 'slow 第二句', '第三句'):
        try:
            out2.append((await rep.jev.ask('J5', state, Q, timeout_s=0.05))['ok'].noul)
        except JevTimeout as e:
            assert e.timeout_s == 0.05
            out2.append('timeout')
    assert out2 == out1
    assert rep.jev.replayer.remaining() == {}  # type: ignore[attr-defined]
    again = read_calls(tmp_path / 'run2' / 'calls.jsonl')
    assert [c['error']['type'] if c['error'] else None for c in again] == [None, 'JevTimeout', None]


async def test_mock_jev_accepts_timeout_and_never_times_out(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    a = await llm.jev.ask('J5', '句子', Q, hints={'ok': 0.9}, timeout_s=0.0)
    assert a['ok'].noul == 0.9
    assert llm.recorder.records[0]['error'] is None


def test_jev_timeout_payload_round_trip() -> None:
    err = JevTimeout('Jev J5: no answer within 0.4s', timeout_s=0.4)
    back = error_from_payload(error_payload(err))
    assert isinstance(back, JevTimeout) and back.timeout_s == 0.4 and str(back) == str(err)
    assert back.status is None


# ---------------------------------------------------------------- L2


def test_route_has_narrate_phrase_pipeline(settings: Settings, tmp_path: Path) -> None:
    assert {'narrate', 'phrase', 'pipeline'} <= set(CATEGORIES)
    assert CATEGORIES['narrate'] == (None, 'C3') and CATEGORIES['phrase'] == (None, 'C4')
    assert CATEGORIES['pipeline'] == (None, 'pipeline')
    # 仓库里的 config.toml 写明了这三个键
    assert (mode(settings, 'narrate'), mode(settings, 'phrase'), mode(settings, 'pipeline')) == (
        'claude_first', 'claude_first', 'claude_only')
    assert all(k in settings.route.model_fields_set for k in ('narrate', 'phrase', 'pipeline'))
    assert uses_model(settings, 'narrate') and uses_model(settings, 'pipeline')
    # 配置里没写时的默认值相同；写成 rules_only 即不调模型；写错了报错
    bare = load_settings(tmp_path / 'none.toml', env_file=tmp_path / 'none.env')
    assert (bare.route.narrate, bare.route.phrase, bare.route.pipeline) == (
        'claude_first', 'claude_first', 'claude_only')
    cfg = tmp_path / 'config.toml'
    cfg.write_text("[route]\nnarrate = 'rules_only'\nphrase = 'rules_only'\n", encoding='utf-8')
    off = load_settings(cfg, env_file=tmp_path / 'none.env')
    assert mode(off, 'narrate') == 'rules_only' and not uses_model(off, 'phrase')
    assert mode(off, 'pipeline') == 'claude_only'
    cfg.write_text("[route]\nnarrate = 'sometimes'\n", encoding='utf-8')
    with pytest.raises(ValueError):
        load_settings(cfg, env_file=tmp_path / 'none.env')
    for cat in CATEGORIES:
        assert mode(settings, cat) in ('jev_first', 'claude_first', 'claude_only', 'rules_only')


# ---------------------------------------------------------------- L3


def record_three(path: Path) -> None:
    rec = Recorder(path)
    for i, who in enumerate(('甲', '乙', '丙')):
        rec.record(kind='J2', provider='jev', model='m', request={'state': f'你是{who}', 'questions': Q},
                   response={'answers': {'ok': {'type': 'noul', 'noul': i / 10}}})
    rec.record(kind='J2', provider='jev', model='m', request={'state': '你是甲', 'questions': Q},
               response={'answers': {'ok': {'type': 'noul', 'noul': 0.9}}})
    rec.record(kind='J5', provider='jev', model='m', request={'state': '一句', 'questions': Q},
               response={'answers': {'ok': {'type': 'noul', 'noul': 0.5}}})


def noul(rec: dict[str, Any]) -> float:
    return rec['response']['answers']['ok']['noul']


def test_replayer_matches_by_request_within_a_kind(tmp_path: Path) -> None:
    path = tmp_path / 'calls.jsonl'
    record_three(path)
    rp = Replayer(path)
    assert rp.remaining() == {'J2': 4, 'J5': 1}
    # 与录制的先后不同（gather 里完成的先后变了）：各自拿到自己那一条
    assert noul(rp.next('J2', {'state': '你是丙', 'questions': Q})) == 0.2
    assert noul(rp.next('J2', {'state': '你是甲', 'questions': Q})) == 0.0  # 一模一样的请求：按录制的先后
    assert noul(rp.next('J2', {'questions': Q, 'state': '你是甲'})) == 0.9  # 键的顺序无关
    assert rp.remaining() == {'J2': 1, 'J5': 1}
    # 对不上任何一条没用过的：严格模式报错，指出按先后本该是哪一条；这条录制不算用掉
    with pytest.raises(ReplayMismatch) as ei:
        rp.next('J2', {'state': '你是丁', 'questions': Q})
    assert 'seq=2' in str(ei.value) and '.state' in str(ei.value)
    assert noul(rp.next('J2', {'state': '你是乙', 'questions': Q})) == 0.1
    with pytest.raises(ReplayMismatch, match='no more recorded calls'):
        rp.next('J2', {'state': '你是甲', 'questions': Q})
    with pytest.raises(ReplayMismatch, match='no more recorded calls'):
        rp.next('C1', {})
    assert rp.remaining() == {'J5': 1}


def test_replayer_falls_back_to_order_when_not_strict(tmp_path: Path) -> None:
    path = tmp_path / 'calls.jsonl'
    record_three(path)
    rp = Replayer(path, strict=False)
    assert noul(rp.next('J2', {'state': '改过的提示词', 'questions': Q})) == 0.0  # 对不上：按先后取下一条
    assert noul(rp.next('J2', {'state': '你是丙', 'questions': Q})) == 0.2  # 对得上的仍然对号
    assert noul(rp.next('J2', {'state': '又改了', 'questions': Q})) == 0.1
    assert noul(rp.next('J2', {'state': '你是甲', 'questions': Q})) == 0.9
    assert rp.remaining() == {'J5': 1}


async def test_concurrent_calls_replay_in_any_order(settings: Settings, tmp_path: Path) -> None:
    """录制时并发的同类调用，重放时换个顺序发出去，各人拿回的还是自己的答案。"""
    run1 = tmp_path / 'run1'
    llm = make_llm(settings, mock=True, run_dir=run1)
    names = ['甲', '乙', '丙', '丁']

    async def ask(client: Any, who: str, i: int) -> float:
        a = await client.ask('J2', f'【你是谁】{who}', Q, hints={'ok': i / 10})
        return a['ok'].noul

    first = await asyncio.gather(*(ask(llm.jev, n, i) for i, n in enumerate(names)))
    rep = make_llm(settings, mock=False, run_dir=None, replay_dir=run1)  # 严格模式
    order = [2, 0, 3, 1]
    second = await asyncio.gather(*(ask(rep.jev, names[i], 9) for i in order))
    assert second == [first[i] for i in order]
    assert rep.jev.replayer.remaining() == {}  # type: ignore[attr-defined]


# ---------------------------------------------------------------- L4


def test_card_choice_has_situation_did_para() -> None:
    c = CardChoice(situation='有人逼他认错', did='宁可挨打也不改口', para='ch01_p0031')
    assert (c.situation, c.did, c.para) == ('有人逼他认错', '宁可挨打也不改口', 'ch01_p0031')
    assert c.text == '有人逼他认错：宁可挨打也不改口'
    assert set(CardChoice.model_json_schema()['properties']) == {'situation', 'did', 'para'}
    assert set(CardChoice.model_json_schema()['required']) == {'situation', 'did', 'para'}
    assert CardChoice.model_validate({'situation': 's', 'did': 'd', 'para': None}).para is None
    # 旧写法只有一句 text
    old = CardChoice.model_validate({'text': '宁可挨打也不改口', 'para': 'ch01_p0031'})
    assert (old.situation, old.did, old.para, old.text) == ('', '宁可挨打也不改口', 'ch01_p0031', '宁可挨打也不改口')
    draft = CharacterCardDraft(id='x', card_yaml='id: x', voice_samples=[],
                               choices=[{'situation': 's', 'did': 'd', 'para': 'p1'}])  # type: ignore[list-item]
    out = CardsOut(cards=[draft])
    assert out.cards[0].choices[0].did == 'd'
    assert json.loads(out.model_dump_json())['cards'][0]['choices'] == [{'situation': 's', 'did': 'd', 'para': 'p1'}]


def test_pipeline_subclass_still_works() -> None:
    from tianlong.pipeline.schemas import CardDraft, CardsDraft, ChoiceDraft

    d = CardDraft(id='x', card_yaml='id: x', voice_samples=[], notes='',
                  choices=[ChoiceDraft(situation='s', did='d', para=None)])
    assert CardsDraft(cards=[d]).cards[0].choices[0].situation == 's'
    assert set(ChoiceDraft.model_fields) == set(CardChoice.model_fields)
