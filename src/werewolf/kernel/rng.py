"""可重放的随机数。内核、每个心智、导演各用一条独立的流，互不影响。

    rng = Rng(seed)                 # 内核自己的流
    mind_rng = rng.fork('mind:3')   # 派生：只取决于 (seed, 父标签, 新标签)，不取决于父流抽过几次

流的状态可以存取（`state()` / `Rng.from_state()`），所以存档后续局，后面的随机数与不存档时相同。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping, MutableSequence, Sequence
from typing import Any, TypeVar

T = TypeVar('T')


def _derive(seed: int, label: str) -> int:
    digest = hashlib.blake2b(f'{seed}\x1f{label}'.encode(), digest_size=8).digest()
    return int.from_bytes(digest, 'big')


class Rng:
    def __init__(self, seed: int, label: str = '') -> None:
        self.seed = int(seed)
        self.label = label
        self._r = random.Random(_derive(self.seed, label))

    def fork(self, label: str) -> Rng:
        """派生一条独立的流。同样的 (seed, label 链) 永远得到同样的流。"""
        return Rng(self.seed, f'{self.label}/{label}' if self.label else label)

    def random(self) -> float:
        return self._r.random()

    def randint(self, a: int, b: int) -> int:
        """[a, b] 闭区间。"""
        return self._r.randint(a, b)

    def uniform(self, a: float, b: float) -> float:
        return self._r.uniform(a, b)

    def chance(self, p: float) -> bool:
        return self._r.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise IndexError('Rng.choice: empty sequence')
        return seq[self._r.randrange(len(seq))]

    def shuffle(self, items: MutableSequence[Any]) -> None:
        self._r.shuffle(items)

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        return self._r.sample(list(seq), k)

    def weighted(self, weights: Mapping[str, float]) -> str:
        """按权重取一个键；键按传入的顺序排布。权重全为 0 或为空时报错。"""
        items = [(k, float(w)) for k, w in weights.items() if w > 0]
        if not items:
            raise ValueError('Rng.weighted: no positive weight')
        total = sum(w for _, w in items)
        u = self._r.random() * total
        acc = 0.0
        for k, w in items:
            acc += w
            if u < acc:
                return k
        return items[-1][0]

    # ---- 存取状态（JSON 原生） ----
    def state(self) -> dict[str, Any]:
        version, internal, gauss = self._r.getstate()
        return {'seed': self.seed, 'label': self.label, 'version': version, 'internal': list(internal), 'gauss': gauss}

    @classmethod
    def from_state(cls, data: Mapping[str, Any]) -> Rng:
        rng = cls(int(data['seed']), str(data['label']))
        rng._r.setstate((int(data['version']), tuple(data['internal']), data['gauss']))
        return rng
