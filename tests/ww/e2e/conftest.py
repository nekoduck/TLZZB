from __future__ import annotations


def pytest_configure(config) -> None:  # noqa: ANN001
    config.addinivalue_line('markers', 'e2e: 真浏览器（Playwright）整局，需要 node 与 playwright')
