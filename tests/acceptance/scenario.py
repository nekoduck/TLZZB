"""分岔测试的公用部分（方案 11.4）：搭一局（真实的 content/、Mock 模型、探针与监视都装好），
脚本化地做玩家的动作，让世界跑到第十日，然后统一断言 3.4 的三条保证。"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tianlong.config import Settings
from tianlong.kernel import actions, refs
from tianlong.kernel.state import TICKS_PER_DAY, TICKS_PER_HOUR, Event, Speech, is_alive
from tianlong.testing import Driver, Monitor, Tracer, guarantees

PLAYER = "player"
DAY2 = TICKS_PER_DAY  # 第二日子时
DAY10 = 9 * TICKS_PER_DAY + 32  # 第十日辰时（与开局同一个时辰）


@dataclass(frozen=True)
class Snap:
    """一个人在某个时辰末的样子。"""

    tick: int
    loc: str
    onstage: bool
    alive: bool
    statuses: frozenset[str]
    inventory: tuple[str, ...]
    arts: tuple[tuple[str, int], ...]
    goal: str | None
    agenda_status: str | None
    waiting_for: str
    step: str | None  # 当前这一步
    step_origin: str | None
    done: int  # 这份打算里已做完的步数
    void: int

    def art(self, name: str) -> int:
        return dict(self.arts).get(name, 0)


def snap(state: Any, cid: str) -> Snap:
    c = state.chars[cid]
    ag = c.agenda
    cur = ag.steps[ag.cursor] if ag is not None and ag.cursor < len(ag.steps) else None
    return Snap(
        tick=state.tick,
        loc=c.loc,
        onstage=c.onstage,
        alive=is_alive(c),
        statuses=frozenset(s.kind for s in c.statuses),
        inventory=tuple(c.inventory),
        arts=tuple(sorted(c.arts.items())),
        goal=ag.goal if ag else None,
        agenda_status=ag.status if ag else None,
        waiting_for=ag.waiting_for if ag else "",
        step=cur.id if cur is not None and cur.state == "pending" else None,
        step_origin=cur.origin if cur is not None else None,
        done=sum(1 for s in ag.steps if s.state == "done") if ag else 0,
        void=sum(1 for s in ag.steps if s.state == "void") if ag else 0,
    )


@dataclass
class Scene:
    drv: Driver
    tr: Tracer
    mon: Monitor
    hist: dict[str, list[Snap]] = field(default_factory=dict)  # 有魂、有名的人，每个时辰一格

    def __post_init__(self) -> None:
        self.tr.hour_hooks.append(self.mon.on_hour)
        self.tr.hour_hooks.append(self._record)
        self._record(self.drv.state, self.drv.defs)

    # ---- 读

    @property
    def state(self) -> Any:
        return self.drv.state

    @property
    def tick(self) -> int:
        return self.drv.state.tick

    def char(self, cid: str) -> Any:
        return self.drv.state.chars[cid]

    def _record(self, state: Any, defs: Any) -> None:
        for cid, c in state.chars.items():
            if c.tier in ("soul", "named") or cid == state.player:
                self.hist.setdefault(cid, []).append(snap(state, cid))

    def sample(self) -> None:
        self._record(self.drv.state, self.drv.defs)

    def last_id(self) -> int:
        return self.drv.state.next_event_id - 1

    def events(self, *, actor: str | None = None, verb: str | None = None, after: int = 0, ok: bool = False) -> list[Event]:
        out = []
        for e in self.drv.events:
            if e.id <= after or (actor is not None and e.actor != actor) or (verb is not None and e.verb != verb):
                continue
            if ok and e.outcome != "ok":
                continue
            out.append(e)
        return out

    def said(self, e: Event) -> str:
        return refs.render(e.fact_text, self.drv.state, self.drv.defs)

    def story(self, *who: str, after: int = 0, limit: int = 60) -> str:
        """与某些人有关的事件，逐行排开（断言失败时看的）。"""
        rows = []
        for e in self.drv.events:
            if e.id <= after:
                continue
            if who and e.actor not in who and not any(f"⟦{w}⟧" in e.fact_text for w in who):
                continue
            rows.append(f"[{e.id} t{e.tick} {e.source}{' off' if e.offscreen else ''} {e.step or ''}] {e.outcome} {self.said(e)}")
        return "\n".join(rows[-limit:])

    def ever(self, cid: str, pred: Any, *, after_tick: int = -1) -> bool:
        return any(pred(s) for s in self.hist.get(cid, []) if s.tick > after_tick)

    def steps_of(self, cid: str, *, after_tick: int = -1) -> list[str]:
        """此人先后在做的各一步（去掉连着重复的）。"""
        out: list[str] = []
        for s in self.hist.get(cid, []):
            if s.tick > after_tick and s.step is not None and (not out or out[-1] != s.step):
                out.append(s.step)
        return out

    def divergence(self, mark: str) -> Any:
        return next((d for d in self.drv.state.divergences if d.mark == mark), None)

    # ---- 玩家的动作

    async def goto(self, loc: str, *, max_moves: int = 30) -> bool:
        """沿最短路一段一段走过去（每一段都是一次真的 ``go``）。走不到返回 False。"""
        drv = self.drv
        for _ in range(max_moves):
            if drv.me.loc == loc:
                return True
            if drv.state.ended is not None or not actions.can_act(drv.me):
                return False
            route = actions.path(drv.state, drv.defs, drv.state.player, drv.me.loc, loc)
            if not route:
                return False
            a = actions.build(drv.state, drv.defs, drv.state.player, "go", {"exit": route[0].to})
            if a is None:
                return False
            await drv.act(a)
            self.sample()
        return drv.me.loc == loc

    async def say(self, act: str, to: str, **kw: Any) -> list[Event]:
        """玩家开口：一句言语行为，经内核的言语规则结算（等 1 刻带那句话）。"""
        await self.drv.say(Speech(act=act, to=[to], **kw))
        self.sample()
        return list(self.drv.last_events)

    async def wait(self, ticks: int = 1) -> None:
        """玩家原地等一会儿（合法清单里不超过 ``ticks`` 刻的最长的一个「等」）；动弹不得时由着时间过去。"""
        drv = self.drv
        if not drv.game._can_act():
            await drv.send("等着")
        else:
            waits = [a for a in drv.legal("wait") if int(a.params.get("ticks", 1)) <= ticks]
            assert waits, "no legal wait"
            await drv.act(max(waits, key=lambda a: int(a.params.get("ticks", 1))))
        self.sample()

    async def wait_until(self, cond: Any, *, until_tick: int, step: int = 2) -> bool:
        """等到 ``cond()`` 成立；过了 ``until_tick`` 还不成立返回 False。"""
        while not cond():
            if self.tick >= until_tick or self.state.ended is not None:
                return False
            await self.wait(step)
        return True

    def situation(self, kind: str) -> Any:
        """玩家所在地点正在进行的这一种情境（没有返回 None）。"""
        me = self.drv.me
        return next((s for s in self.state.situations.values() if s.kind == kind and s.loc == me.loc), None)

    async def setpiece(self, name: str) -> bool:
        """做眼前情境里的一个动作；致命的先被警示一次，再做一次才算数。此刻做不了返回 False。"""
        drv = self.drv
        for _ in range(2):
            acts = [a for a in drv.legal("setpiece") if a.params.get("name") == name]
            if not acts:
                return False
            await drv.act(acts[0])
            self.sample()
            if drv.game.last_turn is None or drv.game.last_turn.kind != "warn":
                return True
        return False

    def park(self) -> None:
        """玩家经效果离开地图（此后人人都算离场，世界自己走）。"""
        self.drv.fx([{"leave": self.drv.state.player}])

    async def run_to(self, tick: int) -> None:
        await self.drv.run_until(tick, chunk=TICKS_PER_HOUR)
        self.sample()

    async def to_day10(self) -> None:
        await self.run_to(DAY10)
        self.mon.finish(self.drv.state)
        assert self.drv.state.tick >= DAY10 or self.drv.state.ended is not None

    def assert_no_rollbacks(self) -> None:
        """整局没有一次不变量失败引起的回滚（6.11）。"""
        assert self.mon.rollbacks == [], "rollbacks:\n" + "\n".join(self.mon.rollbacks[:10])

    # ---- 三条统一的断言

    def guarantees(self, after_id: int) -> dict[str, list[str]]:
        return guarantees(
            self.drv.events, self.tr, self.mon, self.drv.defs, after_id=after_id, cheat_ids=self.drv.cheat_ids
        )

    def assert_guarantees(self, after_id: int) -> None:
        """方案 11.4：没有幽灵事件、没有回弹、没有停摆。另加：每回合直接查的不变量没有失败过。

        内核回滚掉的动作（``violations`` 里的 rollback）不在这三条之内——回滚正是「前提不成立就不执行」
        的那道闸；各场景在最后另用 ``assert_no_rollbacks`` 查。
        """
        g = self.guarantees(after_id)
        assert g["ghost"] == [], "ghost events:\n" + "\n".join(g["ghost"][:10])
        assert g["rebound"] == [], "rebound:\n" + "\n".join(g["rebound"][:10])
        assert g["stall"] == [], "stall:\n" + "\n".join(g["stall"][:10])
        assert self.mon.invariant_failures == [], self.mon.invariant_failures[:5]
        # 探针确实看见了这一局：事件都有出处，时辰都采了样
        assert self.tr.hours >= 8 and self.mon.samples >= self.tr.hours
        seen = sum(1 for e in self.drv.events if e.id in self.tr.entry_of)
        assert seen >= 0.95 * len(self.drv.events), (seen, len(self.drv.events))


@contextlib.contextmanager
def spy(module: Any, name: str) -> Any:
    """记下对 ``module.name`` 的每次调用：(位置参数, 返回值)。只看不改。"""
    orig = getattr(module, name)
    calls: list[tuple[tuple, Any]] = []

    def wrapped(*a: Any, **kw: Any) -> Any:
        out = orig(*a, **kw)
        calls.append((a, out))
        return out

    setattr(module, name, wrapped)
    try:
        yield calls
    finally:
        setattr(module, name, orig)


def known_rollbacks(sc: Scene, *patterns: str) -> list[str]:
    """回滚记录里，不属于 ``patterns``（已知问题的特征串）的那些。"""
    return [r for r in sc.mon.rollbacks if not any(p in r for p in patterns)]


@contextlib.asynccontextmanager
async def scene(settings: Settings, *, seed: int = 1) -> AsyncIterator[Scene]:
    """开一局（调试开关打开，/debug 可用），探针与监视都装好。"""
    with Tracer() as tr:
        drv = await Driver.new(settings, seed=seed, debug=True, run_id=f"scene-s{seed}")
        tr.attach(drv)
        sc = Scene(drv, tr, Monitor(max_idle=TICKS_PER_HOUR))
        _after_send(sc)
        try:
            yield sc
        finally:
            await drv.close()


def _after_send(sc: Scene) -> None:
    """每次输入之后让监视器看一眼（不变量、回滚、活性）。"""
    drv = sc.drv
    send, act = drv.send, drv.act

    async def traced_send(raw: str) -> Any:
        out = await send(raw)
        sc.mon.after_turn(drv.state, drv.defs, drv.last_events)
        return out

    async def traced_act(*a: Any, **kw: Any) -> Any:
        out = await act(*a, **kw)
        sc.mon.after_turn(drv.state, drv.defs, drv.last_events)
        return out

    drv.send = traced_send  # type: ignore[method-assign]
    drv.act = traced_act  # type: ignore[method-assign]


def current_steps_touching(state: Any, who: str) -> list[tuple[str, str, str]]:
    """别人的打算里，仍待做、而且指名要对 ``who`` 做的步骤：(人, 步骤, 状态)。"""
    out = []
    for c in state.chars.values():
        if c.id == who or c.agenda is None:
            continue
        for st in c.agenda.steps:
            do = st.do or {}
            if who in (do.get("target"), do.get("to"), do.get("target2")) and st.state == "pending":
                out.append((c.id, st.id, st.state))
    return out


__all__ = ["DAY2", "DAY10", "PLAYER", "Scene", "Snap", "current_steps_touching", "scene", "snap", "spy"]
