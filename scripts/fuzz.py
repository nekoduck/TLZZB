#!/usr/bin/env python
"""随机试探（方案 11.2）：用 Mock 模型随机玩，每个种子若干回合；不联网，不花钱。

    .venv/bin/python scripts/fuzz.py --seeds 20 --turns 2000          # 方案规定的全量
    .venv/bin/python scripts/fuzz.py --seeds 3 --turns 200 --jobs 1   # 与默认测试同样的规模

玩家每回合的输入随机取自：三个选项、整张合法清单里的任一动作、评测数据里的自由输入样例
（``evals/data/classify.yaml`` 与 ``anticheat.yaml``）。收场了从检查点重来或另开一局。

要求（任何一条不成立，退出码为 1）：

- 游戏从不抛异常；6.11 的不变量一次都不失败（``invariants.check`` 每回合查一遍；``violations`` 里没有回滚）。
- 世界不停摆：每个走完的游戏日都有非玩家引发的事件；没有哪个有魂、有名角色无事可做超过一日。
- 内存与存档不失控：每人的记忆不超过 200 条，事件尾巴不超过 200 条；存档大小的曲线写进报告。

每个种子的结果是一行 JSON（``--out``，默认 ``runs/fuzz/<时间>.jsonl``），末尾打印汇总；``--md`` 另写一份
Markdown 的汇总表。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def run_one(seed: int, turns: int, max_days: int, sample_every: int) -> dict[str, Any]:
    """一个种子（在子进程里跑）。"""
    from tianlong.testing import FuzzConfig, fuzz_seed, load_free_texts

    logging.disable(logging.CRITICAL)
    cfg = FuzzConfig(turns=turns, max_days=max_days, sample_every=sample_every)
    with tempfile.TemporaryDirectory(prefix=f"fuzz-s{seed}-") as tmp:
        rep = asyncio.run(fuzz_seed(tmp, seed, cfg, load_free_texts()))
    d = rep.to_dict()
    d["problems"] = rep.problems()
    d["line"] = rep.line()
    return d


def summarize(rows: list[dict[str, Any]], wall_s: float) -> dict[str, Any]:
    def total(key: str) -> int:
        return sum(int(r.get(key) or 0) for r in rows)

    def count(key: str) -> int:
        return sum(len(r.get(key) or []) for r in rows)

    mins = [r["min_day_others"] for r in rows if r.get("min_day_others") is not None]
    mins2 = [r["min_day_elsewhere"] for r in rows if r.get("min_day_elsewhere") is not None]
    idle = max((max(r["longest_idle"].values(), default=0) for r in rows), default=0)
    return {
        "seeds": len(rows),
        "turns": total("turns"),
        "wall_s": round(wall_s, 1),
        "cpu_s": round(sum(float(r["wall_s"]) for r in rows), 1),
        "turns_per_s": round(total("turns") / max(1e-9, sum(float(r["wall_s"]) for r in rows)), 2),
        "games": total("games"),
        "restores": total("restores"),
        "game_days": round(total("ticks") / 96, 1),
        "full_days": total("days_full"),
        "events": total("events"),
        "exceptions": count("exceptions"),
        "invariant_failures": count("invariant_failures"),
        "rollbacks": count("rollbacks"),
        "stalls": count("stalls"),
        "quiet_days": count("quiet_days"),
        "ghosts": count("ghosts"),
        "size_problems": count("size_problems"),
        "min_day_others": min(mins) if mins else None,
        "min_day_elsewhere": min(mins2) if mins2 else None,
        "longest_idle_ticks": idle,
        "max_memory": max((r["max_memory"] for r in rows), default=0),
        "max_tail": max((r["max_tail"] for r in rows), default=0),
        "max_save_kb": max((r["max_save_bytes"] for r in rows), default=0) // 1024,
        "max_rss_mb": max((r["max_rss_kb"] for r in rows), default=0) // 1024,
        "ok": all(r.get("ok") for r in rows),
    }


def curve_table(rows: list[dict[str, Any]], step: int) -> list[str]:
    """存档大小随回合数的曲线：各种子在同一回合数上的中位数与最大值（KB）。"""
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        for p in r.get("curve") or []:
            by_turn.setdefault((p["turn"] // step) * step, []).append(p)
    lines = [
        "| 回合 | 存档 KB（中位） | 存档 KB（最大） | 记忆条数合计（中位） | 请求 | 年谱 | 事实 | 进程内存 MB（最大） |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def med(xs: list[float]) -> float:
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0.0

    for t in sorted(by_turn):
        ps = by_turn[t]
        lines.append(
            f"| {t} | {med([p['save_bytes'] for p in ps]) / 1024:.0f} | {max(p['save_bytes'] for p in ps) / 1024:.0f} "
            f"| {med([p['memory_total'] for p in ps]):.0f} | {med([p['requests'] for p in ps]):.0f} "
            f"| {med([p['chronicle'] for p in ps]):.0f} | {med([p['facts'] for p in ps]):.0f} "
            f"| {max(p['rss_kb'] for p in ps) / 1024:.0f} |"
        )
    return lines


def markdown(rows: list[dict[str, Any]], summary: dict[str, Any], args: argparse.Namespace) -> str:
    out = [
        f"### 随机试探：{summary['seeds']} 个种子 × {args.turns} 回合（Mock 模型）",
        "",
        f"- 共 {summary['turns']} 回合，{summary['events']} 条事件，约 {summary['game_days']} 个游戏日"
        f"（其中走完的整日 {summary['full_days']} 个）；另开新局 {summary['games'] - summary['seeds']} 次，"
        f"从检查点重来 {summary['restores']} 次。",
        f"- 用时 {summary['wall_s']} 秒（{args.jobs} 个进程；单进程合计 {summary['cpu_s']} 秒，"
        f"{summary['turns_per_s']} 回合/秒）。",
        f"- 异常 {summary['exceptions']}；不变量失败 {summary['invariant_failures']}；回滚 {summary['rollbacks']}；"
        f"停摆（无事可做超过一日）{summary['stalls']}；没有非玩家事件的整日 {summary['quiet_days']}；"
        f"幽灵事件 {summary['ghosts']}。",
        f"- 走完的整日里，非玩家引发的事件最少的一天有 {summary['min_day_others']} 条"
        f"（其中不在玩家眼前的最少 {summary['min_day_elsewhere']} 条）；"
        f"有魂、有名角色最长的一次无事可做是 {summary['longest_idle_ticks']} 刻。",
        f"- 每人记忆至多 {summary['max_memory']} 条，事件尾巴至多 {summary['max_tail']} 条；"
        f"存档最大 {summary['max_save_kb']} KB，进程内存最大 {summary['max_rss_mb']} MB。",
        "",
        "| 种子 | 回合 | 秒 | 新局 | 重来 | 整日 | 事件 | 异常 | 不变量 | 回滚 | 停摆 | 静日 | 幽灵 | 存档 KB | 最长无事（刻） |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in sorted(rows, key=lambda r: r["seed"]):
        out.append(
            f"| {r['seed']} | {r['turns']} | {r['wall_s']:.0f} | {r['games'] - 1} | {r['restores']} | {r['days_full']} "
            f"| {r['events']} | {len(r['exceptions'])} | {len(r['invariant_failures'])} | {len(r['rollbacks'])} "
            f"| {len(r['stalls'])} | {len(r['quiet_days'])} | {len(r['ghosts'])} | {r['max_save_bytes'] // 1024} "
            f"| {max(r['longest_idle'].values(), default=0)} |"
        )
    out += ["", "存档大小随回合数的曲线（各种子在同一回合数上的统计）：", ""]
    out += curve_table(rows, max(args.sample_every, args.turns // 10 or 1))
    problems = [(r["seed"], p) for r in rows for p in r.get("problems") or []]
    if problems:
        out += ["", "问题（每个种子至多列 10 条）：", ""]
        shown: dict[int, int] = {}
        for seed, p in problems:
            shown[seed] = shown.get(seed, 0) + 1
            if shown[seed] <= 10:
                out.append(f"- 种子 {seed}：{p}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="随机试探（方案 11.2）：Mock 模型，不联网。")
    ap.add_argument("--seeds", type=int, default=3, help="几个种子（从 --seed-base 起连续取）")
    ap.add_argument("--seed-base", type=int, default=1)
    ap.add_argument("--turns", type=int, default=200, help="每个种子玩多少回合")
    ap.add_argument("--jobs", type=int, default=0, help="并行的进程数（0：按 CPU 数，至多等于种子数）")
    ap.add_argument("--max-days", type=int, default=40, help="一局至多走多少游戏日，到了就另开一局（0 不限）")
    ap.add_argument("--sample-every", type=int, default=25, help="每隔多少回合记一个大小的点")
    ap.add_argument("--out", default=None, help="每个种子一行 JSON 的结果文件")
    ap.add_argument("--md", default=None, help="另写一份 Markdown 汇总")
    args = ap.parse_args(argv)

    import os

    seeds = [args.seed_base + i for i in range(args.seeds)]
    args.jobs = args.jobs or min(len(seeds), os.cpu_count() or 1)
    out = Path(args.out) if args.out else REPO / "runs" / "fuzz" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    print(f"fuzz: {len(seeds)} seeds × {args.turns} turns, {args.jobs} jobs → {out}", flush=True)
    with out.open("w", encoding="utf-8") as f:

        def done(row: dict[str, Any]) -> None:
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(row["line"], flush=True)
            for p in (row.get("problems") or [])[:5]:
                print(f"    ! {p}", flush=True)

        if args.jobs <= 1:
            for s in seeds:
                done(run_one(s, args.turns, args.max_days, args.sample_every))
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futs = {pool.submit(run_one, s, args.turns, args.max_days, args.sample_every): s for s in seeds}
                for fut in as_completed(futs):
                    try:
                        done(fut.result())
                    except Exception as e:  # 子进程自己倒了：也算这个种子不过
                        s = futs[fut]
                        row = {
                            "seed": s, "turns": 0, "wall_s": 0.0, "games": 1, "restores": 0, "ticks": 0, "days_full": 0,
                            "events": 0, "exceptions": [f"worker crashed: {type(e).__name__}: {e}"],
                            "invariant_failures": [], "rollbacks": [], "stalls": [], "quiet_days": [], "ghosts": [],
                            "size_problems": [], "longest_idle": {}, "max_memory": 0, "max_tail": 0, "max_save_bytes": 0,
                            "max_rss_kb": 0, "curve": [], "ok": False,
                            "problems": [f"worker crashed: {type(e).__name__}: {e}"], "line": f"seed {s}: worker crashed: {e}",
                        }  # fmt: skip
                        done(row)
    summary = summarize(rows, time.perf_counter() - t0)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    md = markdown(rows, summary, args)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(md, encoding="utf-8")
    else:
        print(md)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
