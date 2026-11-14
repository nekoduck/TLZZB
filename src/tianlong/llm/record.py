"""`runs/<id>/calls.jsonl` 的录制与重放（方案 4.3「可重放」、10.5）。

每次逻辑调用（一次 `jev.ask`、一次 `claude.call_json`、一次 `claude.stream_text`）写一行：

    {seq, kind, provider, model, request, response, usage, latency_ms, cost,
     escalated, violations, error, attempts}

调用方事后要补记的东西（是否升级、校验违规）用 `annotate(seq, ...)` 追加一行
`{"note_for": seq, ...}`；重放时跳过这类行。

重放按调用代号分队列，按录制的先后取出。请求与录制不一致（严格模式）或某个代号的录制
用完了，抛 `ReplayMismatch`，并说明是哪一条、哪里不同。
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tianlong.llm.errors import (
    ClaudeError,
    JevError,
    LLMError,
    LLMRefusal,
    LLMUnavailable,
    ReplayMismatch,
    RetryableLLMError,
)

CALLS_FILE = 'calls.jsonl'


def _jsonable(obj: Any) -> Any:
    """把 pydantic 模型、Path 等转成可写进 JSON 的东西。"""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, str | int | float | bool) or obj is None:
        return obj
    return str(obj)


def canonical(obj: Any) -> str:
    return json.dumps(_jsonable(obj), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()[:16]


def error_payload(err: BaseException) -> dict[str, Any]:
    out: dict[str, Any] = {'type': type(err).__name__, 'message': str(err)}
    for attr in ('status', 'stop_reason', 'category', 'reason'):
        v = getattr(err, attr, None)
        if v is not None:
            out[attr] = v
    return out


def error_from_payload(p: Mapping[str, Any]) -> LLMError:
    """把录制的错误还原成同类异常，让重放走同一条兜底路。"""
    t = str(p.get('type', 'LLMError'))
    msg = str(p.get('message', ''))
    if t == 'JevError':
        return JevError(msg, status=p.get('status'))
    if t == 'ClaudeError':
        return ClaudeError(msg, status=p.get('status'))
    if t == 'RetryableLLMError':
        return RetryableLLMError(str(p.get('reason', msg)), stop_reason=p.get('stop_reason'))
    if t == 'LLMRefusal':
        return LLMRefusal(p.get('category'))
    if t == 'LLMUnavailable':
        return LLMUnavailable(msg)
    if t == 'BudgetExceeded':
        return LLMError(msg)
    return LLMError(msg)


class Recorder:
    """把调用写进 calls.jsonl。`path` 为 None 时只记在内存里（测试用）。线程与协程安全。"""

    def __init__(self, path: Path | None, *, keep_in_memory: bool = False) -> None:
        self.path = path
        self.keep_in_memory = keep_in_memory or path is None
        self.records: list[dict[str, Any]] = []
        self._seq = 0
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def count(self) -> int:
        return self._seq

    def _write(self, line: dict[str, Any]) -> None:
        if self.keep_in_memory:
            self.records.append(line)
        if self.path is not None:
            with self.path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(line, ensure_ascii=False) + '\n')

    def record(
        self,
        *,
        kind: str,
        provider: str,
        model: str,
        request: Any,
        response: Any = None,
        usage: Mapping[str, Any] | None = None,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        escalated: bool = False,
        violations: list[str] | None = None,
        error: BaseException | None = None,
        attempts: int = 1,
        extra: Mapping[str, Any] | None = None,
    ) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            line: dict[str, Any] = {
                'seq': seq,
                'kind': kind,
                'provider': provider,
                'model': model,
                'request': _jsonable(request),
                'response': _jsonable(response),
                'usage': _jsonable(dict(usage or {})),
                'latency_ms': round(float(latency_ms), 1),
                'cost': round(float(cost), 12),
                'escalated': bool(escalated),
                'violations': list(violations or []),
                'error': error_payload(error) if error is not None else None,
                'attempts': attempts,
                'request_fp': fingerprint(request),
            }
            if extra:
                line.update(_jsonable(dict(extra)))
            self._write(line)
            return seq

    def annotate(self, seq: int, *, escalated: bool | None = None, violations: list[str] | None = None) -> None:
        """事后补记：这次调用之后被升级了、叙述里有违规的句子等。"""
        note: dict[str, Any] = {'note_for': seq}
        if escalated is not None:
            note['escalated'] = escalated
        if violations is not None:
            note['violations'] = list(violations)
        with self._lock:
            self._write(note)


def read_calls(path: Path) -> list[dict[str, Any]]:
    """读 calls.jsonl 里的调用行（跳过补记行），并把补记合并回去。"""
    calls: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    with path.open(encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            line = json.loads(raw)
            if 'note_for' in line:
                target = calls.get(int(line['note_for']))
                if target is not None:
                    for k, v in line.items():
                        if k != 'note_for':
                            target[k] = v
                continue
            seq = int(line['seq'])
            calls[seq] = line
            order.append(seq)
    return [calls[s] for s in order]


class Replayer:
    """按代号、按先后把录制的响应交回去。"""

    def __init__(self, source: Path, *, strict: bool = True) -> None:
        path = source / CALLS_FILE if source.is_dir() else source
        if not path.is_file():
            raise ReplayMismatch(f'replay source not found: {path}')
        self.path = path
        self.strict = strict
        self._queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._lock = threading.Lock()
        for line in read_calls(path):
            self._queues[str(line['kind'])].append(line)

    def remaining(self) -> dict[str, int]:
        return {k: len(q) for k, q in self._queues.items() if q}

    def next(self, kind: str, request: Any) -> dict[str, Any]:
        with self._lock:
            q = self._queues.get(kind)
            if not q:
                raise ReplayMismatch(
                    f'replay: no more recorded calls of kind {kind!r} in {self.path} '
                    f'(the run now makes more {kind} calls than the recording)'
                )
            rec = q.popleft()
        if self.strict:
            got = _jsonable(request)
            want = rec.get('request')
            if canonical(got) != canonical(want):
                raise ReplayMismatch(
                    f'replay: request for {kind!r} differs from recorded call seq={rec.get("seq")}: '
                    + _describe_diff(want, got)
                )
        return rec


def _describe_diff(want: Any, got: Any, path: str = '') -> str:
    if isinstance(want, Mapping) and isinstance(got, Mapping):
        keys = sorted(set(want) | set(got))
        for k in keys:
            if k not in want:
                return f'{path}.{k} is new'
            if k not in got:
                return f'{path}.{k} is missing'
            if canonical(want[k]) != canonical(got[k]):
                return _describe_diff(want[k], got[k], f'{path}.{k}')
        return f'{path} differs'
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return f'{path} has {len(got)} items, recorded {len(want)}'
        for i, (a, b) in enumerate(zip(want, got, strict=True)):
            if canonical(a) != canonical(b):
                return _describe_diff(a, b, f'{path}[{i}]')
        return f'{path} differs'
    w, g = canonical(want), canonical(got)
    return f'{path or "request"}: recorded {w[:80]!r}, now {g[:80]!r}'


__all__ = [
    'CALLS_FILE',
    'Recorder',
    'Replayer',
    'canonical',
    'error_from_payload',
    'error_payload',
    'fingerprint',
    'read_calls',
]
