"""评测跑手（方案 10.4 的 ``python -m tianlong eval <套件>``；套件见 11.5、11.6、11.7 与 evals/README.md）。

    from tianlong import evalrun
    code = evalrun.main(settings, 'classify', mock=True, limit=5)      # 打印表，写 evals/results/<套件>.json
    res = await evalrun.run_suite(cfg, 'checks')                        # 只跑、不写

``all`` 依次跑全部套件并重出 evals/report.md；``report`` 只按已有的结果文件重出报告。
Mock（``--mock``）下结果写在 evals/results/mock/，不覆盖真模型的结果。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tianlong.config import Settings
from tianlong.evalrun import (
    checks,
    classify,
    decisions,
    gameplay,
    knowledge,
    latency,
    perspective,
    report,
    zh_en,
)
from tianlong.evalrun.common import EvalConfig, print_result, write_result

Runner = Callable[[EvalConfig], Awaitable[dict[str, Any]]]

SUITES: dict[str, tuple[Runner, str]] = {
    "classify": (classify.run, "归类集 300 条：规则、J1、C1 三条路的各字段准确率与 7.12 的闸"),
    "decisions": (decisions.run, "抉择集 80 条：Jev（及 Haiku、Sonnet）与原著做法的一致率"),
    "checks": (checks.run, "核对集 100 句：J5 的检出率、误杀率与门槛扫描"),
    "zh_en": (zh_en.run, "中英问法对比（归类 60 条、抉择、核对）与重复 5 次的稳定性"),
    "anticheat": (gameplay.run_anticheat, "防作弊集 40 条：整回合之后的世界状态"),
    "refusals": (gameplay.run_refusals, "拒绝集 20 条：请求裁决与答复不越界"),
    "knowledge": (knowledge.run, "知识边界 100 问：人物说得出口的事实不越出他所知"),
    "perspective": (perspective.run, "视角：Mock 玩 200 回合，叙述与选项的字符串检查"),
    "latency_cost": (latency.run, "时延与成本：真 Jev 玩 30 回合"),
}
ALIASES = {"narration": "checks", "narration_checks": "checks", "latency": "latency_cost", "cost": "latency_cost"}
SPECIAL = ("all", "report")


def suite_names() -> list[str]:
    return [*SUITES, *SPECIAL]


def resolve(name: str) -> str | None:
    name = ALIASES.get(name, name)
    return name if name in SUITES or name in SPECIAL else None


async def run_suite(cfg: EvalConfig, name: str) -> dict[str, Any]:
    """跑一个套件，返回结果（不打印、不写文件）。"""
    return await SUITES[name][0](cfg)


async def run(cfg: EvalConfig, name: str) -> int:
    """跑一个套件（或 all / report），打印表、写结果文件。返回进程的退出码：0 正常，1 有套件跑崩。"""
    if name == "report":
        cfg.echo(f"报告：{report.write(cfg)}")
        return 0
    names = list(SUITES) if name == "all" else [name]
    results: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for n in names:
        try:
            res = await run_suite(cfg, n)
        except Exception as e:  # noqa: BLE001 — all 里一个套件跑崩，其余照跑
            if name != "all":
                raise
            failed.append(n)
            cfg.echo(f"== eval {n}：跑崩了（{type(e).__name__}: {e}）")
            continue
        path = write_result(cfg, res)
        results[n] = res
        print_result(cfg, res)
        cfg.echo(f"结果：{path}")
        cfg.echo("")
    if name == "all":
        # 没跑成的套件若有旧结果，报告里照旧列出
        merged = {**report.load_results(cfg), **results} if cfg.lang == "zh" else results
        cfg.echo(f"报告：{report.write(cfg, merged)}")
    return 1 if failed else 0


def main(settings: Settings, suite: str | None, *, mock: bool = False, limit: int | None = None, lang: str = "zh",
         model: str | None = None, out: str | Path | None = None, repeat: int = 5, parallel: int = 8,
         echo: Callable[[str], None] = print) -> int:
    """命令行入口（``__main__.cmd_eval`` 调它）。套件名不对时列出可用的套件，返回 2。"""
    name = resolve(suite) if suite else None
    if name is None:
        if suite:
            echo(f"没有这个评测套件：{suite}")
        echo("用法：python -m tianlong eval <套件> [--mock] [--limit N] [--lang zh|en] [--model jev-…]")
        echo("可用的套件：")
        for n, (_fn, desc) in SUITES.items():
            echo(f"  {n:<13}{desc}")
        echo("  all          依次跑全部套件，并重出 evals/report.md")
        echo("  report       只按已有的结果文件重出 evals/report.md")
        return 2
    cfg = EvalConfig(settings=settings, mock=mock, limit=limit, lang=lang, model=model,
                     out_dir=Path(out) if out else None, repeat=repeat, parallel=parallel, echo=echo)
    return asyncio.run(run(cfg, name))


__all__ = ["SUITES", "EvalConfig", "main", "resolve", "run", "run_suite", "suite_names"]
