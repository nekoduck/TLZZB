"""记账与预算护栏（方案 7.4 第 12 条、7.13、10.3 `[limits]`）。

单价取自配置 `[prices]`，单位是美元每百万 token；按模型 id 里的子串分档
（haiku / sonnet / opus / jev）。Batch 一律半价。缓存写入：5 分钟档用 `cache_write`，
1 小时档按输入单价的 2 倍算（配置里只给了 5 分钟档的价）。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from tianlong.config import Price, Settings
from tianlong.llm.errors import BudgetExceeded
from tianlong.llm.specs import model_family

log = logging.getLogger(__name__)

WARN_FRACTION = 0.8
_M = 1_000_000.0


def _get(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, Mapping):
        v = usage.get(name)
    else:
        v = getattr(usage, name, None)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _cache_creation_split(usage: Any) -> tuple[int, int] | None:
    """(5 分钟档, 1 小时档) 的缓存写入 token；usage 里没有细分时返回 None。"""
    cc = usage.get('cache_creation') if isinstance(usage, Mapping) else getattr(usage, 'cache_creation', None)
    if cc is None:
        return None
    m5 = _get(cc, 'ephemeral_5m_input_tokens')
    h1 = _get(cc, 'ephemeral_1h_input_tokens')
    if m5 == 0 and h1 == 0:
        return None
    return m5, h1


class Ledger:
    """累计本局花费；到八成提醒一次，到顶后再发付费请求就抛 `BudgetExceeded`。"""

    def __init__(
        self,
        prices: Mapping[str, Price],
        budget_usd: float,
        *,
        default_ttl: str = '5m',
        on_warn: Callable[[str], None] | None = None,
    ) -> None:
        self.prices = dict(prices)
        self.budget_usd = float(budget_usd)
        self.default_ttl = default_ttl
        self.on_warn = on_warn
        self.spent = 0.0
        self.warned = False
        self.by_kind: dict[str, float] = defaultdict(float)
        self.calls_by_kind: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, *, on_warn: Callable[[str], None] | None = None) -> Ledger:
        return cls(
            settings.prices,
            settings.limits.session_budget_usd,
            default_ttl=settings.cache.ttl,
            on_warn=on_warn,
        )

    # ---- 价格 ----
    def price_for(self, model: str) -> Price:
        fam = model_family(model)
        p = self.prices.get(fam)
        if p is None:
            log.warning('no price for model %r (family %r); counting it as free', model, fam)
            return Price()
        return p

    def cost_jev(self, model: str, usage: Any) -> float:
        p = self.price_for(model)
        return _get(usage, 'input_tokens') * p.input / _M + _get(usage, 'output_tokens') * p.output / _M

    def cost_claude(self, model: str, usage: Any, *, batch: bool = False, ttl: str | None = None) -> float:
        p = self.price_for(model)
        inp = _get(usage, 'input_tokens')
        out = _get(usage, 'output_tokens')
        c_read = _get(usage, 'cache_read_input_tokens')
        c_write = _get(usage, 'cache_creation_input_tokens')
        read_price = p.cache_read if p.cache_read is not None else p.input * 0.1
        write_5m = p.cache_write if p.cache_write is not None else p.input * 1.25
        write_1h = p.input * 2.0
        split = _cache_creation_split(usage)
        if split is not None:
            m5, h1 = split
            write_cost = m5 * write_5m + h1 * write_1h
        else:
            ttl = ttl or self.default_ttl
            write_cost = c_write * (write_1h if ttl == '1h' else write_5m)
        total = (inp * p.input + out * p.output + c_read * read_price + write_cost) / _M
        return total * (0.5 if batch else 1.0)

    # ---- 记账 ----
    def add(self, kind: str, cost: float) -> float:
        with self._lock:
            self.spent += cost
            self.by_kind[kind] += cost
            self.calls_by_kind[kind] += 1
            crossed = not self.warned and self.budget_usd > 0 and self.spent >= WARN_FRACTION * self.budget_usd
            if crossed:
                self.warned = True
        if crossed:
            msg = f'本局模型花费已到预算的八成：${self.spent:.2f} / ${self.budget_usd:.2f}'
            log.warning(msg)
            if self.on_warn is not None:
                self.on_warn(msg)
        return cost

    def charge_jev(self, model: str, usage: Any, *, kind: str = 'jev') -> float:
        return self.add(kind, self.cost_jev(model, usage))

    def charge_claude(
        self, kind: str, model: str, usage: Any, *, batch: bool = False, ttl: str | None = None
    ) -> float:
        return self.add(kind, self.cost_claude(model, usage, batch=batch, ttl=ttl))

    # ---- 护栏 ----
    @property
    def remaining(self) -> float:
        return self.budget_usd - self.spent

    @property
    def fraction(self) -> float:
        return self.spent / self.budget_usd if self.budget_usd > 0 else 0.0

    def check(self) -> None:
        """发付费请求之前调用。到顶就抛 `BudgetExceeded`，由游戏暂停并询问玩家。"""
        if self.budget_usd > 0 and self.spent >= self.budget_usd:
            raise BudgetExceeded(self.spent, self.budget_usd)

    def extend(self, extra_usd: float) -> None:
        """玩家同意继续：加预算。"""
        with self._lock:
            self.budget_usd += float(extra_usd)
            if self.spent < WARN_FRACTION * self.budget_usd:
                self.warned = False

    def summary(self) -> dict[str, Any]:
        return {
            'spent_usd': round(self.spent, 6),
            'budget_usd': self.budget_usd,
            'by_kind': {k: round(v, 6) for k, v in sorted(self.by_kind.items())},
            'calls_by_kind': dict(sorted(self.calls_by_kind.items())),
        }
