"""`--replay`：按录制返回，不联网（方案 4.3「可重放」）。

录制里失败的调用，重放时抛同类异常，让游戏走同一条兜底路。重放的调用也照样录进
本次的 calls.jsonl，供逐字对照。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from tianlong.config import Settings
from tianlong.llm.claude import (
    BatchItem,
    BatchResult,
    BatchStatus,
    CallMeta,
    ClaudeClient,
    T,
    build_params,
    message_text,
)
from tianlong.llm.errors import LLMUnavailable, ReplayMismatch
from tianlong.llm.jev import Answers, JevClient, validate_response
from tianlong.llm.record import Recorder, Replayer, error_from_payload


def _meta_from(rec: Mapping[str, Any], provider: str) -> CallMeta:
    resp = rec.get('response') or {}
    stop = None
    if isinstance(resp, Mapping):
        stop = resp.get('stop_reason')
    return CallMeta(
        kind=str(rec.get('kind')),
        provider=provider,
        model=str(rec.get('model', '')),
        stop_reason=stop,
        usage=dict(rec.get('usage') or {}),
        attempts=int(rec.get('attempts') or 1),
    )


class ReplayJev(JevClient):
    provider = 'replay-jev'

    def __init__(self, settings: Settings, replayer: Replayer, *, recorder: Recorder | None = None) -> None:
        super().__init__(settings, recorder=recorder)
        self.replayer = replayer

    async def ask(
        self,
        kind: str,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        hints: Mapping[str, Any] | None = None,
        escalated: bool = False,
        timeout_s: float | None = None,  # 重放不计时：录制里超时的那一次照样抛 JevTimeout
    ) -> Answers:
        spec = self.spec(kind)
        body = self.build_request(spec, state, questions)
        rec = self.replayer.next(kind, body)
        err_p = rec.get('error')
        if err_p:
            err = error_from_payload(err_p)
            self._record(kind, spec, body, None, 0.0, 0.0, escalated, attempts=0, error=err)
            raise err
        data = rec.get('response') or {}
        try:
            answers = validate_response(data, questions)
        except ValueError as e:
            raise ReplayMismatch(f'replay: recorded {kind} answers do not fit the questions: {e}') from e
        self._record(kind, spec, body, data, 0.0, 0.0, escalated, attempts=int(rec.get('attempts') or 1))
        return answers


class ReplayClaude(ClaudeClient):
    provider = 'replay-claude'

    def __init__(self, settings: Settings, replayer: Replayer, *, recorder: Recorder | None = None) -> None:
        super().__init__(settings, recorder=recorder)
        self.replayer = replayer

    def _api(self) -> Any:
        raise LLMUnavailable('replay mode makes no Claude requests')

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
        rec = self.replayer.next(kind, params)
        meta = _meta_from(rec, self.provider)
        resp = rec.get('response')
        err_p = rec.get('error')
        if err_p:
            err = error_from_payload(err_p)
            self._record(meta, params, resp, escalated=escalated, error=err)
            raise err
        try:
            obj = schema.model_validate_json(message_text(resp or {}))
        except ValidationError as e:
            raise ReplayMismatch(f'replay: recorded {kind} output does not fit {schema.__name__}') from e
        self._record(meta, params, resp, escalated=escalated)
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
        rec = self.replayer.next(kind, params)
        meta = _meta_from(rec, self.provider)
        resp = rec.get('response') or {}
        pieces = [str(p) for p in (resp.get('pieces') or [])] if isinstance(resp, Mapping) else []
        err_p = rec.get('error')
        out: list[str] = []
        try:
            for p in pieces:
                out.append(p)
                yield p
            if err_p:
                raise error_from_payload(err_p)
        except GeneratorExit:
            meta.stopped_early = True
            raise
        finally:
            err = error_from_payload(err_p) if err_p else None
            self._record(meta, params, {'pieces': out, 'stop_reason': meta.stop_reason,
                                        'stopped_early': meta.stopped_early}, escalated=escalated, error=err)
            if meta_out is not None:
                meta_out.append(meta)

    async def batch_submit(self, kind: str, items: Sequence[BatchItem], *, ttl: str | None = None) -> str:
        raise LLMUnavailable('batch calls are not replayed; rerun the pipeline step instead')

    async def batch_poll(self, batch_id: str) -> BatchStatus:
        raise LLMUnavailable('batch calls are not replayed')

    async def batch_results(
        self,
        batch_id: str,
        kind: str,
        *,
        schemas: Mapping[str, type[BaseModel] | None] | type[BaseModel] | None = None,
        ttl: str | None = None,
    ) -> dict[str, BatchResult]:
        raise LLMUnavailable('batch calls are not replayed')
