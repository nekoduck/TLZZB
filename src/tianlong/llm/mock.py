"""确定性的替身（方案 10.6、ARCHITECTURE 4.2）。不联网，不花钱，照样录进 calls.jsonl。

- `MockJev`：choice 取描述以「（你本来要做的）」开头的项，否则取第一项，概率给 1；
  noul 取 hints 里的值，没有就是 0；score 取 hints 里的值，没有就是 1（截在刻度之内）。
  hints 里给 choice 题写一个选项 id，就取那一项（测试用）。
- `MockClaude`：`call_json` 返回 `mock_default()`；`stream_text` 逐句吐出 `mock_text`。
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from tianlong.llm.claude import (
    BatchItem,
    BatchResult,
    BatchStatus,
    CallMeta,
    ClaudeClient,
    T,
    build_params,
    split_sentences,
)
from tianlong.llm.errors import LLMUnavailable
from tianlong.llm.jev import Answers, JevClient, validate_response

STAR_PREFIX = '（你本来要做的）'


def _desc_text(desc: Any) -> str:
    """选项描述可以是字符串、对象或数组；取能读的文字。"""
    if desc is None:
        return ''
    if isinstance(desc, str):
        return desc
    if isinstance(desc, Mapping):
        for v in desc.values():
            t = _desc_text(v)
            if t:
                return t
        return ''
    if isinstance(desc, list | tuple):
        return _desc_text(desc[0]) if desc else ''
    return str(desc)


def _is_star(desc: Any) -> bool:
    return _desc_text(desc).lstrip().startswith(STAR_PREFIX)


def mock_choice(question: Mapping[str, Any], hint: Any = None) -> dict[str, Any]:
    criteria = question.get('criteria') or {}
    keys = [str(k) for k in criteria] if isinstance(criteria, Mapping) else []
    if not keys:
        return {'type': 'choice', 'choice': '', 'confidence': 0.0, 'probabilities': {}}
    pick: str | None = None
    if isinstance(hint, str) and hint in keys:
        pick = hint
    if pick is None:
        for k in keys:
            if _is_star(criteria[k]):
                pick = k
                break
    if pick is None:
        pick = keys[0]
    probs = {k: (1.0 if k == pick else 0.0) for k in keys}
    return {'type': 'choice', 'choice': pick, 'confidence': 1.0, 'probabilities': probs}


def score_confidence(probabilities: Sequence[float]) -> float:
    """官方给的 Score confidence 公式（docs/vendor/jev/confidence.md）。"""
    n = len(probabilities)
    if n < 2:
        return 1.0
    m = max(range(n), key=lambda i: probabilities[i])
    spread = sum(p * abs(i - m) for i, p in enumerate(probabilities))
    even = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return max(0.0, 1.0 - spread / even) if even else 1.0


def mock_score(question: Mapping[str, Any], hint: Any = None) -> dict[str, Any]:
    criteria = question.get('criteria') or []
    n = len(criteria) if isinstance(criteria, list | tuple) else 0
    top = max(0, n - 1)
    value = float(hint) if isinstance(hint, int | float) and not isinstance(hint, bool) else 1.0
    value = min(max(value, 0.0), float(top))
    lo = math.floor(value)
    hi = min(lo + 1, top)
    frac = value - lo
    probs = [0.0] * max(n, 1)
    if hi == lo or frac == 0.0:
        probs[lo] = 1.0
    else:
        probs[lo] = round(1.0 - frac, 6)
        probs[hi] = round(frac, 6)
    legend = {str(i): c for i, c in enumerate(criteria)} if n else {}
    return {
        'type': 'score',
        'score': value,
        'confidence': round(score_confidence(probs), 6),
        'legend': legend,
        'probabilities': {str(i): p for i, p in enumerate(probs)},
    }


def mock_noul(question: Mapping[str, Any], hint: Any = None) -> dict[str, Any]:
    if isinstance(hint, bool):
        value = 1.0 if hint else 0.0
    elif isinstance(hint, int | float):
        value = min(max(float(hint), 0.0), 1.0)
    else:
        value = 0.0
    return {'type': 'noul', 'noul': value}


def mock_answers(questions: Mapping[str, Mapping[str, Any]], hints: Mapping[str, Any] | None) -> dict[str, Any]:
    hints = hints or {}
    out: dict[str, Any] = {}
    for qid, q in questions.items():
        t = q.get('type')
        h = hints.get(qid)
        if t == 'choice':
            out[qid] = mock_choice(q, h)
        elif t == 'score':
            out[qid] = mock_score(q, h)
        elif t == 'noul':
            out[qid] = mock_noul(q, h)
        else:
            raise ValueError(f'unknown question type {t!r} for {qid!r}')
    return out


class MockJev(JevClient):
    provider = 'mock-jev'

    async def ask(
        self,
        kind: str,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        hints: Mapping[str, Any] | None = None,
        escalated: bool = False,
    ) -> Answers:
        spec = self.spec(kind)
        body = self.build_request(spec, state, questions)
        data = {'model': spec.model, 'answers': mock_answers(questions, hints), 'usage': {'input_tokens': 0,
                                                                                           'output_tokens': 0}}
        answers = validate_response(data, questions)
        if self.recorder is not None:
            self.recorder.record(
                kind=kind, provider=self.provider, model=spec.model, request=body, response=data,
                usage=data['usage'], latency_ms=0.0, cost=0.0, escalated=escalated,
                extra={'hints': dict(hints)} if hints else None,
            )
        return answers


def _resolve(value: Any) -> Any:
    if callable(value) and not isinstance(value, type):
        return value()
    return value


def _fake_message(model: str, text: str) -> dict[str, Any]:
    return {
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': [{'type': 'text', 'text': text}],
        'stop_reason': 'end_turn',
        'usage': {'input_tokens': 0, 'output_tokens': 0},
    }


class MockClaude(ClaudeClient):
    provider = 'mock-claude'
    mock = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mock_batches: dict[str, tuple[str, list[BatchItem]]] = {}

    def _api(self) -> Any:  # 永远不建真客户端
        raise LLMUnavailable('MockClaude has no API client')

    async def call_json(
        self,
        kind: str,
        system_blocks: Sequence[str | Mapping[str, Any]] | str | None,
        user_text: str,
        schema: type[T],
        *,
        mock_default: Callable[[], T] | T | None = None,
        escalated: bool = False,
        ttl: str | None = None,
    ) -> tuple[T, CallMeta]:
        spec = self.spec(kind)
        params = build_params(spec, system_blocks, user_text, schema=schema, ttl=self.ttl(ttl))
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model, stop_reason='end_turn', mock=True)
        if mock_default is None:
            err = LLMUnavailable(f'MockClaude.call_json({kind}) got no mock_default')
            self._record(meta, params, None, escalated=escalated, error=err)
            raise err
        value = _resolve(mock_default)
        obj = value if isinstance(value, schema) else schema.model_validate(value)
        msg = _fake_message(spec.model, obj.model_dump_json())
        meta.usage = dict(msg['usage'])
        self._record(meta, params, msg, escalated=escalated)
        return obj, meta

    async def stream_text(
        self,
        kind: str,
        system_blocks: Sequence[str | Mapping[str, Any]] | str | None,
        user_text: str,
        *,
        mock_text: str | Callable[[], str] | None = None,
        escalated: bool = False,
        ttl: str | None = None,
        meta_out: list[CallMeta] | None = None,
    ) -> AsyncIterator[str]:
        spec = self.spec(kind)
        params = build_params(spec, system_blocks, user_text, ttl=self.ttl(ttl))
        text = str(_resolve(mock_text) or '')
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model, stop_reason='end_turn', mock=True,
                        first_token_ms=0.0)
        pieces: list[str] = []
        try:
            for piece in split_sentences(text):
                pieces.append(piece)
                yield piece
        except GeneratorExit:
            meta.stopped_early = True
            raise
        finally:
            response = {'pieces': pieces, 'stop_reason': meta.stop_reason, 'stopped_early': meta.stopped_early,
                        'message': _fake_message(spec.model, text)}
            self._record(meta, params, response, escalated=escalated)
            if meta_out is not None:
                meta_out.append(meta)

    # ---- Batch：提交即完成 ----
    async def batch_submit(self, kind: str, items: Sequence[BatchItem], *, ttl: str | None = None) -> str:
        spec, reqs = self._batch_requests(kind, items, self.ttl(ttl))
        bid = f'mockbatch_{len(self._mock_batches) + 1:04d}'
        self._mock_batches[bid] = (kind, list(items))
        self._batch_schemas[bid] = {it.custom_id: it.schema for it in items}
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model, mock=True)
        self._record(meta, {'batch': reqs}, {'batch_id': bid}, escalated=False, extra={'batch': 'submit'})
        return bid

    async def batch_poll(self, batch_id: str) -> BatchStatus:
        _, items = self._mock_batches[batch_id]
        return BatchStatus(id=batch_id, processing_status='ended', request_counts={'succeeded': len(items)})

    async def batch_results(
        self,
        batch_id: str,
        kind: str,
        *,
        schemas: Mapping[str, type[BaseModel] | None] | type[BaseModel] | None = None,
        ttl: str | None = None,
    ) -> dict[str, BatchResult]:
        _, items = self._mock_batches[batch_id]
        model = self.spec(kind).model
        out: dict[str, BatchResult] = {}
        for it in items:
            if it.mock_default is None:
                result: dict[str, Any] = {'type': 'errored', 'error': 'no mock_default'}
            else:
                value = _resolve(it.mock_default)
                if isinstance(value, BaseModel):
                    text = value.model_dump_json()
                elif isinstance(value, str):
                    text = value
                else:
                    text = json.dumps(value, ensure_ascii=False)
                result = {'type': 'succeeded', 'message': _fake_message(model, text)}
            schema = schemas if isinstance(schemas, type) else (
                schemas.get(it.custom_id) if isinstance(schemas, Mapping) else it.schema)
            out[it.custom_id] = self._batch_one(kind, it.custom_id, result, schema, self.ttl(ttl))
        return out

