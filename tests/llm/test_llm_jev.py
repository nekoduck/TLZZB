from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from tianlong.config import Settings
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import JevError, LLMUnavailable
from tianlong.llm.jev import ChoiceAnswer, JevClient, NoulAnswer, ScoreAnswer, validate_response
from tianlong.llm.record import Recorder


def make_client(settings: Settings, handler: Any, **kw: Any) -> tuple[JevClient, Recorder]:
    rec = Recorder(None)
    client = JevClient(settings, recorder=rec, ledger=Ledger.from_settings(settings),
                       transport=httpx.MockTransport(handler), backoff_s=0.0, **kw)
    return client, rec


def test_fixture_parses(smoke_request: dict[str, Any], smoke_response: dict[str, Any]) -> None:
    answers = validate_response(smoke_response, smoke_request['questions'])
    a = answers['action']
    assert isinstance(a, ChoiceAnswer)
    assert a.choice in smoke_request['questions']['action']['criteria']
    assert a.top == a.choice
    assert 0.0 <= a.confidence <= 1.0
    assert abs(sum(a.probabilities.values()) - 1.0) < 0.02
    ranked = a.ranked()
    assert a.margin == pytest.approx(ranked[0][1] - ranked[1][1])
    assert a['choice'] == a.choice  # 原始 JSON 照样能读
    s = answers['weight']
    assert isinstance(s, ScoreAnswer)
    assert set(s.levels) == {0, 1, 2, 3}
    assert 0.0 <= s.score <= 3.0 and s.level in s.levels
    assert 0.0 <= s.normalized() <= 1.0
    n = answers['speak']
    assert isinstance(n, NoulAnswer)
    assert 0.0 <= n.noul <= 1.0
    assert n.confidence == pytest.approx(abs(2 * n.noul - 1))


async def test_request_shape_and_parsing(
    settings: Settings, smoke_request: dict[str, Any], smoke_response: dict[str, Any]
) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=smoke_response)

    client, rec = make_client(settings, handler)
    answers = await client.ask('J2', smoke_request['state'], smoke_request['questions'], hints={'speak': 1.0})
    await client.aclose()
    assert len(seen) == 1
    req = seen[0]
    assert req.method == 'POST'
    assert str(req.url) == 'https://api.typesafe.ai/v1/systemone'
    assert req.headers['authorization'] == 'Bearer test-jev-key'
    body = json.loads(req.content)
    assert body == {'model': settings.jev.model, 'state': smoke_request['state'],
                    'questions': smoke_request['questions']}
    assert 'hints' not in body  # hints 只给 Mock
    assert set(answers) == {'action', 'speak', 'weight'}
    # 录制与记账
    assert rec.count == 1
    line = rec.records[0]
    assert line['kind'] == 'J2' and line['provider'] == 'jev'
    assert line['usage']['input_tokens'] == smoke_response['usage']['input_tokens']
    assert line['cost'] == pytest.approx(smoke_response['usage']['input_tokens'] * 0.042 / 1e6)
    assert 'test-jev-key' not in json.dumps(line)


@pytest.mark.parametrize('first', [529, 429, 503, 'timeout'])
async def test_retry_once_then_succeed(
    settings: Settings, smoke_request: dict[str, Any], smoke_response: dict[str, Any], first: Any
) -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first == 'timeout':
                raise httpx.ReadTimeout('slow', request=req)
            return httpx.Response(first, json={'error': 'busy'})
        return httpx.Response(200, json=smoke_response)

    client, rec = make_client(settings, handler)
    answers = await client.ask('J2', smoke_request['state'], smoke_request['questions'])
    assert calls == 2
    assert 'action' in answers
    assert rec.records[0]['error'] is None


async def test_fails_after_one_retry(settings: Settings, smoke_request: dict[str, Any]) -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(529, json={'error': 'overloaded'})

    client, rec = make_client(settings, handler)
    with pytest.raises(JevError) as ei:
        await client.ask('J1', smoke_request['state'], smoke_request['questions'])
    assert calls == 2
    assert ei.value.status == 529
    assert rec.records[0]['error']['type'] == 'JevError'


async def test_client_error_not_retried(settings: Settings, smoke_request: dict[str, Any]) -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, json={'detail': 'bad question'})

    client, _ = make_client(settings, handler)
    with pytest.raises(JevError) as ei:
        await client.ask('J1', smoke_request['state'], smoke_request['questions'])
    assert calls == 1 and ei.value.status == 422


async def test_malformed_response_retried(
    settings: Settings, smoke_request: dict[str, Any], smoke_response: dict[str, Any]
) -> None:
    bad = json.loads(json.dumps(smoke_response))
    bad['answers']['action']['choice'] = 'zzz'  # 清单外的选项
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=bad if calls == 1 else smoke_response)

    client, _ = make_client(settings, handler)
    answers = await client.ask('J2', smoke_request['state'], smoke_request['questions'])
    assert calls == 2 and answers['action'].type == 'choice'

    calls = 0

    def always_bad(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={'model': 'jev-1.13.0', 'answers': {}})

    client2, _ = make_client(settings, always_bad)
    with pytest.raises(JevError):
        await client2.ask('J2', smoke_request['state'], smoke_request['questions'])
    assert calls == 2


async def test_missing_key_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, smoke_request: dict[str, Any]
) -> None:
    monkeypatch.delenv('JEV_KEY')
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    client, rec = make_client(settings, handler)
    with pytest.raises(LLMUnavailable):
        await client.ask('J1', smoke_request['state'], smoke_request['questions'])
    assert not called
    assert rec.records[0]['error']['type'] == 'LLMUnavailable'


async def test_semaphore_limits_parallelism(
    settings: Settings, smoke_request: dict[str, Any], smoke_response: dict[str, Any]
) -> None:
    settings.jev.max_parallel = 3
    active = 0
    peak = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=smoke_response)

    client, rec = make_client(settings, handler)
    await asyncio.gather(*(client.ask('J2', smoke_request['state'], smoke_request['questions'])
                           for _ in range(10)))
    assert peak <= 3
    assert rec.count == 10
    assert sorted(r['seq'] for r in rec.records) == list(range(1, 11))


async def test_model_override(settings: Settings, smoke_request: dict[str, Any],
                              smoke_response: dict[str, Any]) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=smoke_response)

    client, _ = make_client(settings, handler, model='jev-1.14.0')
    await client.ask('J2', smoke_request['state'], smoke_request['questions'])
    assert seen[0]['model'] == 'jev-1.14.0'


def test_client_survives_new_event_loops(
    settings: Settings, smoke_request: dict[str, Any], smoke_response: dict[str, Any]
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=smoke_response)

    client, rec = make_client(settings, handler)
    for _ in range(2):  # 两次 asyncio.run：信号量与 httpx 客户端要跟着换循环
        asyncio.run(client.ask('J2', smoke_request['state'], smoke_request['questions']))
    assert rec.count == 2
