"""夜间反思与内心独白（ARCHITECTURE 6.2）。

反思：Jev 对每个座位打一个「是狼的可能性」分数，换算成概率；`deltas` = 估计 − 当前值。
独白：Claude 写几句第一人称的内心话，只进落盘文件与复盘；任何失败都返回 None。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Literal

from tianlong.llm.errors import LLMError
from werewolf.kernel.types import Seat
from werewolf.llm.gateway import Gateway
from werewolf.mind.types import Persona, ReflectionResult

JEV_KIND = 'J3_reflect'
LEVELS = ['几乎不可能是狼', '不太像狼', '说不准', '比较像狼', '几乎肯定是狼']
P_MIN, P_MAX = 0.02, 0.98


def level_to_prob(score: float, n_levels: int = len(LEVELS)) -> float:
    """分数（0 到 n-1，按概率加权的级数）换算成是狼的概率，夹在 [P_MIN, P_MAX]。"""
    top = max(1, n_levels - 1)
    return min(P_MAX, max(P_MIN, float(score) / top))


async def reflect(gw: Gateway, state: str, seats: Sequence[Seat], current: Mapping[Seat, float],
                  basis: Literal['private', 'public']) -> ReflectionResult | None:
    if not seats or gw.route('reflect') != 'jev':
        return None
    questions = {
        f'wolf_{s}': {'type': 'score', 'instructions': f'{s}号是狼人的可能性有多大？', 'criteria': LEVELS}
        for s in seats
    }
    try:
        ans = await gw.ask_jev(JEV_KIND, state, questions, timeout_s=gw.ww.limits.reflect_timeout_s)
        est = {s: level_to_prob(ans[f'wolf_{s}'].score) for s in seats}  # type: ignore[attr-defined]
    except (LLMError, TimeoutError, ValueError, KeyError, AttributeError):
        return None
    deltas = {s: est[s] - float(current.get(s, 1 / 3)) for s in seats}
    top = max(est, key=lambda s: est[s])
    return ReflectionResult(deltas=deltas, focus=top if est[top] >= 0.5 else None, basis=basis, source='jev')


_MONO_SYS = (
    '你在玩一局九人狼人杀。现在是夜里，你独自在心里把局面过一遍。'
    '直接写出一小段第一人称的内心独白，不要先分析、不要列步骤：你怎么看各个座位、接下来打算怎么做。'
    '口语，短句，不超过80字，座位写「N号」，不加标题、引号和括号，不要提到自己是AI或模型。'
)


async def monologue(gw: Gateway, state: str, persona: Persona) -> str | None:
    if gw.route('monologue') != 'claude':
        return None
    persona_text = (f'你叫{persona.name}，{persona.title}。{persona.bio}\n'
                    + ('你的取舍：' + '；'.join(persona.choices) if persona.choices else ''))
    try:
        parts: list[str] = []

        async def run() -> None:
            async for piece in gw.llm.claude.stream_text(
                    'W_monologue', [_MONO_SYS, persona_text], state,
                    mock_text='（夜里，我把今天的发言又想了一遍。）'):
                parts.append(piece)

        await asyncio.wait_for(run(), gw.ww.limits.monologue_timeout_s)
    except (LLMError, TimeoutError, ValueError):
        return None
    text = ''.join(parts).strip()
    return text or None
