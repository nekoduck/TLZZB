"""Jev 客户端（方案 7.2、7.3）：用 httpx 直接调 `POST {base_url}/v1/systemone`。

`ask()` 返回 `Answers`：问题 id → 答案。答案是 `dict` 的子类，所以既可以按原始 JSON
读（`a['action']['choice']`），也可以用属性读（`a['action'].top`、`.margin`、`.confidence`）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx

from tianlong.config import Settings
from tianlong.llm.errors import JevError, LLMUnavailable
from tianlong.llm.specs import JevSpec, jev_spec

if TYPE_CHECKING:
    from tianlong.llm.cost import Ledger
    from tianlong.llm.record import Recorder


# --------------------------------------------------------------------------- 答案类型


class JevAnswer(dict[str, Any]):
    """一个问题的答案。原样保留响应里的字段。"""

    @property
    def type(self) -> str:
        return str(self.get('type', ''))

    @property
    def confidence(self) -> float:
        return float(self.get('confidence', 0.0))


class ChoiceAnswer(JevAnswer):
    @property
    def choice(self) -> str:
        c = self.get('choice')
        return str(c) if c is not None else self.top

    @property
    def probabilities(self) -> dict[str, float]:
        return {str(k): float(v) for k, v in (self.get('probabilities') or {}).items()}

    def ranked(self) -> list[tuple[str, float]]:
        """按概率从高到低；同概率时保持响应里的顺序。"""
        items = list(self.probabilities.items())
        return sorted(items, key=lambda kv: -kv[1])

    @property
    def top(self) -> str:
        r = self.ranked()
        if r:
            return r[0][0]
        return str(self.get('choice', ''))

    @property
    def top_p(self) -> float:
        r = self.ranked()
        return r[0][1] if r else 0.0

    @property
    def margin(self) -> float:
        """头两名的概率差；只有一项时为头名的概率。"""
        r = self.ranked()
        if not r:
            return 0.0
        return r[0][1] - (r[1][1] if len(r) > 1 else 0.0)

    def p(self, option: str) -> float:
        return self.probabilities.get(option, 0.0)


class ScoreAnswer(JevAnswer):
    @property
    def score(self) -> float:
        return float(self.get('score', 0.0))

    @property
    def levels(self) -> dict[int, float]:
        """级数 → 概率（响应里的键是字符串）。"""
        return {int(k): float(v) for k, v in (self.get('probabilities') or {}).items()}

    @property
    def probabilities(self) -> dict[int, float]:
        return self.levels

    @property
    def level(self) -> int:
        lv = self.levels
        if not lv:
            return round(self.score)
        return max(sorted(lv), key=lambda k: lv[k])

    @property
    def top(self) -> int:
        return self.level

    @property
    def margin(self) -> float:
        ps = sorted(self.levels.values(), reverse=True)
        if not ps:
            return 0.0
        return ps[0] - (ps[1] if len(ps) > 1 else 0.0)

    def normalized(self) -> float:
        """把分数换算到 0 到 1。"""
        lv = self.levels
        top_level = max(lv) if lv else 0
        return self.score / top_level if top_level else 0.0


class NoulAnswer(JevAnswer):
    @property
    def noul(self) -> float:
        return float(self.get('noul', 0.0))

    @property
    def confidence(self) -> float:
        """Noul 没有 confidence 字段；按官方建议取 |2p − 1|。"""
        return abs(2.0 * self.noul - 1.0)

    def yes(self, threshold: float = 0.5) -> bool:
        return self.noul > threshold


Answers = dict[str, JevAnswer]

_TYPES: dict[str, type[JevAnswer]] = {'choice': ChoiceAnswer, 'score': ScoreAnswer, 'noul': NoulAnswer}


def wrap_answer(raw: Mapping[str, Any]) -> JevAnswer:
    cls = _TYPES.get(str(raw.get('type', '')), JevAnswer)
    return cls(raw)


def wrap_answers(raw: Mapping[str, Mapping[str, Any]]) -> Answers:
    return {str(k): wrap_answer(v) for k, v in raw.items()}


def validate_response(data: Any, questions: Mapping[str, Mapping[str, Any]]) -> Answers:
    """核对响应：每个问题都有答案，类型对得上，choice 落在选项里。不合格抛 ValueError。"""
    if not isinstance(data, Mapping) or not isinstance(data.get('answers'), Mapping):
        raise ValueError('response has no answers object')
    raw = data['answers']
    out: Answers = {}
    for qid, q in questions.items():
        a = raw.get(qid)
        if not isinstance(a, Mapping):
            raise ValueError(f'answer missing for question {qid!r}')
        qtype = q.get('type')
        if a.get('type') != qtype:
            raise ValueError(f'answer type {a.get("type")!r} != question type {qtype!r} for {qid!r}')
        if qtype == 'choice':
            criteria = q.get('criteria') or {}
            if 'choice' not in a or not isinstance(a.get('probabilities'), Mapping):
                raise ValueError(f'choice answer {qid!r} lacks choice/probabilities')
            if isinstance(criteria, Mapping) and criteria and str(a['choice']) not in criteria:
                raise ValueError(f'choice {a["choice"]!r} for {qid!r} is not among the options')
        elif qtype == 'score':
            if 'score' not in a or not isinstance(a.get('probabilities'), Mapping):
                raise ValueError(f'score answer {qid!r} lacks score/probabilities')
        elif qtype == 'noul':
            if not isinstance(a.get('noul'), int | float):
                raise ValueError(f'noul answer {qid!r} lacks noul')
        out[qid] = wrap_answer(a)
    return out


# --------------------------------------------------------------------------- 客户端

_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})


class JevClient:
    """真 Jev。hints 只给 Mock 用，这里忽略。"""

    provider = 'jev'

    def __init__(
        self,
        settings: Settings,
        *,
        recorder: Recorder | None = None,
        ledger: Ledger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_s: float = 0.25,
        max_backoff_s: float = 2.0,
        model: str | None = None,
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self.ledger = ledger
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._sem: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.backoff_s = backoff_s
        self.max_backoff_s = max_backoff_s
        self.model_override = model

    def spec(self, kind: str) -> JevSpec:
        s = jev_spec(self.settings, kind)
        if self.model_override:
            s = s.model_copy(update={'model': self.model_override})
        return s

    def _bind_loop(self) -> asyncio.Semaphore:
        """信号量与 httpx 客户端都绑在事件循环上；换了循环（例如多次 asyncio.run）就重建。"""
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._sem is None:
            self._loop = loop
            self._sem = asyncio.Semaphore(max(1, self.settings.jev.max_parallel))
            self._client = None
        return self._sem

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_request(self, spec: JevSpec, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        return {'model': spec.model, 'state': state, 'questions': dict(questions)}

    async def ask(
        self,
        kind: str,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        hints: Mapping[str, Any] | None = None,
        escalated: bool = False,
    ) -> Answers:
        """问一组问题，返回 问题 id → 答案。失败（重试之后）抛 `JevError` 或 `LLMUnavailable`。"""
        spec = self.spec(kind)
        body = self.build_request(spec, state, questions)
        data: dict[str, Any] | None = None
        latency_ms = 0.0
        cost = 0.0
        attempts = 0
        try:
            for attempt in range(2):
                data, latency_ms = await self.post(spec, body)
                attempts += 1
                cost += self._charge(kind, spec, data)
                try:
                    answers = validate_response(data, questions)
                except ValueError as e:
                    # 缺字段、答了清单外的东西：方案 7.3 说重试一次
                    if attempt == 0:
                        continue
                    raise JevError(f'Jev {kind}: malformed response: {e}') from e
                self._record(kind, spec, body, data, latency_ms, cost, escalated, attempts=attempts)
                return answers
        except JevError as e:
            self._record(kind, spec, body, data, latency_ms, cost, escalated, attempts=attempts, error=e)
            raise
        except LLMUnavailable as e:
            self._record(kind, spec, body, None, 0.0, 0.0, escalated, attempts=0, error=e)
            raise
        raise AssertionError('unreachable')

    def _charge(self, kind: str, spec: JevSpec, data: Mapping[str, Any]) -> float:
        if self.ledger is None:
            return 0.0
        return self.ledger.charge_jev(str(data.get('model', spec.model)), data.get('usage') or {}, kind=kind)

    def _record(
        self,
        kind: str,
        spec: JevSpec,
        body: Mapping[str, Any],
        data: Mapping[str, Any] | None,
        latency_ms: float,
        cost: float,
        escalated: bool,
        *,
        attempts: int,
        error: BaseException | None = None,
    ) -> None:
        if self.recorder is None:
            return
        model = str((data or {}).get('model', spec.model))
        self.recorder.record(
            kind=kind,
            provider=self.provider,
            model=model,
            request=body,
            response=data,
            usage=dict((data or {}).get('usage') or {}),
            latency_ms=latency_ms,
            cost=cost,
            escalated=escalated,
            error=error,
            attempts=attempts,
        )

    async def post(self, spec: JevSpec, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """发一次请求；超时、5xx、429、529 重试一次。返回 (响应 JSON, 毫秒)。"""
        key = self.settings.jev_api_key()
        if not key:
            names = [self.settings.jev.api_key_env, *self.settings.jev.api_key_env_fallbacks]
            raise LLMUnavailable(f'Jev API key not set (looked in env: {", ".join(names)})')
        if self.ledger is not None:
            self.ledger.check()
        url = f'{spec.base_url}/v1/systemone'
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        last_err: str = ''
        last_status: int | None = None
        async with self._bind_loop():
            for attempt in range(2):
                t0 = time.perf_counter()
                delay = self.backoff_s
                try:
                    r = await self._http().post(url, content=payload, headers=headers, timeout=spec.timeout_s)
                except httpx.TimeoutException:
                    last_err, last_status = 'timeout', None
                except httpx.TransportError as e:
                    last_err, last_status = f'transport error: {type(e).__name__}', None
                else:
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except ValueError:
                            last_err, last_status = 'response is not JSON', 200
                        else:
                            if isinstance(data, dict):
                                return data, latency_ms
                            last_err, last_status = 'response is not a JSON object', 200
                    elif r.status_code in _RETRY_STATUS:
                        last_err, last_status = f'HTTP {r.status_code}', r.status_code
                        ra = r.headers.get('retry-after')
                        if ra:
                            try:
                                delay = max(delay, float(ra))
                            except ValueError:
                                pass
                    else:
                        detail = r.text[:300]
                        raise JevError(f'Jev HTTP {r.status_code}: {detail}', status=r.status_code)
                if attempt == 0:
                    await asyncio.sleep(min(delay, self.max_backoff_s))
        raise JevError(f'Jev request failed after retry: {last_err}', status=last_status)
