"""基线跑（方案 11.3）：不放玩家，世界自己走若干日。

- 开局先经效果把玩家移出地图（``leave``），人人都算离场。
- ``lucky=True``：设内核旗标 ``__lucky__``（判定一律成功，D-079）；重新打算只用规则分，
  离场的 say 用规则答复（没给 decide 时），也就是「判定取成功，抉择取 ★」。
- 每个时辰：``advance_time`` 8 刻，``world.tick``，情境的 turns 加一；然后查不变量与活性。

返回 ``RunReport``：每个参照点何时达成、是否赶在 ``by`` 之前；不变量失败；停摆（活性不满足超过一日）；
事件计数。基线跑只是内容的验收手段，运行时没有任何东西要求世界照它走。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tianlong.kernel import checks, invariants, predicates, resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, TICKS_PER_HOUR, Ctx, WorldState
from tianlong.world import situations
from tianlong.world.clock import tick
from tianlong.world.common import LOG, Decide


@dataclass
class MarkResult:
    id: str
    text: str
    by: int | None
    reached: int | None = None  # 第一次成立的时刻

    @property
    def on_time(self) -> bool:
        return self.reached is not None and (self.by is None or self.reached <= self.by)


@dataclass
class RunReport:
    days: int
    start_tick: int
    end_tick: int = 0
    lucky: bool = False
    marks: dict[str, MarkResult] = field(default_factory=dict)
    invariant_failures: list[str] = field(default_factory=list)
    stalls: list[tuple[str, int, int]] = field(default_factory=list)  # (人, 从, 到)
    events: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    by_verb: dict[str, int] = field(default_factory=dict)
    offscreen: int = 0
    replans: int = 0
    divergences: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def on_time(self) -> list[str]:
        return [m.id for m in self.marks.values() if m.on_time]

    @property
    def late(self) -> list[str]:
        return [m.id for m in self.marks.values() if m.reached is not None and not m.on_time]

    @property
    def missed(self) -> list[str]:
        return [m.id for m in self.marks.values() if m.reached is None]

    def summary(self) -> str:
        lines = [
            f"基线跑 {self.days} 日（{'走运' if self.lucky else '真骰子'}），刻 {self.start_tick} → {self.end_tick}",
            f"参照点：按时 {len(self.on_time)}，迟到 {len(self.late)}，未达成 {len(self.missed)}，共 {len(self.marks)}",
        ]
        for m in self.marks.values():
            how = "按时" if m.on_time else ("迟到" if m.reached is not None else "未达成")
            lines.append(f"  {m.id:<18} by {m.by!s:>5}  reached {m.reached!s:>5}  {how}  {m.text}")
        lines.append(f"事件 {self.events}（离场 {self.offscreen}）；来源 {dict(self.by_source)}；重新打算 {self.replans} 次")
        lines.append(f"不变量失败 {len(self.invariant_failures)}；停摆 {len(self.stalls)}；异常 {len(self.errors)}")
        for s in self.stalls[:20]:
            lines.append(f"  停摆 {s[0]}：{s[1]} → {s[2]}")
        for f in self.invariant_failures[:20]:
            lines.append(f"  不变量 {f}")
        return "\n".join(lines)


def _head(c: Any) -> tuple[str, str] | None:
    """一份打算的标识：第一步的 id 与 origin（换了打算，它就变了）。"""
    ag = c.agenda
    if ag is None or not ag.steps:
        return None
    return ag.steps[0].id, ag.steps[0].origin


def _marks_check(state: WorldState, defs: Defs, report: RunReport) -> None:
    for m in report.marks.values():
        if m.reached is not None:
            continue
        mark = next(x for x in defs.baseline if x.id == m.id)
        try:
            if predicates.holds(state, defs, mark.holds, Ctx()):
                m.reached = state.tick
        except predicates.KernelError as e:
            report.errors.append(f"mark {m.id}: {e}")


async def run_days(
    llm: Any,
    state: WorldState,
    defs: Defs,
    days: int,
    *,
    lucky: bool = False,
    decide: Decide | None = None,
) -> RunReport:
    """不放玩家，让世界自己走 ``days`` 日。原地改写 ``state``。"""
    report = RunReport(days=days, start_tick=state.tick, lucky=lucky)
    for m in defs.baseline:
        report.marks[m.id] = MarkResult(id=m.id, text=m.text, by=m.by)
    setup: list[dict] = [{"leave": state.player}]
    if lucky:
        setup.append({"set_flag": [checks.LUCKY_FLAG, True]})
    resolve.run_effects(state, defs, setup, source="rule")
    _marks_check(state, defs, report)
    stalled_since: dict[str, int] = {}
    seen_stall: set[tuple[str, int]] = set()
    counts: Counter[str] = Counter()
    verbs: Counter[str] = Counter()
    last_event = state.next_event_id - 1
    hours = days * (TICKS_PER_DAY // TICKS_PER_HOUR)
    heads = {cid: _head(c) for cid, c in state.chars.items()}
    for _ in range(hours):
        evs = timeflow.advance_time(state, defs, TICKS_PER_HOUR)
        try:
            evs += await tick(llm, state, defs, decide=decide, rules_only=lucky)
        except Exception as e:  # 基线跑要跑完：记下来，接着走
            LOG.exception("world tick failed at %s", state.tick)
            report.errors.append(f"tick {state.tick}: {type(e).__name__}: {e}")
        situations.end_of_turn(state, defs)
        for ev in state.events_tail:
            if ev.id <= last_event:
                continue
            last_event = ev.id
            report.events += 1
            counts[ev.source] += 1
            verbs[ev.verb] += 1
            if ev.offscreen:
                report.offscreen += 1
        for cid, c in state.chars.items():
            h = _head(c)
            if h != heads.get(cid):
                heads[cid] = h
                if h is not None and h[1] == "replan":
                    report.replans += 1
        problems = invariants.check(state, defs)
        report.invariant_failures += [f"[{state.tick}] {p}" for p in problems]
        _marks_check(state, defs, report)
        now_stalled = set(invariants.liveness(state, defs))
        for cid in now_stalled:
            stalled_since.setdefault(cid, state.tick)
        for cid in list(stalled_since):
            if cid not in now_stalled:
                start = stalled_since.pop(cid)
                if state.tick - start > TICKS_PER_DAY and (cid, start) not in seen_stall:
                    report.stalls.append((cid, start, state.tick))
                    seen_stall.add((cid, start))
    for cid, start in stalled_since.items():
        if state.tick - start > TICKS_PER_DAY:
            report.stalls.append((cid, start, state.tick))
    report.end_tick = state.tick
    report.by_source = dict(counts)
    report.by_verb = dict(verbs.most_common(30))
    report.divergences = [d.mark for d in state.divergences]
    return report


__all__ = ["run_days", "RunReport", "MarkResult"]
