"""给内核的入口装探针（方案 11.4 的三条保证要用）。

``with Tracer() as tr:`` 期间，``kernel.resolve`` 的 ``apply`` / ``run_effects`` / ``settle_offscreen``、
``kernel.timeflow.advance_time``、``kernel.effects.tick_situations`` 与 ``world.tick`` 换成带记录的包装
（退出时原样换回）。上层模块都是按「模块.函数」调用它们的，所以包装对整局生效。探针只看不改：

1. **每条事件出自哪里，动手之前前提成立没有。** 在调用内核之前（世界还没被这次结算改动），独立地求一遍
   值：动作的人当前这一步打算的 ``pre``、``start``、``deadline``；将要开场的情境的 ``when``（带上绑好的
   角色位）；setpiece 所属的情境是否正在进行。调用返回后把结论挂到它产生的事件 id 上。
2. **除了内核入口，没有别的东西改过世界。** 每次最外层的入口返回时给「世界的实况」（人在哪、是死是活、
   带着什么、会什么、身份；东西在哪；旗标）取一个指纹，下次进入口时再取一次：两次之间变了，就是有人
   绕过内核直接改了状态。
3. **以 rule 为来源的效果调用都记下来**（哪些效果、哪一刻、是不是测试自己布的景），供「没有回弹」核对。
4. ``world.tick`` 每做完一次（一个时辰），调用登记的钩子（活性采样）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tianlong import world
from tianlong.kernel import effects, predicates, resolve, timeflow
from tianlong.kernel.state import Ctx, Event, WorldState


@dataclass(frozen=True)
class StepCheck:
    """一条带 ``step`` 的事件：那一步是谁的、什么来历、动手之前是否还该做。"""

    char: str
    step: str
    origin: str  # canon / replan / rule
    pending: bool  # 动手之前这一步还没做完、没作废
    pre_ok: bool | None  # 动手之前 pre 成立；None：没能在动手之前求值
    in_window: bool  # start 已到、deadline 未过
    entry: str

    @property
    def ok(self) -> bool:
        return self.pending and self.pre_ok is True and self.in_window


@dataclass(frozen=True)
class SitCheck:
    """一条出自情境的事件：哪种情境、什么来历、开场之前 ``when`` 是否成立。"""

    kind: str
    origin: str  # canon / generic
    how: str  # open（在玩家眼前开场）/ settle_new（不在眼前，开场即离场结算）/ settle_running / setpiece
    when_ok: bool  # 开场那一刻 when 成立；已在进行的情境：它确实在进行
    entry: str

    @property
    def ok(self) -> bool:
        return self.when_ok


@dataclass(frozen=True)
class RuleCall:
    """一次以 rule 为来源的效果调用。"""

    tick: int
    keys: tuple[str, ...]
    events: tuple[int, ...]
    cheat: bool  # 测试自己布的景，或 /debug 作弊
    during: str  # 发生在哪个外层入口里（如 timeflow/scheduled；最外层为空）
    at_event: int = 0  # 调用开始时下一条事件的 id（用来与分岔那条事件排先后）


@dataclass(frozen=True)
class ForeignWrite:
    tick: int
    before_entry: str  # 在进这个入口之前发现的
    diff: tuple[str, ...]


def world_fingerprint(state: WorldState) -> tuple:
    """世界的实况：人、物、旗标。只取「真发生了什么」的那部分，不含记账（回合数、选项、牵挂、随机数计数）。"""
    chars = tuple(
        (
            c.id,
            c.loc,
            c.onstage,
            tuple((s.kind, s.level) for s in c.statuses),
            tuple(c.inventory),
            tuple(c.roles),
            tuple(c.arts.items()),
            c.neili,
            c.superior,
        )
        for c in state.chars.values()
    )
    items = tuple((it.id, it.holder, it.loc, it.feature, it.in_world) for it in state.items.values())
    flags = frozenset((k, v) for k, v in state.flags.items() if isinstance(v, (bool, int, str)))
    return (chars, items, flags)


def _diff(a: tuple, b: tuple) -> tuple[str, ...]:
    out: list[str] = []
    for name, xs, ys in (("char", a[0], b[0]), ("item", a[1], b[1])):
        before = {x[0]: x for x in xs}
        for y in ys:
            x = before.get(y[0])
            if x != y:
                out.append(f"{name} {y[0]}: {x} → {y}")
    if a[2] != b[2]:
        out.append(f"flags: {sorted(map(str, a[2] ^ b[2]))}")
    return tuple(out[:6])


@dataclass
class Tracer:
    fingerprint: bool = True
    step_checks: dict[int, StepCheck] = field(default_factory=dict)
    sit_checks: dict[int, SitCheck] = field(default_factory=dict)
    entry_of: dict[int, str] = field(default_factory=dict)  # 事件 id → 出自哪个内核入口（外层/内层）
    rule_calls: list[RuleCall] = field(default_factory=list)
    foreign: list[ForeignWrite] = field(default_factory=list)
    calls: Counter = field(default_factory=Counter)
    hour_hooks: list[Callable[[WorldState, Any], None]] = field(default_factory=list)
    hours: int = 0
    _stack: list[str] = field(default_factory=list)
    _cheat: int = 0
    _last_fp: tuple | None = None
    _last_state: Any = None
    _orig: dict[tuple[Any, str], Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ 装与拆

    def __enter__(self) -> Tracer:
        self._patch(resolve, "apply", self._wrap_apply)
        self._patch(resolve, "run_effects", self._wrap_run_effects)
        self._patch(resolve, "settle_offscreen", self._wrap_settle)
        self._patch(timeflow, "advance_time", self._wrap_plain("timeflow"))
        for name in ("run_due_scheduled", "_run_scheduled"):  # 后一个是 world 借用的旧名
            if hasattr(timeflow, name):
                self._patch(timeflow, name, self._wrap_plain("scheduled"))
        self._patch(effects, "tick_situations", self._wrap_plain("tick_situations"))
        self._patch(world, "tick", self._wrap_world_tick)
        return self

    def __exit__(self, *exc: object) -> None:
        for (mod, name), fn in self._orig.items():
            setattr(mod, name, fn)
        self._orig.clear()

    def _patch(self, mod: Any, name: str, make: Callable[[Any], Any]) -> None:
        orig = getattr(mod, name)
        self._orig[(mod, name)] = orig
        setattr(mod, name, make(orig))

    def attach(self, driver: Any) -> None:
        """让探针知道哪些改动是测试自己做的：``driver.fx`` 布的景，``/debug``（拨时间除外）的作弊。"""
        fx, send = driver.fx, driver.send

        def traced_fx(*a: Any, **kw: Any) -> Any:
            self._cheat += 1
            try:
                return fx(*a, **kw)
            finally:
                self._cheat -= 1

        async def traced_send(raw: str) -> Any:
            cheat = raw.strip().startswith("/debug") and not raw.strip().startswith("/debug tick")
            self._cheat += int(cheat)
            try:
                return await send(raw)
            finally:
                self._cheat -= int(cheat)

        driver.fx = traced_fx
        driver.send = traced_send

    def reset_records(self) -> None:
        """清掉按事件 id 记的东西（读档之后事件 id 会回头；长跑时也免得越积越多）。指纹与计数不动。"""
        self.step_checks.clear()
        self.sit_checks.clear()
        self.entry_of.clear()
        self.rule_calls.clear()

    # ------------------------------------------------------------------ 入口的进出

    def _enter(self, state: WorldState, entry: str) -> None:
        self.calls[entry] += 1
        if not self._stack and self.fingerprint:
            fp = world_fingerprint(state)
            if self._last_fp is not None and self._last_state is state and fp != self._last_fp:
                self.foreign.append(ForeignWrite(state.tick, entry, _diff(self._last_fp, fp)))
        self._stack.append(entry)

    def _leave(self, state: WorldState, entry: str, evs: list[Event] | None) -> None:
        path = "/".join(self._stack)
        self._stack.pop()
        for ev in evs or ():
            self.entry_of.setdefault(ev.id, path)
        if not self._stack and self.fingerprint:
            self._last_fp = world_fingerprint(state)
            self._last_state = state

    @staticmethod
    def _holds(state: WorldState, defs: Any, pred: Any, ctx: Ctx) -> bool:
        try:
            return bool(predicates.holds(state, defs, pred, ctx))
        except predicates.KernelError:
            return False

    def _step_info(self, state: WorldState, defs: Any, cid: str | None, step_id: str | None, entry: str) -> StepCheck | None:
        """``cid`` 的打算里的一步（不给 ``step_id`` 就取当前这一步），在动手之前求值。"""
        c = state.chars.get(cid) if cid else None
        ag = c.agenda if c is not None else None
        if c is None or ag is None:
            return None
        st = None
        if step_id is None:
            if ag.status == "active" and ag.cursor < len(ag.steps):
                st = ag.steps[ag.cursor]
        else:
            st = next((s for s in ag.steps if s.id == step_id), None)
        if st is None:
            return None
        in_window = (st.start is None or state.tick >= st.start) and (st.deadline is None or state.tick <= st.deadline)
        return StepCheck(
            char=c.id,
            step=st.id,
            origin=st.origin,
            pending=st.state == "pending",
            pre_ok=self._holds(state, defs, st.pre, Ctx(self_id=c.id)),
            in_window=in_window,
            entry=entry,
        )

    def _mark_steps(self, state: WorldState, evs: list[Event], info: StepCheck | None, entry: str) -> None:
        for ev in evs or ():
            if ev.step is None or ev.id in self.step_checks:
                continue
            if info is not None and ev.step == info.step:
                self.step_checks[ev.id] = info
                continue
            # 这条事件挂的不是事先看过的那一步：事后只查得到来历，查不到「动手之前」
            origin = "?"
            owner = ev.actor or "?"
            for c in state.chars.values():
                if c.agenda is None:
                    continue
                st = next((s for s in c.agenda.steps if s.id == ev.step), None)
                if st is not None and (c.id == ev.actor or origin == "?"):
                    origin, owner = st.origin, c.id
            self.step_checks[ev.id] = StepCheck(owner, ev.step, origin, True, None, True, entry)

    # ------------------------------------------------------------------ 包装

    def _wrap_apply(self, orig: Any) -> Any:
        def apply(state: WorldState, defs: Any, action: Any, speech: Any = None, **kw: Any) -> list[Event]:
            info = self._step_info(state, defs, action.actor, None, "apply")
            sit: SitCheck | None = None
            if action.verb == "setpiece":
                sid = action.params.get("situation")
                s = state.situations.get(sid)
                kind = s.kind if s is not None else str(sid)
                d = defs.situations.get(kind)
                sit = SitCheck(kind, d.origin if d else "?", "setpiece", s is not None, "apply")
            self._enter(state, "apply")
            evs: list[Event] = []
            try:
                evs = orig(state, defs, action, speech, **kw)
                return evs
            finally:
                self._leave(state, "apply", evs)
                self._mark_steps(state, evs, info, "apply")
                if sit is not None:
                    for ev in evs:
                        self.sit_checks.setdefault(ev.id, sit)

        return apply

    def _wrap_run_effects(self, orig: Any) -> Any:
        def run_effects(
            state: WorldState, defs: Any, effs: list[dict], ctx: Ctx | None = None, **kw: Any
        ) -> list[Event]:
            source = kw.get("source")
            step = kw.get("step")
            info = None
            if step is not None:
                info = self._step_info(state, defs, ctx.self_id if ctx else None, step, "run_effects")
            sit: SitCheck | None = None
            if source == "situation":
                for e in effs:
                    if isinstance(e, dict) and "open" in e:
                        arg = e["open"]
                        kind = arg[0] if isinstance(arg, list) else arg
                        roles = arg[2] if isinstance(arg, list) and len(arg) > 2 else {}
                        d = defs.situations.get(kind)
                        ok = d is not None and self._holds(state, defs, d.when, Ctx(roles=dict(roles or {})))
                        sit = SitCheck(str(kind), d.origin if d else "?", "open", ok, "run_effects")
            outer = "/".join(self._stack)
            at_event = state.next_event_id
            self._enter(state, "run_effects")
            evs: list[Event] = []
            try:
                evs = orig(state, defs, effs, ctx, **kw)
                return evs
            finally:
                self._leave(state, "run_effects", evs)
                self._mark_steps(state, evs, info, "run_effects")
                if sit is not None:
                    for ev in evs:
                        self.sit_checks.setdefault(ev.id, sit)
                if source == "rule":
                    keys = tuple(sorted({k for e in effs if isinstance(e, dict) for k in e}))
                    self.rule_calls.append(
                        RuleCall(state.tick, keys, tuple(ev.id for ev in evs), self._cheat > 0, outer, at_event)
                    )

        return run_effects

    def _wrap_settle(self, orig: Any) -> Any:
        def settle_offscreen(state: WorldState, defs: Any, situation: str, **kw: Any) -> list[Event]:
            s = state.situations.get(situation)
            if s is not None:
                d = defs.situations.get(s.kind)
                sit = SitCheck(s.kind, d.origin if d else "?", "settle_running", True, "settle")
            else:
                d = defs.situations.get(situation)
                roles = dict(kw.get("roles") or {})
                ok = d is not None and self._holds(state, defs, d.when, Ctx(roles=roles))
                sit = SitCheck(str(situation), d.origin if d else "?", "settle_new", ok, "settle")
            self._enter(state, "settle")
            evs: list[Event] = []
            try:
                evs = orig(state, defs, situation, **kw)
                return evs
            finally:
                self._leave(state, "settle", evs)
                for ev in evs:
                    self.sit_checks.setdefault(ev.id, sit)

        return settle_offscreen

    def _wrap_plain(self, entry: str) -> Callable[[Any], Any]:
        def make(orig: Any) -> Any:
            def wrapped(state: WorldState, *a: Any, **kw: Any) -> Any:
                self._enter(state, entry)
                out: Any = None
                try:
                    out = orig(state, *a, **kw)
                    return out
                finally:
                    self._leave(state, entry, out if isinstance(out, list) and entry != "tick_situations" else None)

            return wrapped

        return make

    def _wrap_world_tick(self, orig: Any) -> Any:
        async def tick(llm: Any, state: WorldState, defs: Any, **kw: Any) -> list[Event]:
            evs = await orig(llm, state, defs, **kw)
            self.hours += 1
            for fn in self.hour_hooks:
                fn(state, defs)
            return evs

        return tick


__all__ = ["Tracer", "StepCheck", "SitCheck", "RuleCall", "ForeignWrite", "world_fingerprint"]
