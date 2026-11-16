"""随机试探（方案 11.2）：用 Mock 模型随机玩若干回合，盯住不变量、活性与各种大小。

玩家每回合的输入从三路里随机取一路：

- 选中三个选项之一（选项本身出自合法清单，其中有开口说话的）；
- 从 ``kernel.actions.legal_actions`` 的整张合法清单里随机取一个动作，直接结算；
- 一句自由输入，从评测数据（归类集、防作弊集）的样例里随机取。

玩家动弹不得时随便输入点什么（过去一个时辰）；被问「是否走出这一带」时随机答是或否。
收场了（死、走出地图）就从检查点重来；刚重来不久又收场，连着两次，就另开一局（种子另派）。
一局走过 ``max_days`` 个游戏日也另开一局，好让试探多落在开局后人多事多的那些日子里。
"""

from __future__ import annotations

import random
import shutil
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tianlong.kernel import actions
from tianlong.kernel.state import TICKS_PER_DAY
from tianlong.testing.driver import REPO, Driver, make_settings
from tianlong.testing.monitor import Monitor, plateau
from tianlong.testing.scan import ghost_events
from tianlong.testing.trace import Tracer

OPTION_KEYS = ("一", "二", "三")
HELPLESS_INPUT = "等着"
RESTORE = "/读档 检查点"


def load_free_texts(evals_dir: str | Path | None = None) -> list[str]:
    """归类集与防作弊集里的全部自由输入样例（去重，保持次序）。"""
    import yaml

    root = Path(evals_dir) if evals_dir else REPO / "evals" / "data"
    out: list[str] = []
    for name in ("classify.yaml", "anticheat.yaml"):
        path = root / name
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in data.get("items", []):
            if isinstance(item.get("input"), str):
                out.append(item["input"])
            for raw in item.get("inputs") or []:
                if isinstance(raw, str):
                    out.append(raw)
    seen: set[str] = set()
    uniq = []
    for t in out:
        t = t.strip()
        if t and t not in seen and not t.startswith("/"):
            seen.add(t)
            uniq.append(t)
    return uniq


@dataclass
class FuzzConfig:
    turns: int = 200
    p_option: float = 0.40
    p_legal: float = 0.35  # 余下的是自由输入
    sample_every: int = 25  # 每隔多少回合记一个大小的点
    quick_end: int = 30  # 读档后这么多回合之内又收场，算「刚重来又收场」
    max_days: int = 40  # 一局至多走这么多游戏日（0 不限）
    max_exceptions: int = 20
    keep_dirs: bool = False


@dataclass
class FuzzReport:
    seed: int
    turns: int = 0
    wall_s: float = 0.0
    games: int = 1
    restores: int = 0
    ticks: int = 0  # 各条时间线上走过的刻数之和
    hours: int = 0  # world.tick 的次数
    endings: dict[str, int] = field(default_factory=dict)
    channels: dict[str, int] = field(default_factory=dict)
    kinds: dict[str, int] = field(default_factory=dict)
    verbs: dict[str, int] = field(default_factory=dict)
    events: int = 0
    events_by_source: dict[str, int] = field(default_factory=dict)
    days_full: int = 0
    quiet_days: list[str] = field(default_factory=list)
    min_day_others: int | None = None  # 走完的日子里，非玩家引发的事件最少的一天有几条
    min_day_elsewhere: int | None = None  # 其中不在玩家眼前的最少有几条
    exceptions: list[str] = field(default_factory=list)
    invariant_failures: list[str] = field(default_factory=list)
    rollbacks: list[str] = field(default_factory=list)
    stalls: list[str] = field(default_factory=list)
    longest_idle: dict[str, int] = field(default_factory=dict)
    ghosts: list[str] = field(default_factory=list)
    max_memory: int = 0
    max_tail: int = 0
    max_save_bytes: int = 0
    max_rss_kb: int = 0
    save_plateau: dict[str, float] = field(default_factory=dict)
    curve: list[dict[str, Any]] = field(default_factory=list)
    size_problems: list[str] = field(default_factory=list)
    quiet_diag: list[dict[str, Any]] = field(default_factory=list)  # 发现「静日」那一刻的世界：谁在哪、在做什么

    @property
    def turns_per_s(self) -> float:
        return self.turns / self.wall_s if self.wall_s else 0.0

    def problems(self) -> list[str]:
        out = [f"exception: {e.splitlines()[-1] if e else e}" for e in self.exceptions]
        out += [f"invariant: {p}" for p in self.invariant_failures]
        out += [f"rollback: {p}" for p in self.rollbacks]
        out += [f"stall: {p}" for p in self.stalls]
        out += [f"quiet day: {p}" for p in self.quiet_days]
        out += [f"ghost: {p}" for p in self.ghosts]
        out += [f"size: {p}" for p in self.size_problems]
        return out

    @property
    def ok(self) -> bool:
        return not self.problems()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["turns_per_s"] = round(self.turns_per_s, 2)
        d["ok"] = self.ok
        return d

    def line(self) -> str:
        return (
            f"seed {self.seed:>3}: {self.turns} turns in {self.wall_s:6.1f}s ({self.turns_per_s:5.1f}/s)  "
            f"games {self.games} restores {self.restores} days {self.days_full}  "
            f"events {self.events}  inv {len(self.invariant_failures)} rb {len(self.rollbacks)} "
            f"exc {len(self.exceptions)} stall {len(self.stalls)} quiet {len(self.quiet_days)} ghost {len(self.ghosts)}  "
            f"mem≤{self.max_memory} tail≤{self.max_tail} save≤{self.max_save_bytes // 1024}KB rss≤{self.max_rss_kb // 1024}MB"
        )


def _world_brief(state: Any, turn: int, recent: list[str]) -> dict[str, Any]:
    """世界此刻的一张速写（诊断用）：玩家在哪，有魂、有名的人各在哪、手上是哪一步。"""
    people = []
    for c in state.chars.values():
        if c.tier not in ("soul", "named") or c.id == state.player:
            continue
        ag = c.agenda
        cur = ag.steps[ag.cursor] if ag is not None and ag.cursor < len(ag.steps) else None
        people.append(
            f"{c.id}@{c.loc}{'' if c.onstage else '(off)'}[{','.join(s.kind for s in c.statuses)}] "
            f"{ag.status if ag else '-'} {cur.id if cur else '-'} {ag.progress if ag else 0}/{cur.takes if cur else 0}"
            f"{' start ' + str(cur.start) if cur is not None and cur.start else ''}"
        )
    me = state.chars[state.player]
    return {
        "turn": turn,
        "tick": state.tick,
        "player": f"{me.loc}{'' if me.onstage else '(off)'}[{','.join(s.kind for s in me.statuses)}]",
        "situations": [f"{s.kind}@{s.loc}" for s in state.situations.values()],
        "people": people,
        "recent_inputs": list(recent),
    }


async def fuzz_seed(
    root: str | Path,
    seed: int,
    cfg: FuzzConfig | None = None,
    texts: list[str] | None = None,
) -> FuzzReport:
    """一个种子的随机试探。日志与存档写在 ``root`` 下（跑完即删，除非 ``cfg.keep_dirs``）。"""
    cfg = cfg or FuzzConfig()
    texts = texts if texts is not None else load_free_texts()
    root = Path(root)
    settings = make_settings(root)
    rng = random.Random(seed)
    rep = FuzzReport(seed=seed)
    mon = Monitor()
    channels: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    verbs: Counter[str] = Counter()
    endings: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    t0 = time.perf_counter()

    async def new_game(n: int) -> Driver:
        return await Driver.new(
            settings, seed=seed if n == 0 else seed * 1000 + n, run_id=f"fuzz-s{seed}-g{n}", keep_events=False
        )

    with Tracer(fingerprint=False) as tr:
        tr.hour_hooks.append(mon.on_hour)
        drv = await new_game(0)
        mon.after_turn(drv.state, drv.defs, drv.last_events)
        since_restore = 10**9
        quick = 0
        last_tick = drv.state.tick
        recent: list[str] = []
        seen_quiet = 0
        try:
            for i in range(cfg.turns):
                g = drv.game
                st = g.state
                # ---- 取这一回合的输入
                action = None
                if g._pending_leave is not None:
                    raw, ch = rng.choice(("是", "否")), "confirm"
                elif not g._can_act():
                    raw, ch = HELPLESS_INPUT, "helpless"
                else:
                    r = rng.random()
                    if r < cfg.p_option:
                        raw, ch = rng.choice(OPTION_KEYS), "option"
                    elif r < cfg.p_option + cfg.p_legal:
                        legal = actions.legal_actions(st, g.defs, st.player)
                        if legal:
                            action = rng.choice(legal)
                            raw, ch = action.signature, "legal"
                        else:
                            raw, ch = rng.choice(OPTION_KEYS), "option"
                    else:
                        raw, ch = rng.choice(texts) if texts else rng.choice(OPTION_KEYS), "text"
                channels[ch] += 1
                if action is not None:
                    verbs[action.verb] += 1
                # ---- 走一回合
                try:
                    if action is not None:
                        await drv.act(action)
                    else:
                        await drv.send(raw)
                except Exception:  # 「游戏从不抛异常」：记下来，接着试
                    rep.exceptions.append(f"turn {i} input {raw!r} tick {drv.state.tick}\n{traceback.format_exc(limit=8)}")
                    if len(rep.exceptions) >= cfg.max_exceptions:
                        break
                    continue
                st = drv.state
                rec = drv.game.last_turn
                if rec is not None:
                    kinds[rec.kind] += 1
                evs = drv.last_events
                rep.events += len(evs)
                for e in evs:
                    sources[e.source] += 1
                rep.ghosts += [f"[turn {i}] {p}" for p in ghost_events(evs, tr, drv.defs)]
                tr.reset_records()
                mon.after_turn(st, drv.defs, evs)
                recent.append(f"{i} t{st.tick} {ch} {raw[:40]} → {rec.kind if rec else '?'} ({len(evs)} ev)")
                del recent[:-40]
                n_quiet = len(mon.quiet_days())
                if n_quiet > seen_quiet:
                    seen_quiet = n_quiet
                    if len(rep.quiet_diag) < 3:
                        rep.quiet_diag.append(_world_brief(st, i, recent))
                if i % cfg.sample_every == 0:
                    mon.sample_sizes(st, drv.game.save_dir)
                rep.ticks += max(0, st.tick - last_tick)
                last_tick = st.tick
                since_restore += 1
                rep.turns = i + 1
                # ---- 收场了：从检查点重来，或另开一局
                too_long = cfg.max_days and st.tick >= cfg.max_days * TICKS_PER_DAY
                if st.ended is None and not too_long:
                    continue
                restart = bool(too_long)
                if st.ended is not None:
                    endings[st.ended] += 1
                    quick = quick + 1 if since_restore <= cfg.quick_end else 0
                    if quick >= 2:
                        restart = True
                if not restart:
                    mon.finish(st)
                    await drv.send(RESTORE)
                    mon.new_epoch(same_game=True)
                    tr.reset_records()
                    rep.restores += 1
                    since_restore = 0
                    restart = drv.state.ended is not None  # 没有可用的检查点
                if restart:
                    mon.finish(drv.state)
                    await drv.close()
                    drv = await new_game(rep.games)
                    rep.games += 1
                    mon.new_epoch()
                    tr.reset_records()
                    since_restore = 10**9
                    quick = 0
                mon.after_turn(drv.state, drv.defs, [])
                last_tick = drv.state.tick
        finally:
            mon.finish(drv.state)
            mon.sample_sizes(drv.state, drv.game.save_dir)
            await drv.close()
    rep.wall_s = time.perf_counter() - t0
    rep.hours = mon.hours
    rep.endings = dict(endings)
    rep.channels = dict(channels)
    rep.kinds = dict(kinds)
    rep.verbs = dict(verbs.most_common())
    rep.events_by_source = dict(sources)
    full = mon.full_days()
    rep.days_full = len(full)
    rep.quiet_days = [f"day {d.day} (epoch {d.epoch}, {d.events} events, all by the player)" for d in mon.quiet_days()]
    rep.min_day_others = min((d.by_others for d in full), default=None)
    rep.min_day_elsewhere = min((d.elsewhere for d in full), default=None)
    rep.invariant_failures = mon.invariant_failures
    rep.rollbacks = mon.rollbacks
    rep.stalls = [f"{s.char} idle {s.ticks} ticks ({s.since} → {s.until}, epoch {s.epoch})" for s in mon.stalls]
    rep.longest_idle = dict(mon.idle_spans.most_common(8))
    rep.max_memory = mon.max_memory
    rep.max_tail = mon.max_tail
    rep.curve = mon.curve
    rep.max_save_bytes = max((r["save_bytes"] for r in mon.curve), default=0)
    rep.max_rss_kb = max((r["rss_kb"] for r in mon.curve), default=0)
    rep.save_plateau = plateau(mon.curve, "save_bytes")
    rep.size_problems = mon.size_problems()
    if not cfg.keep_dirs:
        shutil.rmtree(root / "runs", ignore_errors=True)
        shutil.rmtree(root / "saves", ignore_errors=True)
    return rep


__all__ = ["FuzzConfig", "FuzzReport", "fuzz_seed", "load_free_texts"]
