from __future__ import annotations

import json

import pytest
from llm_fakes import FakeAnthropic, message

from tianlong.config import Settings
from tianlong.llm.claude import FALLBACK_BETA, BatchItem, CallMeta, ClaudeClient, build_params
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import BudgetExceeded, LLMRefusal, LLMUnavailable, RetryableLLMError
from tianlong.llm.record import Recorder
from tianlong.llm.schemas import DEFAULT_SCHEMAS, Decision, Intent, OptionsOut
from tianlong.llm.specs import claude_spec

SAMPLING = {'temperature', 'top_p', 'top_k'}

INTENT = {
    'kind': 'act', 'verb': 'look', 'target': None, 'target2': None, 'act': None, 'to': [], 'asks': None,
    'cls': None, 'urgent': False, 'claim_refs': [], 'attempts': [], 'dropped': '', 'note': '',
}


def make(settings: Settings, fake: FakeAnthropic | None = None) -> tuple[ClaudeClient, FakeAnthropic, Recorder]:
    fake = fake or FakeAnthropic()
    rec = Recorder(None)
    return ClaudeClient(settings, recorder=rec, ledger=Ledger.from_settings(settings), client=fake), fake, rec


def assert_no_sampling(fake: FakeAnthropic) -> None:
    for _, kw in fake.calls:
        assert not SAMPLING & set(kw)
        for r in kw.get('requests', []):
            assert not SAMPLING & set(r['params'])
        assert 'budget_tokens' not in json.dumps(kw, default=str)


async def test_parse_path_reads_text_block_after_thinking(settings: Settings) -> None:
    client, fake, rec = make(settings)
    fake.responses.append(message(json.dumps(INTENT), model='claude-haiku-5-5'))
    obj, meta = await client.call_json('C1', ['规则一', '规则二'], '我四下看看', Intent, mock_default=None)
    assert isinstance(obj, Intent) and obj.verb == 'look'
    assert meta.attempts == 1 and meta.stop_reason == 'end_turn' and meta.cost > 0
    (kind, kw), = fake.calls
    assert kind == 'create'
    assert kw['model'] == 'claude-haiku-5-5'
    assert kw['max_tokens'] == 1500
    assert kw['output_config']['effort'] == 'low'
    fmt = kw['output_config']['format']
    assert fmt['type'] == 'json_schema' and fmt['schema']['additionalProperties'] is False
    assert kw['messages'] == [{'role': 'user', 'content': '我四下看看'}]
    assert 'thinking' not in kw  # C1 保留默认的自适应思考
    assert kw['timeout'] == 15.0
    assert kw['betas'] == [FALLBACK_BETA] and kw['fallbacks'] == 'default'
    assert_no_sampling(fake)
    assert rec.records[0]['kind'] == 'C1' and rec.records[0]['error'] is None


async def test_cache_control_on_system_blocks(settings: Settings) -> None:
    client, fake, _ = make(settings)
    fake.responses.append(message(json.dumps(INTENT)))
    await client.call_json('C1', ['规则', '', {'type': 'text', 'text': '人物卡'}], 'x', Intent)
    system = fake.calls[0][1]['system']
    assert [b['text'] for b in system] == ['规则', '人物卡']  # 空块跳过
    assert all(b['cache_control'] == {'type': 'ephemeral'} for b in system)
    settings.cache.ttl = '1h'
    fake.responses.append(message(json.dumps(INTENT)))
    await client.call_json('C1', ['规则'], 'x', Intent)
    assert fake.calls[1][1]['system'][0]['cache_control'] == {'type': 'ephemeral', 'ttl': '1h'}


def test_at_most_four_cache_breakpoints(settings: Settings) -> None:
    p = build_params(claude_spec(settings, 'C1'), [f'块{i}' for i in range(6)], 'x')
    marks = [('cache_control' in b) for b in p['system']]
    assert marks == [False, False, True, True, True, True]


@pytest.mark.parametrize('kind', ['C1', 'C2_soul', 'C2_named', 'C4', 'C5', 'C6', 'C7', 'P1'])
def test_between_tools_only_for_c3(settings: Settings, kind: str) -> None:
    schema = DEFAULT_SCHEMAS.get(kind)
    p = build_params(claude_spec(settings, kind), ['s'], 'u', schema=schema)
    assert 'thinking' not in p
    assert not SAMPLING & set(p)


def test_c3_between_tools_and_guard(settings: Settings) -> None:
    p = build_params(claude_spec(settings, 'C3'), ['s'], 'u')
    assert p['thinking'] == {'type': 'between_tools'}
    assert 'format' not in p['output_config']
    # 配到不接受它的模型或过高的 effort 上时不发
    settings.models['C3'].model = 'claude-haiku-5-5'
    assert 'thinking' not in build_params(claude_spec(settings, 'C3'), ['s'], 'u')
    settings.models['C3'].model = 'claude-sonnet-5-5'
    settings.models['C3'].effort = 'max'
    assert 'thinking' not in build_params(claude_spec(settings, 'C3'), ['s'], 'u')


async def test_max_tokens_retried_then_ok(settings: Settings) -> None:
    client, fake, rec = make(settings)
    fake.responses += [message('{"kind": "ac', stop_reason='max_tokens'), message(json.dumps(INTENT))]
    obj, meta = await client.call_json('C1', ['s'], 'u', Intent)
    assert obj.kind == 'act' and meta.attempts == 2 and len(fake.calls) == 2
    assert len(rec.records) == 1 and len(rec.records[0]['prior_responses']) == 1


async def test_max_tokens_twice_raises_retryable(settings: Settings) -> None:
    client, fake, rec = make(settings)
    # 即使文本里已有合法 JSON，max_tokens 也算失败
    fake.responses += [message(json.dumps(INTENT), stop_reason='max_tokens')] * 2
    with pytest.raises(RetryableLLMError) as ei:
        await client.call_json('C1', ['s'], 'u', Intent)
    assert ei.value.stop_reason == 'max_tokens'
    assert len(fake.calls) == 2
    assert rec.records[0]['error']['type'] == 'RetryableLLMError'


async def test_invalid_json_retryable(settings: Settings) -> None:
    client, fake, _ = make(settings)
    fake.responses += [message('不是 JSON'), message('{"kind": "fly"}')]
    with pytest.raises(RetryableLLMError):
        await client.call_json('C1', ['s'], 'u', Intent)
    assert len(fake.calls) == 2


async def test_refusal_not_retried(settings: Settings) -> None:
    client, fake, rec = make(settings)
    fake.responses += [message(None, stop_reason='refusal',
                               stop_details={'type': 'refusal', 'category': 'general_harms', 'explanation': 'x'}),
                       message(json.dumps(INTENT))]
    with pytest.raises(LLMRefusal) as ei:
        await client.call_json('C2_named', ['s'], 'u', Intent)
    assert ei.value.category == 'general_harms'
    assert len(fake.calls) == 1
    assert rec.records[0]['error']['category'] == 'general_harms'


async def test_missing_key_unavailable_at_call_time(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    rec = Recorder(None)
    client = ClaudeClient(settings, recorder=rec)  # 构造不报错
    with pytest.raises(LLMUnavailable, match='ANTHROPIC_API_KEY'):
        await client.call_json('C1', ['s'], 'u', Intent)
    with pytest.raises(LLMUnavailable):
        async for _ in client.stream_text('C3', ['s'], 'u'):
            pass
    assert [r['error']['type'] for r in rec.records] == ['LLMUnavailable', 'LLMUnavailable']


async def test_no_server_fallback_uses_plain_endpoint(settings: Settings) -> None:
    settings.claude.server_fallback = False
    client, fake, _ = make(settings)
    fake.responses.append(message(json.dumps(INTENT)))
    await client.call_json('C1', ['s'], 'u', Intent)
    kw = fake.calls[0][1]
    assert 'betas' not in kw and 'fallbacks' not in kw


async def test_budget_blocks_call(settings: Settings) -> None:
    client, fake, _ = make(settings)
    assert client.ledger is not None
    client.ledger.add('C3', 100.0)
    with pytest.raises(BudgetExceeded):
        await client.call_json('C1', ['s'], 'u', Intent)
    assert fake.calls == []


async def test_stream_text(settings: Settings) -> None:
    client, fake, rec = make(settings)
    final = message('你推开门。厅里很静。', model='claude-sonnet-5-5', thinking_first=False,
                    usage={'input_tokens': 3000, 'output_tokens': 40, 'cache_read_input_tokens': 2500,
                           'cache_creation_input_tokens': 0})
    fake.stream_script = (['你推开', '门。', '厅里很静。'], final)
    metas: list[CallMeta] = []
    pieces = [p async for p in client.stream_text('C3', ['规则', '文风'], '发生的事', meta_out=metas)]
    assert pieces == ['你推开', '门。', '厅里很静。']
    kind, kw = fake.calls[0]
    assert kind == 'stream'
    assert kw['thinking'] == {'type': 'between_tools'}
    assert kw['model'] == 'claude-sonnet-5-5' and kw['output_config'] == {'effort': 'medium'}
    assert all('cache_control' in b for b in kw['system'])
    assert_no_sampling(fake)
    m = metas[0]
    assert m.stop_reason == 'end_turn' and m.first_token_ms is not None and m.cost > 0
    assert m.usage['cache_read_input_tokens'] == 2500
    assert rec.records[0]['response']['pieces'] == pieces


async def test_stream_refusal_after_text(settings: Settings) -> None:
    client, fake, rec = make(settings)
    fake.stream_script = (['半句'], message('半句', stop_reason='refusal', thinking_first=False,
                                             stop_details={'category': 'general_harms'}))
    got: list[str] = []
    with pytest.raises(LLMRefusal):
        async for p in client.stream_text('C3', ['s'], 'u'):
            got.append(p)
    assert got == ['半句']
    assert rec.records[0]['error']['type'] == 'LLMRefusal'


async def test_stream_early_close_is_recorded_and_closed(settings: Settings) -> None:
    client, fake, rec = make(settings)
    fake.stream_script = (['一。', '二。', '三。'], message('一。二。三。', thinking_first=False))
    gen = client.stream_text('C3', ['s'], 'u')
    async for _ in gen:
        break
    await gen.aclose()
    assert fake.streams[0].closed
    r = rec.records[0]
    assert r['response']['stopped_early'] is True and r['response']['pieces'] == ['一。']
    assert r['usage']['input_tokens'] == 100  # 从快照里记账


async def test_batch_flow(settings: Settings) -> None:
    client, fake, rec = make(settings)
    good = {'type': 'succeeded', 'message': message(json.dumps({'options': [{'id': 'a', 'text': 't'}]}),
                                                    model='claude-opus-5-5')}
    fake.batch_results = [('b2', {'type': 'errored', 'error': {'type': 'invalid_request'}}), ('b1', good)]
    items = [BatchItem('b1', ['语料'], 'u1', OptionsOut), BatchItem('b2', ['语料'], 'u2', OptionsOut)]
    bid = await client.batch_submit('P1', items, ttl='1h')
    kind, kw = fake.calls[0]
    assert kind == 'batch_create'
    reqs = kw['requests']
    assert [r['custom_id'] for r in reqs] == ['b1', 'b2']
    p = reqs[0]['params']
    assert p['model'] == 'claude-opus-5-5' and p['output_config']['effort'] == 'high'
    assert p['system'][0]['cache_control'] == {'type': 'ephemeral', 'ttl': '1h'}
    assert 'fallbacks' not in p and 'betas' not in kw  # Batch 不能用服务端兜底
    assert_no_sampling(fake)
    st = await client.batch_poll(bid)
    assert st.ended
    res = await client.batch_results(bid, 'P1')
    assert res['b1'].ok and isinstance(res['b1'].obj, OptionsOut)
    assert not res['b2'].ok and 'errored' in (res['b2'].error or '')
    # 半价记账
    u = good['message']['usage']
    full = (u['input_tokens'] * 4.0 + u['output_tokens'] * 20.0) / 1e6
    assert res['b1'].cost == pytest.approx(full / 2)
    assert [r.get('batch') for r in rec.records] == ['submit', 'result', 'result']


def test_all_schemas_transform(settings: Settings) -> None:
    for kind, schema in DEFAULT_SCHEMAS.items():
        spec = claude_spec(settings, kind)
        p = build_params(spec, ['s'], 'u', schema=schema)
        assert p['output_config']['format']['schema']['type'] == 'object'
    d = Decision.model_validate({'inner': 'x', 'action': 'none', 'speech': None, 'display': 'y'})
    assert d.speech is None
