"""验收用的测试辅助（方案 11.2、11.4）：驱动一局游戏、盯住世界、给内核入口装探针、扫事件日志。

只给测试与 ``scripts/fuzz.py`` 用，游戏本身不 import 它。这里不出现人名、地名、剧情：
场景脚本写在 ``tests/acceptance/`` 里。

- ``driver.Driver``：包住 ``game.Game``——发输入、直接结算合法清单里的一个动作、经效果布景、拨时间，
  并把每回合的事件收成一份完整的事件日志。
- ``monitor.Monitor``：每回合、每个时辰检查不变量、活性、每日的动静与各种大小（11.2）。
- ``trace.Tracer``：给 ``kernel.resolve`` 的几个入口装探针，记下每条事件出自哪一步、哪个情境，
  动手之前前提是否成立；并核对「除了内核入口，没有别的东西改过世界」（11.4 的三条保证）。
- ``scan``：按探针的记录扫事件日志（幽灵事件、回弹、死者是否还在行动）。
- ``fuzz``：随机试探的主循环（``scripts/fuzz.py`` 与测试共用）。
"""

from tianlong.testing.driver import Driver, make_settings
from tianlong.testing.fuzz import FuzzConfig, FuzzReport, fuzz_seed, load_free_texts
from tianlong.testing.monitor import Monitor
from tianlong.testing.scan import (
    dead_actors,
    ghost_events,
    guarantees,
    rebound_events,
    stall_problems,
)
from tianlong.testing.trace import Tracer

__all__ = [
    "Driver",
    "FuzzConfig",
    "FuzzReport",
    "Monitor",
    "Tracer",
    "dead_actors",
    "fuzz_seed",
    "ghost_events",
    "guarantees",
    "load_free_texts",
    "make_settings",
    "rebound_events",
    "stall_problems",
]
