"""盯住一局游戏（方案 11.2）：不变量、活性、每日的动静、内存与存档的大小。

``after_turn`` 每次输入之后调用；``on_hour`` 每个时辰（``world.tick`` 之后）调用一次——
两处都采活性的样，所以「无事可做超过一日」是按时辰量的，不是按回合量的。
读档或另开一局之后调用 ``new_epoch``：时间线断了，按日的统计与停摆的计时都重新开始。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tianlong.kernel import invariants, memory
from tianlong.kernel.state import EVENTS_TAIL_MAX, TICKS_PER_DAY, TICKS_PER_HOUR, Event, WorldState

ROLLBACK = "rollback "
HOUR_SLACK = TICKS_PER_HOUR  # 「一个时辰内重新打算」的口径
FULL_DAY_MIN = 64  # 一日至少走过这么多刻、并且走到了日终，才算「走完了这一日」（开局在辰时，第一日只有 64 刻）


def rss_kb() -> int:
    """本进程此刻占的内存（KB）；读不到就是 0。"""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") // 1024
    except (OSError, ValueError, IndexError):
        return 0


def dir_bytes(path: Path) -> int:
    total = 0
    if path.is_dir():
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    return total


@dataclass
class DayStat:
    epoch: int
    day: int  # 第几日（从 1 起）
    first_tick: int
    last_tick: int
    events: int = 0
    by_player: int = 0  # 玩家自己引发的
    by_others: int = 0  # 不是玩家引发的
    elsewhere: int = 0  # 不是玩家引发、也不在玩家眼前的（离场结算，或发生在别处）

    @property
    def covered(self) -> int:
        return self.last_tick - self.first_tick

    @property
    def full(self) -> bool:
        """这一局把这一日走完了（开局那天从辰时算起，也算）。"""
        hi = self.day * TICKS_PER_DAY
        return self.last_tick >= hi and self.covered >= FULL_DAY_MIN


@dataclass
class Stall:
    epoch: int
    char: str
    since: int
    until: int

    @property
    def ticks(self) -> int:
        return self.until - self.since


@dataclass
class Monitor:
    """累计一局（或一串读档重来的局）的检查结果。``problems`` 为空即通过硬性的几条。"""

    max_idle: int = TICKS_PER_DAY  # 活性：无事可做至多这么久（11.2 是一日）
    invariant_failures: list[str] = field(default_factory=list)  # invariants.check 直接查出的
    rollbacks: list[str] = field(default_factory=list)  # violations 里的回滚记录
    stalls: list[Stall] = field(default_factory=list)  # 超过 max_idle 的
    idle_spans: Counter = field(default_factory=Counter)  # 每个人最长的一次无事可做（刻）
    days: dict[tuple[int, int], DayStat] = field(default_factory=dict)
    curve: list[dict[str, Any]] = field(default_factory=list)  # 大小随回合数的曲线
    max_memory: int = 0
    max_tail: int = 0
    max_violations: int = 0
    samples: int = 0
    hours: int = 0
    turns: int = 0
    epoch: int = 0
    _idle_since: dict[str, int] = field(default_factory=dict)
    _seen_violations: set[str] = field(default_factory=set)
    _last_tick: int | None = None
    _player_loc: str | None = None

    # ---- 时间线

    def new_epoch(self, *, same_game: bool = False) -> None:
        """读档（``same_game``）或另开一局：时间线断了。

        读档读回来的 ``violations`` 是已经看过的那些的前一段，所以认过的记录留着，免得把同一次回滚数两遍。
        """
        self.epoch += 1
        self._idle_since = {}
        self._last_tick = None
        if not same_game:
            self._seen_violations = set()

    # ---- 活性（每个时辰、每个回合各采一次样）

    def sample_liveness(self, state: WorldState, defs: Any) -> list[str]:
        now = state.tick
        stalled = set(invariants.liveness(state, defs))
        for cid in stalled:
            self._idle_since.setdefault(cid, now)
        for cid in list(self._idle_since):
            since = self._idle_since[cid]
            span = now - since
            if span > self.idle_spans[cid]:
                self.idle_spans[cid] = span
            if cid not in stalled:
                del self._idle_since[cid]
                if span > self.max_idle:
                    self.stalls.append(Stall(self.epoch, cid, since, now))
        self.samples += 1
        return sorted(stalled)

    def on_hour(self, state: WorldState, defs: Any) -> None:
        self.hours += 1
        self.sample_liveness(state, defs)

    def _close_idle(self, now: int) -> None:
        for cid, since in self._idle_since.items():
            if now - since > self.max_idle:
                self.stalls.append(Stall(self.epoch, cid, since, now))
        self._idle_since = {}

    # ---- 每回合

    def after_turn(self, state: WorldState, defs: Any, events: list[Event], *, save_dir: Path | None = None) -> None:
        self.turns += 1
        pid = state.player
        for p in invariants.check(state, defs):
            self.invariant_failures.append(f"[turn {self.turns} tick {state.tick}] {p}")
        for v in state.violations:
            if v in self._seen_violations:
                continue
            self._seen_violations.add(v)
            if ROLLBACK in v:
                self.rollbacks.append(f"[turn {self.turns}] {v}")
        self.max_violations = max(self.max_violations, len(state.violations))
        self.sample_liveness(state, defs)
        # 每日的动静
        me = state.chars.get(pid)
        ploc = me.loc if me is not None and me.onstage else None
        start = self._last_tick if self._last_tick is not None else state.tick
        for t in {start, state.tick} | {e.tick for e in events}:
            d = self._day(t)
            d.first_tick = min(d.first_tick, t)
            d.last_tick = max(d.last_tick, t)
        if self._last_tick is not None:  # 这一回合跨过的整日也算走过了
            for dn in range(start // TICKS_PER_DAY + 1, state.tick // TICKS_PER_DAY + 1):
                lo = dn * TICKS_PER_DAY
                d = self._day(lo)
                d.first_tick = min(d.first_tick, lo)
                prev = self._day(lo - 1)
                prev.last_tick = max(prev.last_tick, lo)
        for e in events:
            d = self._day(e.tick)
            d.events += 1
            if e.source == "player" or e.actor == pid:
                d.by_player += 1
            else:
                d.by_others += 1
                if e.offscreen or e.loc != ploc:
                    d.elsewhere += 1
        self._last_tick = state.tick
        self._player_loc = ploc
        # 大小的上限
        mem = max((len(c.memory) for c in state.chars.values()), default=0)
        self.max_memory = max(self.max_memory, mem)
        self.max_tail = max(self.max_tail, len(state.events_tail))
        if save_dir is not None:
            self.sample_sizes(state, save_dir)

    def _day(self, tick: int) -> DayStat:
        dn = tick // TICKS_PER_DAY + 1
        key = (self.epoch, dn)
        d = self.days.get(key)
        if d is None:
            d = self.days[key] = DayStat(self.epoch, dn, tick, tick)
        return d

    def sample_sizes(self, state: WorldState, save_dir: Path) -> dict[str, Any]:
        """记一个点：存档有多大、各个只增不减的表有多长、进程占多少内存。"""
        state_file = save_dir / "state.json"
        row = {
            "turn": self.turns,
            "epoch": self.epoch,
            "tick": state.tick,
            "save_bytes": state_file.stat().st_size if state_file.is_file() else 0,
            "save_dir_bytes": dir_bytes(save_dir),
            "rss_kb": rss_kb(),
            "memory_max": max((len(c.memory) for c in state.chars.values()), default=0),
            "memory_total": sum(len(c.memory) for c in state.chars.values()),
            "events_tail": len(state.events_tail),
            "next_event_id": state.next_event_id,
            "facts": len(state.facts),
            "requests": len(state.requests),
            "promises": len(state.promises),
            "threads": len(state.threads),
            "chronicle": len(state.chronicle),
            "scheduled": len(state.scheduled),
            "flags": len(state.flags),
            "violations": len(state.violations),
            "beliefs_total": sum(len(c.beliefs) for c in state.chars.values()),
        }
        self.curve.append(row)
        return row

    def finish(self, state: WorldState) -> None:
        self._close_idle(state.tick)

    # ---- 结论

    def full_days(self) -> list[DayStat]:
        return [d for d in self.days.values() if d.full]

    def quiet_days(self) -> list[DayStat]:
        """整日走完、却没有任何非玩家引发的事件的日子。"""
        return [d for d in self.full_days() if d.by_others == 0]

    def size_problems(self) -> list[str]:
        out = []
        if self.max_memory > memory.MAX_MEMORIES:
            out.append(f"a character holds {self.max_memory} memories (limit {memory.MAX_MEMORIES})")
        if self.max_tail > EVENTS_TAIL_MAX:
            out.append(f"events_tail reached {self.max_tail} (limit {EVENTS_TAIL_MAX})")
        return out

    def problems(self) -> list[str]:
        out = [f"invariant: {p}" for p in self.invariant_failures]
        out += [f"rollback: {p}" for p in self.rollbacks]
        out += [f"stall: {s.char} idle {s.ticks} ticks ({s.since} → {s.until}, epoch {s.epoch})" for s in self.stalls]
        out += [f"quiet day: day {d.day} (epoch {d.epoch}) has no event not caused by the player" for d in self.quiet_days()]
        out += self.size_problems()
        return out


def plateau(curve: list[dict[str, Any]], key: str = "save_bytes") -> dict[str, float]:
    """大小有没有封顶：比较前半程与后半程的最大值，并给出后半程每千回合的涨幅。"""
    pts = [(r["turn"], r[key]) for r in curve if r.get(key)]
    if len(pts) < 4:
        return {"n": len(pts), "first_half_max": 0.0, "second_half_max": 0.0, "ratio": 1.0, "slope_per_1000": 0.0}
    half = len(pts) // 2
    a, b = pts[:half], pts[half:]
    amax = max(v for _, v in a)
    bmax = max(v for _, v in b)
    # 后半程的最小二乘斜率
    n = len(b)
    mx = sum(t for t, _ in b) / n
    my = sum(v for _, v in b) / n
    den = sum((t - mx) ** 2 for t, _ in b) or 1.0
    slope = sum((t - mx) * (v - my) for t, v in b) / den
    return {
        "n": len(pts),
        "first_half_max": float(amax),
        "second_half_max": float(bmax),
        "ratio": bmax / amax if amax else 1.0,
        "slope_per_1000": slope * 1000.0,
    }


__all__ = ["Monitor", "DayStat", "Stall", "plateau", "rss_kb", "dir_bytes", "HOUR_SLACK"]
