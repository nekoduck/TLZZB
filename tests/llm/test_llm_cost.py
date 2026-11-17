from __future__ import annotations

import pytest

from tianlong.config import Settings
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import BudgetExceeded


def test_jev_cost(settings: Settings) -> None:
    led = Ledger.from_settings(settings)
    assert led.cost_jev('jev-1.13.0', {'input_tokens': 1_000_000, 'output_tokens': 500}) == pytest.approx(0.042)


def test_claude_cost_with_cache(settings: Settings) -> None:
    led = Ledger.from_settings(settings)
    usage = {'input_tokens': 1000, 'output_tokens': 300, 'cache_read_input_tokens': 3000,
             'cache_creation_input_tokens': 2000}
    # Sonnet：2 / 10 / 0.10 / 2.50 每百万
    want = (1000 * 2.0 + 300 * 10.0 + 3000 * 0.10 + 2000 * 2.50) / 1e6
    assert led.cost_claude('claude-sonnet-5-5', usage) == pytest.approx(want)
    assert led.cost_claude('claude-sonnet-5-5', usage, batch=True) == pytest.approx(want / 2)
    # 1 小时缓存写入按输入单价 2 倍
    want_1h = (1000 * 2.0 + 300 * 10.0 + 3000 * 0.10 + 2000 * 4.0) / 1e6
    assert led.cost_claude('claude-sonnet-5-5', usage, ttl='1h') == pytest.approx(want_1h)


def test_cache_creation_split(settings: Settings) -> None:
    led = Ledger.from_settings(settings)
    usage = {'input_tokens': 0, 'output_tokens': 0, 'cache_creation_input_tokens': 300,
             'cache_creation': {'ephemeral_5m_input_tokens': 100, 'ephemeral_1h_input_tokens': 200}}
    want = (100 * 0.125 + 200 * 0.20) / 1e6  # Haiku：5m 0.125；1h = 2 × 0.10
    assert led.cost_claude('claude-haiku-5-5', usage) == pytest.approx(want)


def test_family_by_substring(settings: Settings) -> None:
    led = Ledger.from_settings(settings)
    u = {'input_tokens': 1_000_000, 'output_tokens': 0}
    assert led.cost_claude('claude-opus-5-5', u) == pytest.approx(4.0)
    assert led.cost_claude('claude-haiku-5-5', u) == pytest.approx(0.10)
    assert led.cost_claude('some-unknown-model', u) == 0.0


def test_budget_warn_and_stop(settings: Settings) -> None:
    warned: list[str] = []
    led = Ledger(settings.prices, 1.0, on_warn=warned.append)
    led.check()
    led.add('C3', 0.5)
    assert not warned
    led.add('C3', 0.31)
    assert len(warned) == 1 and '八成' in warned[0]
    led.add('C3', 0.1)
    assert len(warned) == 1  # 只提醒一次
    led.check()  # 0.91 < 1.0
    led.add('C2_soul', 0.1)
    with pytest.raises(BudgetExceeded) as ei:
        led.check()
    assert ei.value.spent == pytest.approx(1.01)
    led.extend(1.0)
    led.check()
    s = led.summary()
    assert s['by_kind']['C3'] == pytest.approx(0.91)
    assert s['calls_by_kind'] == {'C2_soul': 1, 'C3': 3}


def test_charge_helpers(settings: Settings) -> None:
    led = Ledger.from_settings(settings)
    c = led.charge_claude('C1', 'claude-haiku-5-5', {'input_tokens': 2000, 'output_tokens': 1000})
    assert c == pytest.approx((2000 * 0.10 + 1000 * 0.50) / 1e6)
    led.charge_jev('jev-1.13.0', {'input_tokens': 1000}, kind='J2')
    assert set(led.by_kind) == {'C1', 'J2'}
    assert led.spent == pytest.approx(c + 1000 * 0.042 / 1e6)
