"""让 Jev 在候选里给概率（ARCHITECTURE 6.2）。所有失败都返回 None，调用方就只用规则。"""

from __future__ import annotations

from collections.abc import Mapping

from tianlong.llm.errors import LLMError
from tianlong.llm.specs import is_jev_kind
from werewolf.llm.gateway import Gateway

DEFAULT_KIND = 'J2_act'


async def choose(gw: Gateway, kind: str, state: str, question: str,
                 options: Mapping[str, str]) -> dict[str, float] | None:
    """返回选项 id → 概率（和为 1）；Jev 不可用、超时、出错都返回 None。

    `kind` 形如 'J2_act'、'J2_plan'、'J4_select'；不合 Jev 代号的按 'J2_act' 记。
    """
    if not options or gw.route('decide') != 'jev':
        return None
    ids = [str(k) for k in options]
    if len(ids) == 1:
        return {ids[0]: 1.0}
    kind = kind if is_jev_kind(kind) else DEFAULT_KIND
    questions = {'pick': {'type': 'choice', 'instructions': question,
                          'criteria': {str(k): str(v) for k, v in options.items()}}}
    try:
        ans = await gw.ask_jev(kind, state, questions, timeout_s=gw.ww.limits.decide_timeout_s)
        probs = ans['pick'].probabilities  # type: ignore[attr-defined]
    except (LLMError, TimeoutError, ValueError, KeyError, AttributeError):
        return None
    out = {i: max(0.0, float(probs.get(i, 0.0))) for i in ids}
    total = sum(out.values())
    if total <= 0:
        return None
    return {i: p / total for i, p in out.items()}
