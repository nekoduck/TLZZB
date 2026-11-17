"""用真的 anthropic SDK，只把 HTTP 换成 httpx2.MockTransport：核对线上的请求体与头。"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from tianlong.config import Settings
from tianlong.llm.claude import CallMeta, ClaudeClient
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import ClaudeError
from tianlong.llm.record import Recorder
from tianlong.llm.schemas import Intent

INTENT = {
    'kind': 'say', 'verb': 'say', 'target': None, 'target2': None, 'act': 'ask', 'to': ['duan_yu'],
    'asks': None, 'cls': None, 'urgent': False, 'claim_refs': [], 'attempts': [], 'dropped': '', 'note': '',
}


def sdk(handler: Any, max_retries: int = 0) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key='test-key', max_retries=max_retries,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def json_message(text: str, model: str = 'claude-haiku-5-5') -> dict[str, Any]:
    return {
        'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': model,
        'content': [{'type': 'thinking', 'thinking': '', 'signature': 's'}, {'type': 'text', 'text': text}],
        'stop_reason': 'end_turn', 'stop_sequence': None,
        'usage': {'input_tokens': 900, 'output_tokens': 50, 'cache_read_input_tokens': 800,
                  'cache_creation_input_tokens': 0},
    }


def sse(events: list[dict[str, Any]]) -> bytes:
    return ''.join(f'event: {e["type"]}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n' for e in events).encode()


async def test_call_json_wire_format(settings: Settings) -> None:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(200, json=json_message(json.dumps(INTENT, ensure_ascii=False)))

    rec = Recorder(None)
    client = ClaudeClient(settings, recorder=rec, client=sdk(handler))
    obj, meta = await client.call_json('C1', ['通用规则', '场景'], '段公子，往哪走？', Intent)
    assert obj.act == 'ask' and meta.usage['cache_read_input_tokens'] == 800
    req = seen[0]
    assert req.url.path == '/v1/messages'
    assert req.headers['anthropic-version'] == '2026-10-21'
    assert 'server-side-fallback-2026-07-01' in req.headers['anthropic-beta']
    body = json.loads(req.content)
    assert not {'temperature', 'top_p', 'top_k'} & set(body)
    assert body['fallbacks'] == 'default'
    assert body['output_config']['effort'] == 'low'
    assert body['output_config']['format']['type'] == 'json_schema'
    assert 'thinking' not in body
    assert [b['cache_control'] for b in body['system']] == [{'type': 'ephemeral'}] * 2
    assert 'test-key' not in json.dumps(rec.records)


async def test_stream_wire_format(settings: Settings) -> None:
    seen: list[httpx2.Request] = []
    events = [
        {'type': 'message_start', 'message': {
            'id': 'msg_2', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-5-5', 'content': [],
            'stop_reason': None, 'stop_sequence': None,
            'usage': {'input_tokens': 2000, 'output_tokens': 1, 'cache_read_input_tokens': 1500,
                      'cache_creation_input_tokens': 0}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '你推开门。'}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '厅里很静。'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
         'usage': {'output_tokens': 30}},
        {'type': 'message_stop'},
    ]

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(200, content=sse(events), headers={'content-type': 'text/event-stream'})

    client = ClaudeClient(settings, recorder=Recorder(None), ledger=Ledger.from_settings(settings),
                          client=sdk(handler))
    metas: list[CallMeta] = []
    pieces = [p async for p in client.stream_text('C3', ['叙述规则', '文风'], '发生的事', meta_out=metas)]
    assert ''.join(pieces) == '你推开门。厅里很静。'
    body = json.loads(seen[0].content)
    assert body['stream'] is True
    assert body['thinking'] == {'type': 'between_tools'}
    assert body['model'] == 'claude-sonnet-5-5'
    assert not {'temperature', 'top_p', 'top_k'} & set(body)
    m = metas[0]
    assert m.stop_reason == 'end_turn' and m.usage['output_tokens'] == 30
    assert m.cost == pytest.approx((2000 * 2.0 + 30 * 10.0 + 1500 * 0.10) / 1e6)


async def test_http_error_maps_to_claude_error(settings: Settings) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={'type': 'error', 'error': {'type': 'invalid_request_error',
                                                                      'message': 'temperature is not supported'}})

    client = ClaudeClient(settings, recorder=Recorder(None), client=sdk(handler))
    with pytest.raises(ClaudeError) as ei:
        await client.call_json('C1', ['s'], 'u', Intent)
    assert ei.value.status == 400


class _BrokenBody(httpx2.AsyncByteStream):
    """先吐出若干 SSE 字节，再在读正文的半路抛出传输层异常。"""

    def __init__(self, head: bytes, exc: Exception) -> None:
        self.head = head
        self.exc = exc

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.head
        raise self.exc

    async def aclose(self) -> None:
        return None


_STREAM_HEAD = [
    {'type': 'message_start', 'message': {
        'id': 'msg_3', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-5-5', 'content': [],
        'stop_reason': None, 'stop_sequence': None,
        'usage': {'input_tokens': 2000, 'output_tokens': 1, 'cache_read_input_tokens': 0,
                  'cache_creation_input_tokens': 0}}},
    {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
    {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '你推开门。'}},
]


@pytest.mark.parametrize(
    'exc',
    [httpx2.ReadTimeout('read timed out'), httpx2.RemoteProtocolError('peer closed'), httpx2.ReadError('reset')],
    ids=['read_timeout', 'remote_protocol', 'read_error'],
)
async def test_stream_transport_error_mid_body_is_claude_error(settings: Settings, exc: Exception) -> None:
    """SDK 只包装发请求那一步；读 SSE 正文时断了线、超了时，也要落成 ClaudeError（4.3：模型失败不打断游戏）。"""

    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=_BrokenBody(sse(_STREAM_HEAD), exc),
                               headers={'content-type': 'text/event-stream'})

    rec = Recorder(None)
    client = ClaudeClient(settings, recorder=rec, ledger=Ledger.from_settings(settings), client=sdk(handler))
    metas: list[CallMeta] = []
    pieces: list[str] = []
    with pytest.raises(ClaudeError) as ei:
        async for p in client.stream_text('C3', ['叙述规则'], '发生的事', meta_out=metas):
            pieces.append(p)
    assert pieces == ['你推开门。']  # 已经写出来的照样交给调用方
    assert type(exc).__name__ in str(ei.value)
    line = rec.records[-1]
    assert line['error'] is not None and line['error']['type'] == 'ClaudeError'
    assert line['response']['pieces'] == ['你推开门。']
    assert metas[0].usage.get('input_tokens') == 2000  # 半路的用量照样记账


async def test_stream_early_close_is_not_an_error(settings: Settings) -> None:
    """调用方中途停下（GeneratorExit）不走这条兜底：照旧记 stopped_early、没有 error。"""
    from contextlib import aclosing

    full = _STREAM_HEAD + [
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '厅里很静。'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
         'usage': {'output_tokens': 30}},
        {'type': 'message_stop'},
    ]

    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=sse(full), headers={'content-type': 'text/event-stream'})

    rec = Recorder(None)
    client = ClaudeClient(settings, recorder=rec, client=sdk(handler))
    async with aclosing(client.stream_text('C3', ['叙述规则'], '发生的事')) as gen:
        async for _ in gen:
            break
    line = rec.records[-1]
    assert line['error'] is None and line['response']['stopped_early'] is True
