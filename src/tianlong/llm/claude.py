"""Claude 客户端（方案 7.4）：官方 `anthropic` SDK，异步。

约定（违反即缺陷）：
- 从不传 `temperature`、`top_p`、`top_k`（SDK 2.x 发的 API 版本 2026-10-21 上一律 400）。
- 思考深浅只用 `output_config.effort` 调；`thinking={'type': 'between_tools'}` 只发给配置了它的
  C3（且只在 Sonnet 5.5 上）。其余不传 `thinking`，5.5 系列默认就是自适应思考。
- JSON 调用把 `anthropic.transform_schema(Model)` 放进 `output_config.format`，先看 stop_reason，
  再自己用 Pydantic 校验（`messages.parse` 会在 SDK 里先校验，截断或拒答时只给出一个解析错误）。
- `max_tokens` 与 JSON 不合 schema 算可重试：再试一次，仍不行抛 `RetryableLLMError`；
  `refusal` 不重试，抛 `LLMRefusal`。
- system 拆成若干块，每块带 `cache_control`（至多 4 个断点，取最后 4 块），TTL 取配置。
- 按块类型读响应：只拼 `text` 块，跳过 thinking、fallback 等。
- 实时调用默认开服务端拒答兜底（beta `server-side-fallback-2026-07-01`，`fallbacks='default'`）；
  Batch 不能用它。
- 缺 `ANTHROPIC_API_KEY` 时，到真正调用那一刻才抛 `LLMUnavailable`。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from tianlong.config import Settings
from tianlong.llm.errors import (
    ClaudeError,
    LLMError,
    LLMRefusal,
    LLMUnavailable,
    RetryableLLMError,
)
from tianlong.llm.specs import ClaudeSpec, claude_spec, thinking_param

if TYPE_CHECKING:
    from tianlong.llm.cost import Ledger
    from tianlong.llm.record import Recorder

T = TypeVar('T', bound=BaseModel)

FALLBACK_BETA = 'server-side-fallback-2026-07-01'
MAX_CACHE_BREAKPOINTS = 4
FORBIDDEN_PARAMS = frozenset({'temperature', 'top_p', 'top_k', 'budget_tokens'})


class CallMeta(BaseModel):
    """一次逻辑调用的元信息。`seq` 是它在 calls.jsonl 里的序号，可用来 `recorder.annotate`。"""

    seq: int = 0
    kind: str
    provider: str
    model: str
    stop_reason: str | None = None
    usage: dict[str, Any] = {}
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    cost: float = 0.0
    attempts: int = 1
    mock: bool = False
    stopped_early: bool = False


@dataclass
class BatchItem:
    """Batch 里的一条请求。schema 为空时结果是纯文本。mock_default 只给 Mock 用。"""

    custom_id: str
    system_blocks: Sequence[str | Mapping[str, Any]] | str | None
    user_text: str
    schema: type[BaseModel] | None = None
    mock_default: Any = None


class BatchStatus(BaseModel):
    id: str
    processing_status: str
    request_counts: dict[str, int] = {}

    @property
    def ended(self) -> bool:
        return self.processing_status == 'ended'


class BatchResult(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    custom_id: str
    ok: bool
    obj: Any = None
    text: str = ''
    stop_reason: str | None = None
    error: str | None = None
    usage: dict[str, Any] = {}
    cost: float = 0.0


# --------------------------------------------------------------------------- 请求的拼装


def cache_control(ttl: str) -> dict[str, str]:
    cc = {'type': 'ephemeral'}
    if ttl == '1h':
        cc['ttl'] = '1h'
    return cc


def system_param(system_blocks: Sequence[str | Mapping[str, Any]] | str | None, ttl: str) -> list[dict[str, Any]]:
    """把 system 块转成带 `cache_control` 的文本块。空块跳过；至多 4 个断点（取最后 4 块）。"""
    if system_blocks is None:
        return []
    if isinstance(system_blocks, str):
        system_blocks = [system_blocks]
    blocks: list[dict[str, Any]] = []
    for b in system_blocks:
        if isinstance(b, str):
            if not b.strip():
                continue
            blocks.append({'type': 'text', 'text': b})
        else:
            d = dict(b)
            if d.get('type', 'text') == 'text' and not str(d.get('text', '')).strip():
                continue
            d.setdefault('type', 'text')
            d.pop('cache_control', None)
            blocks.append(d)
    first_cached = max(0, len(blocks) - MAX_CACHE_BREAKPOINTS)
    for i, d in enumerate(blocks):
        if i >= first_cached:
            d['cache_control'] = cache_control(ttl)
    return blocks


def output_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {'type': 'json_schema', 'schema': anthropic.transform_schema(schema.model_json_schema())}


def build_params(
    spec: ClaudeSpec,
    system_blocks: Sequence[str | Mapping[str, Any]] | str | None,
    user_text: str,
    *,
    schema: type[BaseModel] | None = None,
    ttl: str = '5m',
) -> dict[str, Any]:
    """Messages 请求体（不含 betas、fallbacks、timeout）。录制与重放都以它为准。"""
    output_config: dict[str, Any] = {'effort': spec.effort}
    if schema is not None:
        output_config['format'] = output_format(schema)
    params: dict[str, Any] = {
        'model': spec.model,
        'max_tokens': spec.max_tokens,
        'messages': [{'role': 'user', 'content': user_text}],
        'output_config': output_config,
    }
    system = system_param(system_blocks, ttl)
    if system:
        params['system'] = system
    thinking = thinking_param(spec)
    if thinking is not None:
        params['thinking'] = thinking
    bad = FORBIDDEN_PARAMS & set(params)
    if bad:  # 防御：配置或调用方塞进来的采样参数
        raise ValueError(f'forbidden sampling params: {sorted(bad)}')
    return params


def message_text(msg: Mapping[str, Any]) -> str:
    """按块类型读：只拼 text 块。"""
    parts: list[str] = []
    for block in msg.get('content') or []:
        if isinstance(block, Mapping) and block.get('type') == 'text':
            parts.append(str(block.get('text', '')))
    return ''.join(parts)


def _to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    return dict(obj)


def _refusal(msg: Mapping[str, Any]) -> LLMRefusal:
    sd = msg.get('stop_details') or {}
    if not isinstance(sd, Mapping):
        sd = _to_dict(sd)
    return LLMRefusal(sd.get('category'), sd.get('explanation'))


_SENTENCE_RE = re.compile(r'[^。！？!?…\n]*(?:[。！？!?]+[」』”’）)]*|…+[」』”’）)]*|\n+|$)')


def split_sentences(text: str) -> list[str]:
    """按中文句末标点切句，标点留在句末。"""
    out = [m.group(0) for m in _SENTENCE_RE.finditer(text) if m.group(0)]
    return out


def _resolve(value: Any) -> Any:
    return value() if callable(value) and not isinstance(value, type) else value


# --------------------------------------------------------------------------- 客户端


class ClaudeClient:
    provider = 'claude'
    mock = False

    def __init__(
        self,
        settings: Settings,
        *,
        recorder: Recorder | None = None,
        ledger: Ledger | None = None,
        client: Any = None,
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self.ledger = ledger
        self._client = client
        self._owns_client = client is None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._batch_schemas: dict[str, dict[str, type[BaseModel] | None]] = {}

    # ---- 基础 ----
    def spec(self, kind: str) -> ClaudeSpec:
        return claude_spec(self.settings, kind)

    def ttl(self, override: str | None = None) -> str:
        return override or self.settings.cache.ttl

    def _api(self) -> Any:
        if self._owns_client and self._client is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop is not self._client_loop:
                self._client = None  # 自己建的客户端绑在旧循环上，换循环就重建
        if self._client is None:
            key = self.settings.claude_api_key()
            if not key:
                raise LLMUnavailable(
                    f'Claude API key not set (env {self.settings.claude.api_key_env}); '
                    'falling back to rules and templates'
                )
            self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=self.settings.claude.max_retries)
            self._owns_client = True
            try:
                self._client_loop = asyncio.get_running_loop()
            except RuntimeError:
                self._client_loop = None
        return self._client

    async def aclose(self) -> None:
        c = self._client
        if c is not None and hasattr(c, 'close'):
            res = c.close()
            if asyncio.iscoroutine(res):
                await res

    def _messages(self, *, batch: bool = False) -> tuple[Any, dict[str, Any]]:
        """(messages 资源, 额外参数)。开服务端兜底时走 beta 端点。"""
        api = self._api()
        if self.settings.claude.server_fallback and not batch:
            return api.beta.messages, {'betas': [FALLBACK_BETA], 'fallbacks': 'default'}
        return api.messages, {}

    def _record(
        self,
        meta: CallMeta,
        request: Any,
        response: Any,
        *,
        escalated: bool,
        error: BaseException | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if self.recorder is None:
            return
        meta.seq = self.recorder.record(
            kind=meta.kind,
            provider=meta.provider,
            model=meta.model,
            request=request,
            response=response,
            usage=meta.usage,
            latency_ms=meta.latency_ms,
            cost=meta.cost,
            escalated=escalated,
            error=error,
            attempts=meta.attempts,
            extra=extra,
        )

    def _charge(self, kind: str, model: str, usage: Mapping[str, Any], *, batch: bool = False, ttl: str) -> float:
        if self.ledger is None:
            return 0.0
        return self.ledger.charge_claude(kind, model, usage, batch=batch, ttl=ttl)

    # ---- 传输层（Mock 与测试替换这两处） ----
    async def _create(self, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        res, extra = self._messages()
        try:
            msg = await res.create(**params, **extra, timeout=timeout)
        except anthropic.APITimeoutError as e:
            raise ClaudeError('Claude request timed out') from e
        except anthropic.APIStatusError as e:
            raise ClaudeError(f'Claude HTTP {e.status_code}: {e.message}', status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError('Claude connection error') from e
        return _to_dict(msg)

    async def _stream(
        self, params: dict[str, Any], *, timeout: float, holder: dict[str, Any]
    ) -> AsyncIterator[str]:
        """吐出文本片段；结束时把完整消息放进 holder['final']，中途关闭时放快照进 holder['snapshot']。"""
        res, extra = self._messages()
        try:
            async with res.stream(**params, **extra, timeout=timeout) as stream:
                try:
                    async for text in stream.text_stream:
                        yield text
                    holder['final'] = _to_dict(await stream.get_final_message())
                finally:
                    if 'final' not in holder:
                        try:
                            holder['snapshot'] = _to_dict(stream.current_message_snapshot)
                        except (AssertionError, AttributeError):
                            pass
        except anthropic.APITimeoutError as e:
            raise ClaudeError('Claude stream timed out') from e
        except anthropic.APIStatusError as e:
            raise ClaudeError(f'Claude HTTP {e.status_code}: {e.message}', status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError('Claude connection error') from e

    # ---- JSON 调用 ----
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
        ttl_ = self.ttl(ttl)
        params = build_params(spec, system_blocks, user_text, schema=schema, ttl=ttl_)
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model, attempts=0)
        responses: list[dict[str, Any]] = []
        last_err: LLMError | None = None
        t0 = time.perf_counter()
        try:
            for _ in range(2):
                if self.ledger is not None:
                    self.ledger.check()
                msg = await self._create(params, timeout=spec.timeout_s)
                meta.attempts += 1
                responses.append(msg)
                usage = _to_dict(msg.get('usage'))
                served = str(msg.get('model') or spec.model)
                meta.model = served
                meta.cost += self._charge(kind, served, usage, ttl=ttl_)
                meta.usage = usage
                meta.stop_reason = msg.get('stop_reason')
                if meta.stop_reason == 'refusal':
                    raise _refusal(msg)
                if meta.stop_reason == 'max_tokens':
                    last_err = RetryableLLMError('stop_reason max_tokens', stop_reason='max_tokens')
                    continue
                try:
                    obj = schema.model_validate_json(message_text(msg))
                except ValidationError as e:
                    last_err = RetryableLLMError(f'output does not match {schema.__name__}: {e.error_count()} errors')
                    continue
                meta.latency_ms = (time.perf_counter() - t0) * 1000.0
                self._record(meta, params, msg, escalated=escalated, extra=_prior(responses))
                return obj, meta
            assert last_err is not None
            raise last_err
        except LLMError as e:
            meta.latency_ms = (time.perf_counter() - t0) * 1000.0
            self._record(
                meta, params, responses[-1] if responses else None, escalated=escalated, error=e,
                extra=_prior(responses),
            )
            raise

    # ---- 流式文本 ----
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
        """逐片吐出文字。refusal 在吐完已有文字后抛 `LLMRefusal`；max_tokens 不抛，记在 meta 里。

        调用方可以中途停下（`aclose()` 或跳出 `async for`）；这次调用照样记账、录制。
        传入 `meta_out` 列表时，结束后把 `CallMeta` 追加进去。
        """
        spec = self.spec(kind)
        ttl_ = self.ttl(ttl)
        params = build_params(spec, system_blocks, user_text, ttl=ttl_)
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model)
        holder: dict[str, Any] = {}
        pieces: list[str] = []
        error: LLMError | None = None
        t0 = time.perf_counter()
        try:
            if self.ledger is not None:
                self.ledger.check()
            inner = self._stream(params, timeout=spec.timeout_s, holder=holder)
            try:
                async for piece in inner:
                    if meta.first_token_ms is None:
                        meta.first_token_ms = (time.perf_counter() - t0) * 1000.0
                    pieces.append(piece)
                    yield piece
            finally:
                await inner.aclose()
            final = holder.get('final') or {}
            meta.stop_reason = final.get('stop_reason')
            if meta.stop_reason == 'refusal':
                raise _refusal(final)
        except GeneratorExit:
            meta.stopped_early = True
            raise
        except LLMError as e:
            error = e
            raise
        finally:
            msg = holder.get('final') or holder.get('snapshot') or {}
            usage = _to_dict(msg.get('usage'))
            served = str(msg.get('model') or spec.model)
            meta.model = served
            meta.usage = usage
            if msg:
                meta.cost = self._charge(kind, served, usage, ttl=ttl_)
            meta.latency_ms = (time.perf_counter() - t0) * 1000.0
            response = {
                'pieces': pieces,
                'stop_reason': meta.stop_reason,
                'stopped_early': meta.stopped_early,
                'message': msg or None,
            }
            self._record(meta, params, response, escalated=escalated, error=error)
            if meta_out is not None:
                meta_out.append(meta)

    # ---- Batch（离线管线） ----
    def _batch_requests(
        self, kind: str, items: Sequence[BatchItem], ttl: str
    ) -> tuple[ClaudeSpec, list[dict[str, Any]]]:
        spec = self.spec(kind)
        reqs: list[dict[str, Any]] = []
        for it in items:
            params = build_params(spec, it.system_blocks, it.user_text, schema=it.schema, ttl=ttl)
            reqs.append({'custom_id': it.custom_id, 'params': params})
        return spec, reqs

    async def batch_submit(self, kind: str, items: Sequence[BatchItem], *, ttl: str | None = None) -> str:
        ttl_ = self.ttl(ttl)
        spec, reqs = self._batch_requests(kind, items, ttl_)
        if self.ledger is not None:
            self.ledger.check()
        res, _ = self._messages(batch=True)
        t0 = time.perf_counter()
        try:
            batch = await res.batches.create(requests=reqs)
        except anthropic.APIStatusError as e:
            raise ClaudeError(f'Claude batch HTTP {e.status_code}: {e.message}', status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError('Claude batch connection error') from e
        bid = str(batch.id)
        self._batch_schemas[bid] = {it.custom_id: it.schema for it in items}
        meta = CallMeta(kind=kind, provider=self.provider, model=spec.model,
                        latency_ms=(time.perf_counter() - t0) * 1000.0)
        self._record(meta, {'batch': reqs}, {'batch_id': bid}, escalated=False,
                     extra={'batch': 'submit'})
        return bid

    async def batch_poll(self, batch_id: str) -> BatchStatus:
        res, _ = self._messages(batch=True)
        try:
            b = await res.batches.retrieve(batch_id)
        except anthropic.APIStatusError as e:
            raise ClaudeError(f'Claude batch HTTP {e.status_code}: {e.message}', status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError('Claude batch connection error') from e
        counts = _to_dict(getattr(b, 'request_counts', None))
        return BatchStatus(id=batch_id, processing_status=str(b.processing_status),
                           request_counts={k: int(v) for k, v in counts.items()})

    async def batch_results(
        self,
        batch_id: str,
        kind: str,
        *,
        schemas: Mapping[str, type[BaseModel] | None] | type[BaseModel] | None = None,
        ttl: str | None = None,
    ) -> dict[str, BatchResult]:
        """取回结果（顺序不定，按 custom_id 索引）。有 schema 的条目校验 JSON。"""
        res, _ = self._messages(batch=True)
        ttl_ = self.ttl(ttl)
        known = self._batch_schemas.get(batch_id, {})
        out: dict[str, BatchResult] = {}
        try:
            stream = await res.batches.results(batch_id)
            async for item in stream:
                cid = str(item.custom_id)
                if isinstance(schemas, type):
                    schema: type[BaseModel] | None = schemas
                elif isinstance(schemas, Mapping):
                    schema = schemas.get(cid)
                else:
                    schema = known.get(cid)
                out[cid] = self._batch_one(kind, cid, _to_dict(item.result), schema, ttl_)
        except anthropic.APIStatusError as e:
            raise ClaudeError(f'Claude batch HTTP {e.status_code}: {e.message}', status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError('Claude batch connection error') from e
        return out

    def _batch_one(
        self, kind: str, cid: str, result: Mapping[str, Any], schema: type[BaseModel] | None, ttl: str
    ) -> BatchResult:
        rtype = result.get('type')
        if rtype != 'succeeded':
            err = result.get('error')
            r = BatchResult(custom_id=cid, ok=False, error=f'{rtype}: {err}' if err else str(rtype))
        else:
            msg = _to_dict(result.get('message'))
            usage = _to_dict(msg.get('usage'))
            model = str(msg.get('model') or self.spec(kind).model)
            cost = self._charge(kind, model, usage, batch=True, ttl=ttl)
            stop = msg.get('stop_reason')
            text = message_text(msg)
            r = BatchResult(custom_id=cid, ok=True, text=text, stop_reason=stop, usage=usage, cost=cost)
            if stop in ('refusal', 'max_tokens'):
                r.ok, r.error = False, f'stop_reason {stop}'
            elif schema is not None:
                try:
                    r.obj = schema.model_validate_json(text)
                except ValidationError as e:
                    r.ok, r.error = False, f'output does not match {schema.__name__}: {e.error_count()} errors'
        meta = CallMeta(kind=kind, provider=f'{self.provider}-batch', model=self.spec(kind).model,
                        stop_reason=r.stop_reason, usage=r.usage, cost=r.cost)
        self._record(meta, {'custom_id': cid}, dict(result), escalated=False,
                     extra={'batch': 'result', 'ok': r.ok})
        return r


def _prior(responses: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(responses) <= 1:
        return None
    return {'prior_responses': responses[:-1]}
