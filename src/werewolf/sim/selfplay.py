"""自对弈：`python -m werewolf sim`（ARCHITECTURE 第 10、11 节）。

三种模式：
- `rules`：不调模型。默认走真正的会话层 + NpcAgent（离线 Gateway）；`--engine arena` 改用同步的
  `sim.arena`（快，但不经过会话层）。
- `jev`：会话层 + 真 Jev（选动作、选方案、夜间反思）；台词走模板，不写独白。
- `full`：会话层 + 真 Jev + Claude 写台词、Jev 核对、Claude 独白。
所有局都是托管（autopilot、send=None、instant 档），真人座位交给 NpcAgent。
`--compare` 在同一批种子上跑 rules 与 jev（或 `--mode` 给的那一档）并给出差值。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from tianlong.config import Settings
from tianlong.llm import LLM, make_llm
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import GameConfig, Role, Seat
from werewolf.llm.config import WerewolfSettings, load
from werewolf.llm.gateway import Gateway
from werewolf.mind.types import MindTuning, Persona
from werewolf.server.npc import NpcAgent
from werewolf.server.session import Session
from werewolf.sim import arena
from werewolf.sim.stats import GameRecord, from_arena, from_session, report_md, summarize

MODES = ('rules', 'jev', 'full')


def ww_for(mode: str, ww: WerewolfSettings, *, jev_weight: float | None = None) -> WerewolfSettings:
    """按模式改路由：jev 模式台词走模板、不写独白；rules 模式靠离线 Gateway，不必改。"""
    route = ww.route
    if mode == 'jev':
        route = route.model_copy(update={'speech': 'template', 'monologue': 'off'})
    mind = ww.mind if jev_weight is None else ww.mind.model_copy(update={'jev_weight': jev_weight})
    return ww.model_copy(update={'route': route, 'mind': mind})


def host_seat(seed: int, human_seat: Seat | None) -> Seat:
    return human_seat if human_seat is not None else Rng(seed, 'sim').fork('seat').randint(1, 9)


def play_arena(seed: int, *, human_seat: Seat | None = None, tuning: MindTuning | None = None,
               checks: bool = False) -> GameRecord:
    t0 = time.perf_counter()
    res = arena.play(seed, human_seat=host_seat(seed, human_seat), tuning=tuning, check_kernel=checks,
                     check_mind=checks)
    rec = from_arena(res, seed)
    rec.wall_s = time.perf_counter() - t0
    return rec


async def play_session(seed: int, gateway: Gateway, ww: WerewolfSettings, *, mode: str = 'rules',
                       human_seat: Seat | None = None, human_role: Role | None = None, check_mind: bool = False,
                       run_dir: Path | None = None) -> tuple[GameRecord, Session]:
    """一局托管的会话（真人座位也是 NpcAgent）。返回统计记录与会话本身。"""

    hs = host_seat(seed, human_seat)
    cfg = GameConfig(human_seat=hs)

    def factory(seat: Seat, role: Role, persona: Persona, rng: Rng) -> NpcAgent:
        return NpcAgent(seat, role, persona, rng, gateway, ww, cfg, check_mind=check_mind)

    t0 = time.perf_counter()
    spent0 = gateway.spent()
    n0 = len(gateway.llm.recorder.records)
    s = Session(gateway, ww, human_seat=hs, human_role=human_role, seed=seed, speed='instant', send=None,
                run_dir=run_dir, autopilot=True, player_factory=factory)
    await s.run()
    for p in s.players.values():
        if isinstance(p, NpcAgent):
            await p.aclose()
    calls = list(gateway.llm.recorder.records[n0:])
    rec = from_session(s, seed, mode, calls=calls, cost=gateway.spent() - spent0, wall_s=time.perf_counter() - t0)
    return rec, s


async def run_batch(mode: str, seeds: Sequence[int], settings: Settings, ww: WerewolfSettings, *,
                    human_seat: Seat | None = None, engine: str = 'session', parallel: int = 1,
                    budget_usd: float = 1.0, check_mind: bool = False,
                    on_game: Callable[[GameRecord], None] | None = None,
                    llm_factory: Callable[[], LLM] | None = None) -> list[GameRecord]:
    """跑一批。模型模式下每局一个 Gateway（各自记账与录制），总花费到 `budget_usd` 就不再开新局。
    `llm_factory` 给测试换替身模型用（缺省是真客户端）。"""
    if mode == 'rules' and engine == 'arena':
        out = []
        for sd in seeds:
            rec = play_arena(sd, human_seat=human_seat, tuning=ww.mind, checks=check_mind)
            out.append(rec)
            if on_game:
                on_game(rec)
        return out
    wmode = ww_for(mode, ww)
    sem = asyncio.Semaphore(max(1, parallel))
    spent = [0.0]
    results: dict[int, GameRecord] = {}

    async def one(sd: int) -> None:
        async with sem:
            if mode != 'rules' and spent[0] >= budget_usd:
                return
            llm = llm_factory() if llm_factory is not None else make_llm(settings, mock=False, run_dir=None)
            llm.recorder.keep_in_memory = True
            gw = Gateway(settings, wmode, offline=mode == 'rules', llm=llm)
            try:
                rec, _ = await play_session(sd, gw, wmode, mode=mode, human_seat=human_seat, check_mind=check_mind)
            finally:
                spent[0] += gw.spent()
                await gw.aclose()
            results[sd] = rec
            if on_game:
                on_game(rec)

    await asyncio.gather(*(one(sd) for sd in seeds))
    return [results[sd] for sd in seeds if sd in results]


def samples(records: Sequence[GameRecord], k: int = 8, tier: str = 'claude') -> list[str]:
    out: list[str] = []
    for rec in records:
        for p, t, text in zip(rec.plans, rec.tiers, rec.texts, strict=False):
            if t == tier and len(out) < k:
                out.append(f'种子 {rec.seed}，第 {p.day} 天 {p.seat} 号（{rec.roles[p.seat].value}，{p.label}）：{text}')
    return out


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog='python -m werewolf sim', description='自对弈与统计')
    ap.add_argument('--games', type=int, default=20)
    ap.add_argument('--mode', choices=MODES, default='rules')
    ap.add_argument('--compare', action='store_true', help='同一批种子再跑一遍 rules（或 jev），给出差值')
    ap.add_argument('--human-seat', type=int, default=None)
    ap.add_argument('--seed', type=int, default=1, help='第一局的种子；之后依次加一')
    ap.add_argument('--report', type=Path, default=None, help='把 markdown 报告写到这里')
    ap.add_argument('--engine', choices=('session', 'arena'), default='session', help='rules 模式走哪条路')
    ap.add_argument('--parallel', type=int, default=0, help='同时跑几局（默认：rules 1，模型模式 4）')
    ap.add_argument('--budget', type=float, default=1.0, help='模型模式的总花费上限（美元）')
    ap.add_argument('--jev-weight', type=float, default=None, help='覆盖 MindTuning.jev_weight')
    ap.add_argument('--config', default='werewolf.toml')
    ap.add_argument('--check', action='store_true', help='每步跑心智不变量（慢）')
    return ap


def _ticker(mode: str, total: int) -> Callable[[GameRecord], None]:
    done = [0]

    def tick(rec: GameRecord) -> None:
        done[0] += 1
        print(f'[{mode}] {done[0]}/{total} seed={rec.seed} winner={rec.winner} days={rec.days} '
              f'cost=${rec.cost:.4f} {rec.wall_s:.1f}s', file=sys.stderr, flush=True)

    return tick


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    settings, ww = load(args.config)
    if args.jev_weight is not None:
        ww = ww_for('rules', ww, jev_weight=args.jev_weight)
    modes = [args.mode]
    if args.compare:
        modes = ['rules', 'jev' if args.mode == 'rules' else args.mode]
    seeds = list(range(args.seed, args.seed + args.games))
    summaries: dict[str, dict[str, float]] = {}
    all_recs: dict[str, list[GameRecord]] = {}
    for mode in modes:
        if mode != 'rules':
            probe = Gateway(settings, ww, offline=False)
            if not probe.jev_usable():
                print(f'{mode} 模式需要 Jev 的密钥；跳过。', file=sys.stderr)
                continue
        par = args.parallel or (1 if mode == 'rules' else 4)
        recs = asyncio.run(run_batch(mode, seeds, settings, ww, human_seat=args.human_seat, engine=args.engine,
                                     parallel=par, budget_usd=args.budget, check_mind=args.check,
                                     on_game=_ticker(mode, len(seeds))))
        all_recs[mode] = recs
        summaries[mode] = summarize(recs)
    if not summaries:
        return 2
    title = f'自对弈 {args.games} 局（{" vs ".join(summaries)}，种子 {seeds[0]}–{seeds[-1]}）'
    notes = [f'jev_weight = {ww.mind.jev_weight}', f'引擎：{args.engine if args.mode == "rules" else "session"}']
    text = report_md(title, summaries, samples=samples(all_recs.get('full', [])), notes=notes)
    print(text)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding='utf-8')
    return 0
