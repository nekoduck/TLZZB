"""调用代号 → 用哪个模型、什么参数（方案 7.1、10.3）。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from tianlong.config import ModelSpec, Settings

Provider = Literal['jev', 'claude']

JEV_KINDS: frozenset[str] = frozenset({'J1', 'J2', 'J3', 'J4', 'J5', 'J6'})
# J2 分两步发（方案 7.3）；子代号与 J2 共用配置，只是录制时分开记。
_JEV_KIND_RE = re.compile(r'^J[1-6]([ab]|_[a-z0-9_]+)?$')
_PIPELINE_RE = re.compile(r'^P[1-6]([a-z_0-9]*)?$')
STREAM_KINDS: frozenset[str] = frozenset({'C3', 'C7'})

# 方案 7.1：C2 分有魂、有名两套。只写 'C2' 时按有魂处理（宁可多花一点，不可答得差）。
_ALIASES: dict[str, str] = {'C2': 'C2_soul'}


class JevSpec(BaseModel):
    kind: str
    provider: Provider = 'jev'
    model: str
    base_url: str
    timeout_s: float
    lang: str


class ClaudeSpec(BaseModel):
    kind: str
    provider: Provider = 'claude'
    config_key: str
    model: str
    effort: str
    max_tokens: int
    timeout_s: float
    thinking: str | None = None
    batch: bool = False
    stream: bool = False


def is_jev_kind(kind: str) -> bool:
    return bool(_JEV_KIND_RE.match(kind))


def config_key(kind: str) -> str:
    """Claude 调用代号对应 `[models.*]` 的哪一节。"""
    kind = _ALIASES.get(kind, kind)
    if _PIPELINE_RE.match(kind) or kind == 'pipeline':
        return 'pipeline'
    return kind


def jev_spec(settings: Settings, kind: str) -> JevSpec:
    if not is_jev_kind(kind):
        raise KeyError(f'not a Jev call kind: {kind!r}')
    j = settings.jev
    return JevSpec(kind=kind, model=j.model, base_url=j.base_url.rstrip('/'), timeout_s=j.timeout_s, lang=j.lang)


def claude_spec(settings: Settings, kind: str) -> ClaudeSpec:
    if is_jev_kind(kind):
        raise KeyError(f'{kind!r} is a Jev call kind, not a Claude one')
    key = config_key(kind)
    m: ModelSpec | None = settings.models.get(key)
    if m is None:
        raise KeyError(f'no [models.{key}] section in config for call kind {kind!r}')
    return ClaudeSpec(
        kind=kind,
        config_key=key,
        model=m.model,
        effort=m.effort,
        max_tokens=m.max_tokens,
        timeout_s=m.timeout_s,
        thinking=m.thinking,
        batch=m.batch,
        stream=key in STREAM_KINDS,
    )


def spec_for(settings: Settings, kind: str) -> JevSpec | ClaudeSpec:
    return jev_spec(settings, kind) if is_jev_kind(kind) else claude_spec(settings, kind)


def model_family(model: str) -> str:
    """按 id 里的子串定价格档：haiku / sonnet / opus / jev；认不出返回 'unknown'。"""
    m = model.lower()
    for fam in ('haiku', 'sonnet', 'opus', 'jev'):
        if fam in m:
            return fam
    return 'unknown'


def supports_between_tools(model: str) -> bool:
    """`thinking={'type': 'between_tools'}` 只有 Sonnet 5.5 接受。"""
    return 'sonnet-5-5' in model.lower()


def thinking_param(spec: ClaudeSpec) -> dict[str, str] | None:
    """按配置给出 `thinking` 参数；None 表示不传（5.5 系列默认自适应思考）。

    `between_tools` 只发给 C3 这类配置了它的调用，且只在 Sonnet 5.5、effort 不高于 high 时发；
    其他情形不传，退回默认的自适应思考。
    """
    if spec.thinking == 'between_tools':
        if supports_between_tools(spec.model) and spec.effort in ('low', 'medium', 'high'):
            return {'type': 'between_tools'}
        return None
    if spec.thinking == 'adaptive':
        return {'type': 'adaptive'}
    return None
