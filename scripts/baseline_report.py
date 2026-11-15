#!/usr/bin/env python
"""基线跑的报告（方案 11.3）：不放玩家，世界自己走若干日，统计每个参照点成立的比例。

- 走运（lucky）：判定一律取成功，重新打算只用规则分。参照点应当全部按时成立，一次就够（没有骰子）。
- 真骰子：对若干个种子各跑一遍，列出每个参照点按时成立、迟到、未达成的次数。
  不要求全中；要求的是每个种子都跑得完，没有不变量失败，没有人停摆，没有异常。

默认用 Mock 的模型（不联网、可重放）。``--live`` 用 config.toml 与环境变量里配置的真模型。
基线跑只是内容的验收手段，运行时没有任何东西要求世界照它走。

    .venv/bin/python scripts/baseline_report.py                      # 走运一遍 + 真骰子种子 0..19，各 12 日
    .venv/bin/python scripts/baseline_report.py --seeds 5 --days 3   # 真骰子种子 0..4，各 3 日
    .venv/bin/python scripts/baseline_report.py --only lucky -v      # 只跑走运，并打印完整摘要

退出码：走运的参照点没有全部按时，或任何一遍有不变量失败、停摆、异常，为 1；否则为 0。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tianlong.config import load_settings  # noqa: E402
from tianlong.content.loader import load  # noqa: E402
from tianlong.content.validate import validate  # noqa: E402
from tianlong.kernel.state import TICKS_PER_DAY  # noqa: E402
from tianlong.llm import make_llm  # noqa: E402
from tianlong.world import RunReport, run_days  # noqa: E402


async def run_one(content: Path, settings, *, seed: int, days: int, lucky: bool, live: bool) -> RunReport:
    """跑一遍。每一遍重新读内容，状态互不相干；模型的录制放在临时目录里，跑完即弃。"""
    defs, state = load(content, seed=seed)
    with tempfile.TemporaryDirectory(prefix="tl-baseline-") as tmp:
        llm = make_llm(settings, mock=not live, run_dir=Path(tmp))
        try:
            return await run_days(llm, state, defs, days, lucky=lucky)
        finally:
            await llm.aclose()


def clean(rep: RunReport) -> bool:
    return not rep.invariant_failures and not rep.stalls and not rep.errors


def day_of(tick: int | None) -> str:
    return "—" if tick is None else f"{tick}（第{tick // TICKS_PER_DAY + 1}日）"


def lucky_table(rep: RunReport) -> list[str]:
    lines = ["| 参照点 | by | 成立于 | 结果 | 说明 |", "| --- | ---: | --- | --- | --- |"]
    for m in rep.marks.values():
        how = "按时" if m.on_time else ("迟到" if m.reached is not None else "未达成")
        lines.append(f"| {m.id} | {m.by} | {day_of(m.reached)} | {how} | {m.text} |")
    return lines


def real_table(reps: dict[int, RunReport]) -> list[str]:
    n = len(reps)
    first = next(iter(reps.values()))
    lines = [
        f"| 参照点 | by | 按时 | 迟到 | 未达成 | 按时比例 | 成立时刻的中位数 | 说明 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for mid, m0 in first.marks.items():
        ms = [r.marks[mid] for r in reps.values()]
        on = sum(1 for m in ms if m.on_time)
        late = sum(1 for m in ms if m.reached is not None and not m.on_time)
        miss = sum(1 for m in ms if m.reached is None)
        hit = sorted(m.reached for m in ms if m.reached is not None)
        median = hit[len(hit) // 2] if hit else None
        lines.append(
            f"| {mid} | {m0.by} | {on} | {late} | {miss} | {on / n:.0%} | {median if median is not None else '—'} | {m0.text} |"
        )
    return lines


def problems(label: str, rep: RunReport) -> list[str]:
    out: list[str] = []
    for f in rep.invariant_failures[:10]:
        out.append(f"  {label} 不变量失败：{f}")
    for cid, a, b in rep.stalls[:10]:
        out.append(f"  {label} 停摆：{cid} 从刻 {a} 到刻 {b}")
    for e in rep.errors[:10]:
        out.append(f"  {label} 异常：{e}")
    return out


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="基线跑报告（方案 11.3）")
    ap.add_argument("--content", default=str(ROOT / "content"), help="内容目录（默认仓库里的 content/）")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--days", type=int, default=12, help="每遍走几个游戏日（默认 12）")
    ap.add_argument("--seeds", type=int, default=20, help="真骰子跑几个种子（0..N-1，默认 20）")
    ap.add_argument("--first-seed", type=int, default=0)
    ap.add_argument("--only", choices=["lucky", "real"], default=None, help="只跑其中一种")
    ap.add_argument("--live", action="store_true", help="用真模型（要有密钥，会联网、会花钱）；默认 Mock")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每一遍的完整摘要")
    args = ap.parse_args(argv)

    content = Path(args.content)
    settings = load_settings(args.config)
    defs, state = load(content)
    issues = validate(defs, state)
    print(f"# 基线跑报告：{content}（{args.days} 日，{'真模型' if args.live else 'Mock 模型'}）")
    print(f"\n内容指纹 {defs.content_hash[:12]}；校验问题 {len(issues)} 条。")
    for p in issues[:20]:
        print(f"  校验：{p}")
    ok = not issues
    t0 = time.time()

    if args.only != "real":
        rep = await run_one(content, settings, seed=args.first_seed, days=args.days, lucky=True, live=args.live)
        print(f"\n## 走运（判定一律取成功）\n")
        print("\n".join(lucky_table(rep)))
        print(
            f"\n按时 {len(rep.on_time)} / {len(rep.marks)}；迟到 {len(rep.late)}；未达成 {len(rep.missed)}。"
            f"事件 {rep.events} 条，重新打算 {rep.replans} 次；"
            f"不变量失败 {len(rep.invariant_failures)}，停摆 {len(rep.stalls)}，异常 {len(rep.errors)}。"
        )
        for line in problems("走运", rep):
            print(line)
        if args.verbose:
            print("\n```text\n" + rep.summary() + "\n```")
        ok = ok and clean(rep) and len(rep.on_time) == len(rep.marks)

    if args.only != "lucky" and args.seeds > 0:
        reps: dict[int, RunReport] = {}
        for seed in range(args.first_seed, args.first_seed + args.seeds):
            reps[seed] = await run_one(content, settings, seed=seed, days=args.days, lucky=False, live=args.live)
        n = len(reps)
        seeds = f"{args.first_seed}..{args.first_seed + n - 1}"
        print(f"\n## 真骰子（种子 {seeds}，共 {n} 个）\n")
        print("\n".join(real_table(reps)))
        bad = [s for s, r in reps.items() if not clean(r)]
        full = sum(1 for r in reps.values() if len(r.on_time) == len(r.marks))
        avg = sum(len(r.on_time) for r in reps.values()) / n
        print(
            f"\n跑完 {n} / {n}；全部参照点按时的种子 {full} 个；平均每个种子按时 {avg:.1f} / {len(next(iter(reps.values())).marks)}。"
            f"不变量失败、停摆或异常的种子：{bad if bad else '无'}。"
        )
        print("\n| 种子 | 按时 | 迟到 | 未达成 | 事件 | 重新打算 | 不变量失败 | 停摆 | 异常 |")
        print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for seed, r in reps.items():
            print(
                f"| {seed} | {len(r.on_time)} | {len(r.late)} | {len(r.missed)} | {r.events} | {r.replans} "
                f"| {len(r.invariant_failures)} | {len(r.stalls)} | {len(r.errors)} |"
            )
        for seed, r in reps.items():
            for line in problems(f"种子 {seed}", r):
                print(line)
            if args.verbose:
                print(f"\n```text\n种子 {seed}\n" + r.summary() + "\n```")
        ok = ok and not bad

    print(f"\n用时 {time.time() - t0:.1f} 秒。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
