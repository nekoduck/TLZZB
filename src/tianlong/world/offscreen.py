"""离场结算（方案 8.2）：替不在玩家眼前的人把打算的当前这一步做下去。

对每个离场的人，取当前这一步：

1. 没到 ``start``：等。过了 ``deadline``：作废。
2. 人不在 ``at``：沿最短路（``kernel.actions.path``）走过去，耗时按出口的 cost（坐骑按 speed 折算），
   带 check 的出口照掷（走路就是一次 ``go``，由 ``resolve.apply`` 结算）。走在一段路上的时间记在
   ``Agenda.progress`` 里，走完这一段清零。
3. 到了地方，``progress`` 累加，满 ``takes`` 时动手。
4. 动手那一刻检查 ``pre``：不成立，这一步作废；成立，有 ``offscreen`` 就执行它，没有就按动词走通用结算。

``say`` 的一步照 6.6、6.7 结算；对方怎样答复，有 ``decide`` 回调（game 传 minds.decide_one）就问它一次，
没有就按裁决用规则答复。图外的人在 ``start`` 到了时按这一步的 ``enter`` 效果入场。

一步做完，游标前移；一步作废，内核把此人放进 ``replan_queue``（8.3）。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import actions, danger, perception, predicates, resolve
from tianlong.kernel import speech as speech_mod
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_HOUR, Character, Ctx, Event, Speech, Step, WorldState, is_alive
from tianlong.world.common import LOG, Decide, in_scene, loc_of

MAX_STEPS_PER_HOUR = 6  # 一个时辰里一个人至多处理几段（入场、赶路的几段路；动手或作废一次就收工）
TARGET_KEYS = ("target", "to")  # do 里指人的键：没写 at 时到这个人身边去做
PARAM_KEYS = ("target", "target2", "item", "exit", "feature", "art", "intent", "ticks", "hours", "name")
DEFAULT_ASK = {"verb": "wait", "params": {}}  # say request 没写所求之事时的占位


# ---------------------------------------------------------------- helpers


def current_step(c: Character) -> Step | None:
    return danger.current_step(c)


def _rid(state: WorldState, cid: str, v: Any) -> Any:
    return predicates.resolve_id(v, state, Ctx(self_id=cid))


def effective_at(state: WorldState, c: Character, st: Step) -> str | None:
    """这一步在哪里做。写了 at 就是 at；没写时，do 里指了人就到那人身边，拿东西就到东西那里，否则就地。

    返回 None 表示要找的人已经不在图内（或死了），这一步做不到。
    """
    if st.at:
        return st.at
    do = st.do
    for key in TARGET_KEYS:
        t = _rid(state, c.id, do.get(key))
        if isinstance(t, str) and t in state.chars:
            return loc_of(state, t)
    if do.get("verb") in ("take", "search") and do.get("item"):
        loc = loc_of(state, _rid(state, c.id, do["item"]))
        return loc or c.loc
    return c.loc


def _step_pending(c: Character, step_id: str) -> bool:
    ag = c.agenda
    if ag is None:
        return False
    return any(s.id == step_id and s.state == "pending" for s in ag.steps)


def set_state(state: WorldState, defs: Defs, c: Character, st: Step, new: str, why: str = "") -> list[Event]:
    """把一步标为 done 或 void（经效果 step_state）。作废的，内核会把此人放进 replan_queue。"""
    if new == "void":
        LOG.info("step void: %s %s (%s) at tick %s", c.id, st.id, why, state.tick)
    return resolve.run_effects(
        state, defs, [{"step_state": [c.id, st.id, new]}], Ctx(self_id=c.id), source="agenda"
    )


def _progress(state: WorldState, defs: Defs, c: Character, n: int) -> None:
    resolve.run_effects(state, defs, [{"progress": [c.id, n]}], Ctx(self_id=c.id), source="agenda")


def expire_deadlines(state: WorldState, defs: Defs, chars: list[Character]) -> list[Event]:
    """过了 deadline 的当前步作废（8.2 第 1 条）。在场的人也一样：期限是时钟上的事，不是动作。"""
    out: list[Event] = []
    for c in chars:
        st = current_step(c)
        if st is not None and st.deadline is not None and state.tick > st.deadline and perception.on_map(c):
            out += set_state(state, defs, c, st, "void", "deadline")
    return out


def _params(state: WorldState, c: Character, st: Step) -> dict:
    do = st.do
    p: dict[str, Any] = {}
    for k in PARAM_KEYS:
        if k in do:
            p[k] = _rid(state, c.id, do[k])
    verb = do.get("verb")
    if verb == "go" and "exit" not in p and do.get("to"):
        p["exit"] = _rid(state, c.id, do["to"])
    if verb in ("give", "show", "steal", "teach", "help", "release", "restrain", "attack", "block", "push"):
        if "target" not in p and do.get("to"):
            p["target"] = _rid(state, c.id, do["to"])
    if verb in ("study", "practice", "teach", "rest") and "hours" not in p:
        p["hours"] = max(1, min(actions.MAX_HOURS, st.takes // TICKS_PER_HOUR or 1))
    if verb == "study" and "target" in p and "item" in p:
        p.pop("target")
    if verb == "attack":
        p.setdefault("intent", "injure")
    return p


# ---------------------------------------------------------------- entry


async def advance(
    state: WorldState, defs: Defs, cid: str, ticks: int = TICKS_PER_HOUR, *, decide: Decide | None = None
) -> list[Event]:
    """替一个离场的人把打算推进 ``ticks`` 刻（通常是一个时辰）。返回产生的事件（都标了 offscreen）。"""
    out: list[Event] = []
    budget = ticks
    for _ in range(MAX_STEPS_PER_HOUR):
        c = state.chars.get(cid)
        if c is None or not is_alive(c) or cid == state.player:
            break
        if not c.onstage:
            evs, entered = _maybe_enter(state, defs, c)
            out += evs
            if not entered:
                break
            budget -= 1
            continue
        if in_scene(state, c) or not perception.conscious(c):
            break
        st = current_step(c)
        if st is None:
            break
        if st.start is not None and state.tick < st.start:
            break
        # 一步作废就停下：他要按当时的处境重新打算（8.3，本时辰第 4 步），不再接着做旧打算的下一步
        if st.deadline is not None and state.tick > st.deadline:
            out += set_state(state, defs, c, st, "void", "deadline")
            break
        where = effective_at(state, c, st)
        if where is None:
            out += set_state(state, defs, c, st, "void", "target gone")
            break
        if c.loc != where:
            if budget <= 0:
                break
            moved, evs, budget, stop = _walk(state, defs, c, st, where, budget)
            out += evs
            if stop:
                break
            continue
        if st.do.get("verb") == "follow":
            _keep_following(state, defs, c, st)
        ag = c.agenda
        assert ag is not None
        need = st.takes - ag.progress
        if need > 0:
            if budget <= 0:
                break
            spend = min(need, budget)
            _progress(state, defs, c, spend)
            budget -= spend
            if spend < need:
                break
        out += await act(state, defs, c, st, decide=decide)
        # 做完（或作废）一步，这个时辰就到此为止：人留在做事的地方，下个时辰的情境检查看得见他
        # （否则一个人在一个时辰里进出石洞，「有人在洞里」的情境永远开不了场）。
        break
    return out


def _maybe_enter(state: WorldState, defs: Defs, c: Character) -> tuple[list[Event], bool]:
    """图外的人：当前这一步带 enter 效果、start 已到，就按它入场（8.1）。离开地图的人不再推演。"""
    st = current_step(c)
    if st is None or not any(isinstance(e, dict) and "enter" in e for e in st.offscreen):
        return [], False
    if st.start is not None and state.tick < st.start:
        return [], False
    if not predicates.holds(state, defs, st.pre, Ctx(self_id=c.id)):
        return set_state(state, defs, c, st, "void", "enter pre"), False
    evs = resolve.run_effects(
        state,
        defs,
        list(st.offscreen) + [{"step_state": [c.id, st.id, "done"]}],
        Ctx(self_id=c.id),
        source="agenda",
        step=st.id,
        offscreen=True,
    )
    entered = c.onstage
    if not entered:
        LOG.warning("enter failed for %s (%s)", c.id, st.id)
    return evs, entered


def _walk(
    state: WorldState, defs: Defs, c: Character, st: Step, where: str, budget: int
) -> tuple[bool, list[Event], int, bool]:
    """沿最短路走一段。返回 (是否挪了地方, 事件, 剩下的刻数, 这个时辰是否到此为止)。"""
    route = actions.path(state, defs, c.id, c.loc, where)
    if not route:
        return False, set_state(state, defs, c, st, "void", f"no way to {where}"), budget, True
    e = route[0]
    ag = c.agenda
    assert ag is not None
    act_ = actions.build(state, defs, c.id, "go", {"exit": e.to})
    if act_ is None:
        return False, [], budget, True  # 走不动（被扣押、重伤）：这个时辰原地不动，也不记路程
    need = actions.travel_cost(state, c, e) - ag.progress
    if need > budget:
        _progress(state, defs, c, budget)
        return False, [], 0, True
    before = c.loc
    evs = resolve.apply(state, defs, act_, source="agenda", offscreen=True)
    budget -= max(0, need)
    if c.agenda is ag and _step_pending(c, st.id):
        _progress(state, defs, c, 0)
    moved = c.loc != before
    return moved, evs, budget, not moved


def _keep_following(state: WorldState, defs: Defs, c: Character, st: Step) -> None:
    """follow 的一步：到了那人身边先跟上（之后他走到哪里，内核让跟随者一起走），再等满 takes。"""
    t = _rid(state, c.id, st.do.get("target"))
    if not isinstance(t, str) or c.following == t:
        return
    tc = state.chars.get(t)
    if tc is None or tc.loc != c.loc or not perception.on_map(tc):
        return
    resolve.run_effects(state, defs, [{"follow": [c.id, t]}], Ctx(self_id=c.id), source="agenda")


# ---------------------------------------------------------------- doing the step


async def act(state: WorldState, defs: Defs, c: Character, st: Step, *, decide: Decide | None = None) -> list[Event]:
    """动手那一刻：查 pre；有 offscreen 就执行；否则按动词走通用结算（8.2 的表）。"""
    ctx = Ctx(self_id=c.id)
    try:
        ok = predicates.holds(state, defs, st.pre, ctx)
    except predicates.KernelError as e:
        LOG.warning("bad pre in %s/%s: %s", c.id, st.id, e)
        ok = False
    if not ok:
        return set_state(state, defs, c, st, "void", "pre")
    if st.offscreen:
        evs = resolve.run_effects(
            state,
            defs,
            list(st.offscreen) + [{"step_state": [c.id, st.id, "done"]}],
            ctx,
            source="agenda",
            step=st.id,
            offscreen=True,
        )
        if _step_pending(c, st.id):
            evs += set_state(state, defs, c, st, "void", "offscreen effects rolled back")
        return evs
    verb = st.do.get("verb")
    if verb == "wait" or verb == "follow":
        return set_state(state, defs, c, st, "done")
    if verb == "rest":
        evs = resolve.run_effects(
            state,
            defs,
            [{"rest": [c.id, max(1, st.takes)]}, {"step_state": [c.id, st.id, "done"]}],
            ctx,
            source="agenda",
            step=st.id,
            offscreen=True,
        )
        return evs
    if verb == "say":
        return await _say(state, defs, c, st, decide)
    if verb not in actions.VERBS or verb == "setpiece":
        return set_state(state, defs, c, st, "void", f"verb {verb}")
    a = actions.build(state, defs, c.id, str(verb), _params(state, c, st))
    if a is None or actions.blocked_by_bottom(state, defs, a):
        return set_state(state, defs, c, st, "void", "cannot do")
    evs = resolve.apply(state, defs, a, source="agenda", offscreen=True)
    if _step_pending(c, st.id):
        tried = any(e.actor == c.id and e.outcome != "blocked" for e in evs)
        evs += set_state(state, defs, c, st, "done" if tried else "void", "" if tried else "blocked")
    return evs


def _speech_for(state: WorldState, c: Character, st: Step) -> Speech:
    do = st.do
    act_ = str(do.get("act") or "tell")
    to = _rid(state, c.id, do.get("to"))
    sp = Speech(act=act_, to=[to] if isinstance(to, str) and to else [])
    fact = _rid(state, c.id, do.get("fact"))
    if isinstance(fact, str) and fact:
        sp.fact_refs = [fact]
    if act_ in speech_mod.REQUEST_ACTS:
        ask = do.get("ask") or DEFAULT_ASK
        params = {k: _rid(state, c.id, v) for k, v in (ask.get("params") or {}).items()}
        sp.ask_action = {"verb": ask.get("verb", "wait"), "params": params}
        if do.get("cls"):
            sp.action_class = str(do["cls"])
        sp.gist = st.text
    elif act_ == "ask":
        about = _rid(state, c.id, do.get("about") or do.get("fact"))
        sp.about = about if isinstance(about, str) else None
        sp.gist = st.text
    return sp


async def _say(state: WorldState, defs: Defs, c: Character, st: Step, decide: Decide | None) -> list[Event]:
    sp = _speech_for(state, c, st)
    if sp.to and not {o.id for o in speech_mod.audience(state, c)}.intersection(sp.to):
        # 这句话是说给某个人听的，他不在跟前：这一步作废（不必让内核把它当违规记一笔）
        return set_state(state, defs, c, st, "void", "addressee not here")
    carrier = actions.build(state, defs, c.id, "wait", {"ticks": 1})
    if carrier is None:
        return set_state(state, defs, c, st, "void", "cannot speak")
    evs = resolve.apply(state, defs, carrier, sp, source="agenda", offscreen=True)
    said = next((e for e in evs if e.verb == "say" and e.actor == c.id), None)
    if said is None:
        return evs + set_state(state, defs, c, st, "void", "speech dropped")
    if _step_pending(c, st.id):
        evs += set_state(state, defs, c, st, "done")
    rid = said.speech.request if said.speech is not None else None
    if rid:
        evs += await reply(state, defs, rid, decide)
    return evs


async def reply(state: WorldState, defs: Defs, rid: str, decide: Decide | None = None) -> list[Event]:
    """对方答复一句冲他来的话（8.2：照样问对方一次，只是不写叙述）。"""
    r = state.requests.get(rid)
    if r is None or r.status != "open":
        return []
    to = state.chars.get(r.to)
    if to is None or not actions.can_act(to) or in_scene(state, to):
        return []
    if decide is not None:
        try:
            d = await decide(state, defs, to.id)
        except Exception as e:  # 模型或上层出错：照规则答复，游戏不中断
            LOG.warning("decide failed for %s: %s", to.id, e)
            d = None
        a = getattr(d, "action", None) if d is not None else None
        if a is not None:
            sp_d = getattr(d, "speech", None)
            if sp_d is None or a.verb == "wait":
                return resolve.apply(state, defs, a, sp_d, source="mind", offscreen=True)
            # 一句话加一件事：分两次进内核，先话后事。话若是「答应」，内核会把答应的事插成他眼下这一步；
            # 同一次结算里紧接着做那件事，不变量把它认作「已作废步骤」的事件，整体回滚，连那句答应也丢了。
            carrier = actions.build(state, defs, to.id, "wait", {"ticks": 1})
            said = resolve.apply(state, defs, carrier, sp_d, source="mind", offscreen=True) if carrier else []
            fresh = actions.is_legal(state, defs, a)
            if fresh is None:
                return said
            return said + resolve.apply(state, defs, fresh, None, source="mind", offscreen=True)
    sp = rule_reply(state, defs, rid)
    if sp is None:
        return []
    carrier = actions.build(state, defs, to.id, "wait", {"ticks": 1})
    if carrier is None:
        return []
    return resolve.apply(state, defs, carrier, sp, source="rule", offscreen=True)


def rule_reply(state: WorldState, defs: Defs, rid: str) -> Speech | None:
    """规则答复：may 答应；conditional 开条件（没有可开的就拒绝）；其余拒绝。问话：知道就说，不知道就拒答。"""
    r = state.requests.get(rid)
    if r is None:
        return None
    to = state.chars[r.to]
    if r.kind == "question":
        about = (r.action.get("params") or {}).get("about")
        b = to.beliefs.get(about) if isinstance(about, str) else None
        if b is not None and b.confidence >= predicates.BELIEVE and not speech_mod.say_blocked(
            state, defs, to, "tell", fact=about, to=[r.frm]
        ):
            return Speech(act="tell", to=[r.frm], request=r.id, fact_refs=[about])
        return Speech(act="refuse", to=[r.frm], request=r.id)
    if r.verdict == "may":
        return Speech(act="agree", to=[r.frm], request=r.id)
    if r.verdict == "conditional":
        opts = speech_mod.counter_options(state, defs, r)
        if opts:
            return Speech(act="bargain", to=[r.frm], request=r.id, condition=opts[0])
    return Speech(act="refuse", to=[r.frm], request=r.id)


__all__ = ["advance", "act", "reply", "rule_reply", "effective_at", "expire_deadlines", "set_state"]
