"""可重放的随机数（ARCHITECTURE 2）。只用 ``state.seed`` 与 ``state.rng_counter``，每次取数计数器加一。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from tianlong.kernel.state import WorldState

_SPAN = float(1 << 64)


def _draw(state: WorldState) -> int:
    digest = hashlib.blake2b(f"{state.seed}:{state.rng_counter}".encode(), digest_size=8).digest()
    state.rng_counter += 1
    return int.from_bytes(digest, "big")


def roll(state: WorldState) -> int:
    """d100：1 到 100。"""
    return _draw(state) % 100 + 1


def uniform(state: WorldState) -> float:
    """[0, 1) 之间的数，给按概率取样的调用方用（llm.route.sample_choice 的 roll_fn）。"""
    return _draw(state) / _SPAN


def pick_weighted[K](state: WorldState, weights: Mapping[K, float]) -> K:
    """按权重取一个键；权重不大于 0 的键不参与。"""
    items = [(k, float(w)) for k, w in weights.items() if w > 0]
    if not items:
        raise ValueError("pick_weighted: no positive weight")
    total = sum(w for _, w in items)
    x = uniform(state) * total
    acc = 0.0
    for k, w in items:
        acc += w
        if x < acc:
            return k
    return items[-1][0]
