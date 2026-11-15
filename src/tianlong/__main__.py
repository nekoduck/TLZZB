"""命令行（方案 10.4）。

    python -m tianlong play [--seed N] [--mock] [--replay runs/<id>] [--debug] [--name 名字]
                            [--save 名字] [--load 名字] [--plain]
    python -m tianlong pipeline <p0..p7|all> [--mock] [--direct]
    python -m tianlong baseline [--seeds 20] [--days 12] [--lucky] [--mock] [--strict]
    python -m tianlong eval <套件名>

``--config`` 指明 config.toml（默认取当前目录下的）。密钥只从环境变量与 .env 读，从不打印。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tianlong.config import Settings, load_settings

EVAL_SUITES_DIR = ("evals", "data")


# ---------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tianlong", description="天龙八部·无量山与万劫谷：终端里的纯文字沙盒")
    p.add_argument("--config", default="config.toml", help="config.toml 的路径（默认当前目录下的）")
    sub = p.add_subparsers(dest="command", metavar="命令")

    play = sub.add_parser("play", help="开一局，或接着玩一局")
    play.add_argument("--seed", type=int, default=None, help="随机种子（不给就取当前时刻）")
    play.add_argument("--mock", action="store_true", help="用确定性的替身模型，不联网、不花钱")
    play.add_argument("--replay", metavar="runs/<id>", default=None, help="按那一局录下的模型调用与输入逐字重放")
    play.add_argument("--debug", action="store_true", help="开 /debug 与调试用的作弊命令（都记进日志）")
    play.add_argument("--name", default=None, help="你的名字")
    play.add_argument("--save", default=None, metavar="名字", help="这一局自动存档用的名字（默认用本局的编号）")
    play.add_argument("--load", default=None, metavar="名字", help="读回 saves/<名字>/ 接着玩")
    play.add_argument("--plain", action="store_true", help="纯文本输出（不上色、不用行编辑）")
    play.add_argument("--width", type=int, default=None, help="纯文本输出的折行宽度")

    pipe = sub.add_parser("pipeline", help="离线内容管线 P0 至 P7（方案 9.1）")
    pipe.add_argument("step", help="p0 … p7，或 all")
    pipe.add_argument("--mock", action="store_true", help="用替身模型")
    pipe.add_argument("--direct", action="store_true", help="不走 Batch，直接调用")
    pipe.add_argument("--baseline", action="store_true", help="P7 里连带跑一遍走运的基线（方案 11.3）")

    base = sub.add_parser("baseline", help="基线跑：没有玩家，世界自己走（方案 11.3）")
    base.add_argument("--seeds", type=int, default=20, help="跑几个种子（从 0 起）")
    base.add_argument("--days", type=int, default=12, help="每个种子走几个游戏日")
    base.add_argument("--lucky", action="store_true", help="判定一律取成功，抉择取 ★（验内容的接线）")
    base.add_argument("--mock", action="store_true", help="用替身模型")
    base.add_argument("--strict", action="store_true", help="到期的参照点没有按时成立也算失败（M5 的闸）")

    ev = sub.add_parser("eval", help="方案第 11 节的评测")
    ev.add_argument("suite", nargs="?", default=None, help="套件名")
    return p


def _settings(args: argparse.Namespace) -> Settings:
    return load_settings(args.config)


# ---------------------------------------------------------------- play


def _replay_info(path: str) -> tuple[Path, dict[str, Any], list[str] | None]:
    """--replay 指的那一局：目录、meta.json（种子、名字）、录下的输入（没有录就是 None）。"""
    from tianlong.game import INPUTS_FILE, META_FILE

    run = Path(path)
    meta: dict[str, Any] = {}
    mp = run / META_FILE
    if mp.is_file():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    ip = run / INPUTS_FILE
    inputs = ip.read_text(encoding="utf-8").splitlines() if ip.is_file() else None
    return run, meta, inputs


def cmd_play(args: argparse.Namespace) -> int:
    from tianlong import store
    from tianlong.game import Game
    from tianlong.ui import repl

    settings = _settings(args)
    replay_dir: Path | None = None
    meta: dict[str, Any] = {}
    inputs: list[str] | None = None
    if args.replay:
        replay_dir, meta, inputs = _replay_info(args.replay)
        if not replay_dir.is_dir():
            print(f"找不到要重放的那一局：{replay_dir}", file=sys.stderr)
            return 2
    seed = args.seed if args.seed is not None else meta.get("seed")
    if seed is None:
        seed = int(time.time()) % 1_000_000
    name = args.name or meta.get("player_name") or None
    holder: dict[str, Any] = {}

    async def factory() -> Any:
        if args.load:
            game = await Game.load(settings, args.load, mock=args.mock, debug=args.debug, replay_dir=replay_dir)
        else:
            game = await Game.new(
                settings, seed=int(seed), mock=args.mock, player_name=name, replay_dir=replay_dir,
                debug=args.debug, save_name=args.save,
            )
        holder["game"] = game
        return game

    async def run() -> int:
        if inputs is not None:
            # 重放：录下的输入一条条喂回去，输出与原来那一局逐字相同
            return await repl.run(factory, settings, args, stdin=io.StringIO("\n".join(inputs) + "\n"))
        return await repl.run(factory, settings, args)

    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 130
    except store.SaveError as e:
        print(str(e), file=sys.stderr)
        return 2
    game = holder.get("game")
    if game is not None:
        print(f"本局存档：{game.save_name}（接着玩：--load {game.save_name}）；日志：{game.run_dir}", file=sys.stderr)
    return code


# ---------------------------------------------------------------- pipeline


def cmd_pipeline(args: argparse.Namespace) -> int:
    from tianlong import pipeline

    settings = _settings(args)
    kwargs: dict[str, Any] = {"mock": args.mock}
    if args.direct:
        kwargs["batch"] = False
    if args.baseline:
        kwargs["baseline"] = lambda content: _baseline_problems(settings, Path(content))
    return pipeline.run(args.step, settings, **kwargs)


async def _baseline_problems(settings: Settings, content: Path, *, days: int = 12) -> list[str]:
    """给 P7 的钩子：走运的基线跑一遍，返回问题清单（参照点没有按时成立、不变量失败、停摆）。"""
    row = await _baseline_one(settings, content, seed=0, days=days, lucky=True, mock=True)
    rep = row["report"]
    out = [f"参照点 {m.id} 没有按时成立（by {m.by}，reached {m.reached}）：{m.text}" for m in _overdue(rep)]
    out += [f"不变量：{f}" for f in rep.invariant_failures[:20]]
    out += [f"停摆：{s[0]}（{s[1]} → {s[2]}）" for s in rep.stalls[:20]]
    out += [f"异常：{e}" for e in rep.errors[:20]]
    return out


# ---------------------------------------------------------------- baseline


def _overdue(rep: Any) -> list[Any]:
    """已经到期（by 不晚于这一跑的终点）却没有按时成立的参照点。"""
    return [m for m in rep.marks.values() if not m.on_time and (m.by is None or m.by <= rep.end_tick)]


async def _baseline_one(
    settings: Settings, content: Path, *, seed: int, days: int, lucky: bool, mock: bool
) -> dict[str, Any]:
    from tianlong import minds, world
    from tianlong.content.loader import load
    from tianlong.llm import make_llm

    defs, state = load(content, seed=seed)
    llm = make_llm(settings, mock=mock, run_dir=None)
    def decide(st: Any, df: Any, cid: str) -> Any:
        return minds.decide_one(llm, st, df, cid, offscreen=True)

    try:
        t0 = time.perf_counter()
        report = await world.run_days(llm, state, defs, days, lucky=lucky, decide=None if lucky else decide)
        return {"seed": seed, "report": report, "seconds": time.perf_counter() - t0, "spent": llm.ledger.spent}
    finally:
        await llm.aclose()


def cmd_baseline(args: argparse.Namespace) -> int:
    settings = _settings(args)
    content = settings.path("content")
    seeds = list(range(max(1, int(args.seeds))))
    days = max(1, int(args.days))

    async def run() -> list[dict[str, Any]]:
        rows = []
        for seed in seeds:
            row = await _baseline_one(settings, content, seed=seed, days=days, lucky=args.lucky, mock=args.mock)
            rep = row["report"]
            print(
                f"种子 {seed:>3}：按时 {len(rep.on_time)}/{len(rep.marks)}，迟到 {len(rep.late)}，"
                f"未达成 {len(rep.missed)}；事件 {rep.events}；不变量失败 {len(rep.invariant_failures)}；"
                f"停摆 {len(rep.stalls)}；异常 {len(rep.errors)}（{row['seconds']:.1f}s）",
                flush=True,
            )
            rows.append(row)
        return rows

    mode = "走运（判定取成功，抉择取 ★）" if args.lucky else "真骰子"
    model = "替身模型" if args.mock else "真模型（没有密钥的调用退回规则）"
    print(f"基线跑：{len(seeds)} 个种子 × {days} 日，{mode}，{model}")
    rows = asyncio.run(run())
    reports = [r["report"] for r in rows]
    n = len(reports)
    print()
    print(f"{'参照点':<20}{'by':>6}{'按时':>8}{'迟到':>6}{'未达成':>7}  说明")
    for mid, first in reports[0].marks.items():
        ms = [rep.marks[mid] for rep in reports]
        on = sum(1 for m in ms if m.on_time)
        late = sum(1 for m in ms if m.reached is not None and not m.on_time)
        miss = sum(1 for m in ms if m.reached is None)
        print(f"{mid:<20}{first.by!s:>6}{on:>5}/{n:<3}{late:>5}{miss:>7}  {first.text}")
    inv = [(r["seed"], f) for r in rows for f in r["report"].invariant_failures]
    stalls = [(r["seed"], s) for r in rows for s in r["report"].stalls]
    errors = [(r["seed"], e) for r in rows for e in r["report"].errors]
    overdue = [(r["seed"], m) for r in rows for m in _overdue(r["report"])]
    print()
    print(f"不变量失败 {len(inv)}；停摆 {len(stalls)}；异常 {len(errors)}；到期而未按时成立的参照点 {len(overdue)}")
    for seed, f in inv[:20]:
        print(f"  不变量（种子 {seed}）{f}")
    for seed, s in stalls[:20]:
        print(f"  停摆（种子 {seed}）{s[0]}：{s[1]} → {s[2]}")
    for seed, e in errors[:20]:
        print(f"  异常（种子 {seed}）{e}")
    if args.lucky or args.strict:
        for seed, m in overdue[:40]:
            print(f"  参照点（种子 {seed}）{m.id}：by {m.by}，reached {m.reached}　{m.text}")
    spent = sum(float(r["spent"]) for r in rows)
    if spent:
        print(f"模型花费 ${spent:.2f}")
    failed = bool(inv or stalls or errors or (args.strict and overdue))
    print("结论：" + ("有问题" if failed else "通过"))
    return 1 if failed else 0


# ---------------------------------------------------------------- eval


def cmd_eval(args: argparse.Namespace) -> int:
    """评测跑手还没写（留给后来的模块）：这里只说明现状，列出现成的数据集。"""
    settings = _settings(args)
    data = settings.root.joinpath(*EVAL_SUITES_DIR)
    files = sorted(p.name for p in data.glob("*.yaml")) if data.is_dir() else []
    suite = args.suite or "（未指定）"
    print(f"eval {suite}: not implemented yet（评测跑手尚未实现）。")
    if files:
        print(f"现成的数据集（{data}，格式见 evals/README.md）：")
        for name in files:
            print(f"  {name.removesuffix('.yaml')}　{name}")
    else:
        print(f"没有找到数据集目录：{data}")
    return 2


# ---------------------------------------------------------------- 入口


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "play":
        return cmd_play(args)
    if args.command == "pipeline":
        return cmd_pipeline(args)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "eval":
        return cmd_eval(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
