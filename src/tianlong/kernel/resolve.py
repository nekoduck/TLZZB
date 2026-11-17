"""结算（方案 6.3）：``apply(state, defs, action, speech=None, *, source) -> list[Event]``。

1. 重新校验动作此刻是否合法；不合法记一条 blocked 事件并返回。
2. 需要判定的按 6.4 掷一次。3. 执行效果，生成事件。4. 言语行为与信念更新（先话后动作）。
5、6. 见证者、记忆、情绪与关系（由 ``events.emit`` 完成）。
7. 断言不变量；失败则整体回滚这次动作并记日志。

回滚不深拷贝整个状态：结算前按 schema 拍一张快照（可变的模型存 __dict__ 的浅拷贝、可变的容器浅拷贝；
记忆、事件、事实、信念这些写入后不再原地修改的对象只拷引用），失败时原地还原。
"""

from __future__ import annotations

import copy
from typing import Any

from tianlong.kernel import (
    actions,
    checks,
    conflict,
    danger,
    effects,
    events,
    growth,
    invariants,
    perception,
    predicates,
    refs,
)
from tianlong.kernel import (
    speech as speech_mod,
)
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.predicates import KernelError
from tianlong.kernel.state import (
    Claim,
    Ctx,
    Event,
    Exit,
    Speech,
    Step,
    WorldState,
    has_status,
    log_violation,
)

# ---------------------------------------------------------------- snapshot / restore
#
# 快照按 schema 拍：每个可变的模型存一份 __dict__ 的浅拷贝，每个可变的容器存一份浅拷贝；
# 还原时原地写回，对象的身份不变。记忆、事件、事实、信念、出口、景物写入后从不原地修改，只拷引用。
# 内核里凡是原地改写的东西都必须在这里登记（见 DECISIONS：回滚的做法）。

_STATE_DICTS = (
    "locations",
    "chars",
    "items",
    "facts",
    "flags",
    "requests",
    "situations",
    "threads",
    "promises",
    "counters",
    "term_alias",
)
_STATE_LISTS = (
    "scheduled",
    "known_terms",
    "chronicle",
    "divergences",
    "pending_options",
    "warned",
    "events_tail",
    "replan_queue",
    "visited",
    "examined",
    "violations",
)
_CHAR_LISTS = ("memory", "statuses", "inventory", "roles", "drives")
_CHAR_DICTS = ("beliefs", "rel", "arts", "art_progress", "mood", "owes", "comforted")


class Snapshot:
    def __init__(self, state: WorldState) -> None:
        objs: list[tuple[Any, dict]] = []
        lists: list[tuple[list, list]] = []
        dicts: list[tuple[dict, dict]] = []
        sd = state.__dict__
        objs.append((state, sd.copy()))
        for name in _STATE_DICTS:
            dicts.append((sd[name], sd[name].copy()))
        for name in _STATE_LISTS:
            lists.append((sd[name], sd[name][:]))
        st = sd["storyteller"]
        dicts.append((st, copy.deepcopy(st)))
        for loc in state.locations.values():
            lists.append((loc.traces, loc.traces[:]))
        for it in state.items.values():
            objs.append((it, it.__dict__.copy()))
        for t in state.threads.values():
            objs.append((t, t.__dict__.copy()))
        for sit in state.situations.values():
            objs.append((sit, sit.__dict__.copy()))
        pending: set[str] = set()
        known: set[str] = set()
        able: set[str] = set()
        for c in state.chars.values():
            d = c.__dict__
            objs.append((c, d.copy()))
            for name in _CHAR_LISTS:
                lists.append((d[name], d[name][:]))
            for name in _CHAR_DICTS:
                dicts.append((d[name], d[name].copy()))
            for r in d["rel"].values():
                objs.append((r, r.__dict__.copy()))
            for s_ in d["statuses"]:
                objs.append((s_, s_.__dict__.copy()))
            ag = d["agenda"]
            if ag is not None:
                objs.append((ag, ag.__dict__.copy()))
                lists.append((ag.steps, ag.steps[:]))
                for step in ag.steps:
                    objs.append((step, step.__dict__.copy()))
                    known.add(step.id)
                    if step.state == "pending":
                        pending.add(step.id)
            if perception.on_map(c):
                able.add(c.id)
        for r in state.requests.values():
            objs.append((r, r.__dict__.copy()))
            lists.append((r.evidence, r.evidence[:]))
        for s_ in state.situations.values():
            objs.append((s_, s_.__dict__.copy()))
            dicts.append((s_.roles, s_.roles.copy()))
        for t in state.threads.values():
            objs.append((t, t.__dict__.copy()))
            lists.append((t.refs, t.refs[:]))
        for p in state.promises.values():
            objs.append((p, p.__dict__.copy()))
        self.objs = objs
        self.lists = lists
        self.dicts = dicts
        self.tick = state.tick
        self.pending_steps = pending
        self.known_steps = known
        self.able = able

    def restore(self, state: WorldState) -> None:
        for obj, saved in self.objs:
            d = obj.__dict__
            d.clear()
            d.update(saved)
        for lst, saved_l in self.lists:
            lst[:] = saved_l
        for dct, saved_d in self.dicts:
            dct.clear()
            dct.update(saved_d)


def snapshot(state: WorldState) -> Snapshot:
    return Snapshot(state)


def _guarded(state: WorldState, defs: Defs, label: str, fn: Any) -> list[Event]:
    predicates.sync_enmity(state, defs)
    snap = Snapshot(state)
    problems: list[str]
    try:
        evs = fn()
        problems = invariants.check(
            state,
            defs,
            new_events=evs,
            tick_before=snap.tick,
            pending_before=snap.pending_steps,
            steps_before=snap.known_steps,
            able_before=snap.able,
        )
    except KernelError as e:
        evs = []
        problems = [f"{type(e).__name__}: {e}"]
    if problems:
        snap.restore(state)
        log_violation(state, f"rollback {label}: " + "; ".join(problems[:3]))
        return []
    return evs


# ---------------------------------------------------------------- public entry points


def apply(
    state: WorldState,
    defs: Defs,
    action: ActionInst,
    speech: Speech | None = None,
    *,
    source: str,
    offscreen: bool = False,
) -> list[Event]:
    """结算一个身体动作加一句话（先话后动作）。不变量失败则整体回滚，返回空表。

    ``offscreen=True``：这是离场结算出来的（world 替不在玩家眼前的人走的一步），
    这次结算产生的事件全部标上 ``offscreen``（K6）。

    玩家第一次尝试致命动作时只出一条警示事件，局面不变（2.4、6.10）：连同这一回合要说的话也不结算
    （K13）。叙述只拿到警示；玩家再说一遍、或再做一遍，才真的发生。
    """
    first = state.next_event_id
    evs = _guarded(state, defs, action.signature, lambda: _apply(state, defs, action, speech, source))
    if offscreen:
        mark_offscreen(state, first)
    return evs


def mark_offscreen(state: WorldState, first_id: int) -> None:
    """把 id 不小于 ``first_id`` 的事件标成离场结算出来的。"""
    for ev in reversed(state.events_tail):
        if ev.id < first_id:
            break
        ev.offscreen = True


def run_effects(
    state: WorldState,
    defs: Defs,
    effs: list[dict],
    ctx: Ctx | None = None,
    *,
    source: str,
    step: str | None = None,
    offscreen: bool = False,
) -> list[Event]:
    """带回滚的效果执行：给 world / storyteller 用（离场结算、情境、机会牌）。"""
    return _guarded(
        state,
        defs,
        f"effects[{source}]",
        lambda: effects.apply_effects(state, defs, effs, ctx, source=source, step=step, offscreen=offscreen),
    )


def settle_offscreen(
    state: WorldState,
    defs: Defs,
    situation: str,
    *,
    loc: str | None = None,
    roles: dict[str, str] | None = None,
    source: str = "situation",
) -> list[Event]:
    """情境的离场结算（5.7、8.2）：取 offscreen 表里第一个 when 成立的一项执行，然后散场。

    ``situation`` 是正在进行的情境实例 id，或者情境定义的名字（那就先按定义绑角色位开场）。带回滚。
    """

    def run() -> list[Event]:
        r = effects.Run(state, defs, Ctx(), source=source, offscreen=True)
        s = state.situations.get(situation)
        if s is None:
            s = effects.open_situation(r, situation, loc, roles)
            if s is None:
                return []
        d = defs.situations[s.kind]
        ctx = Ctx(roles=dict(s.roles), situation=s.id)
        out = list(r.events)
        for rule in d.offscreen:
            if predicates.holds(state, defs, rule.when, ctx):
                out += effects.apply_effects(state, defs, rule.effects, ctx, source=source, offscreen=True)
                break
        effects.close_situation(state, defs, s.id)
        return out

    return _guarded(state, defs, f"offscreen[{situation}]", run)


def resolve_action(
    state: WorldState, defs: Defs, action: ActionInst, *, source: str, step: str | None = None
) -> list[Event]:
    """不带校验与回滚的动作结算（内核内部用，例如履行还价的条件）。"""
    c = state.chars[action.actor]
    r = R(state, defs, action, c, source, step)
    return getattr(r, actions.VERBS[action.verb].resolver)()


def _matching_step(
    state: WorldState, defs: Defs, actor: str, action: ActionInst, speech: Speech | None
) -> Step | None:
    c = state.chars[actor]
    st = danger.current_step(c)
    if st is None or not actions.step_ready(state, defs, c, st):
        return None
    if st.at is not None and st.at != c.loc:
        return None
    if actions.matches_do(action, st.do, Ctx(self_id=actor), state=state, speech=speech):
        return st
    if action.verb == "wait" and actions.following_step(state, c, st):
        return st  # 已经跟上了的「跟着某人」：原地等着也是在做这一步
    return None


def _apply(
    state: WorldState, defs: Defs, action: ActionInst, speech: Speech | None, source: str
) -> list[Event]:
    c = state.chars.get(action.actor)
    if c is None or not actions.can_act(c):
        log_violation(state, f"{action.actor} cannot act now ({action.signature})")
        return []
    out: list[Event] = []
    # 玩家的致命动作：先警示，局面不变（2.4、6.10）。致命与否按此刻的状态重算。
    # 这一回合要说的话也一并不结算（K13）：局面不变，叙述只拿到这条警示。
    fresh = actions.is_legal(state, defs, action)
    lethal = fresh.lethal if fresh is not None else action.lethal
    if source == "player" and lethal and action.signature not in state.warned:
        danger.mark_warned(state, action.signature)
        out.append(
            events.emit(
                state,
                defs,
                actor=c.id,
                verb="warn_danger",
                text=f"⟦{c.id}⟧觉出这一下凶险万分，没有贸然动手：{action.desc}",
                params={"action": action.signature},
                outcome="blocked",
                source=source,
                hidden=True,
            )
        )
        return out
    tense = actions.tense_for(state, c)
    # 先话
    if speech is not None:
        say_step = None
        st = danger.current_step(c)
        if (
            st is not None
            and st.do.get("verb") == "say"
            and actions.matches_do(action, st.do, Ctx(self_id=c.id), state=state, speech=speech)
        ):
            say_step = st
        sevs = speech_mod.process(
            state, defs, c.id, speech, source=source, step=say_step.id if say_step else None
        )
        out.extend(sevs)
        if say_step is not None and sevs:
            effects.set_step_state(state, c, say_step.id, "done")
        if sevs:
            speech_mod.promise_kept(state, defs, c.id, action, sevs[0].speech, sevs[0])
        if not perception.conscious(c) or not perception.on_map(c):
            return out
    # 再校验动作
    act = actions.is_legal(state, defs, action)
    if act is None:
        if action.verb == "wait" and out:
            return out
        out.append(
            events.emit(
                state,
                defs,
                actor=c.id,
                verb=action.verb,
                text=f"⟦{c.id}⟧想要{action.desc}，却没能做成",
                params=dict(action.params),
                outcome="blocked",
                source=source,
                hidden=True,
            )
        )
        return out
    step = _matching_step(state, defs, c.id, act, None)
    if has_status(c, "hidden") and _reveals(act):
        c.statuses = [s for s in c.statuses if s.kind != "hidden"]
    r = R(state, defs, act, c, source, step.id if step else None)
    spoke = any(e.verb == "say" and e.actor == c.id for e in out)
    if act.verb == "wait" and spoke:
        aevs = []  # 只说话、不动：不另记「等」的事件
    else:
        aevs = getattr(r, actions.VERBS[act.verb].resolver)()
    out.extend(aevs)
    main = aevs[0] if aevs else None
    # 收尾：这一步做完了；许诺；还价的条件；牵制与示警用掉；紧张场面的步数；情境散场
    ok = main is not None and main.outcome == "ok"
    if step is not None and ok and step.state == "pending":
        if step.do.get("verb") in ("wait", "follow") and c.agenda is not None:
            # 「等」这一步要等够 takes 刻（紧张场面里每步算 1）；「跟着某人」也一样：跟上之后要跟满 takes 刻
            c.agenda.progress += max(1, act.cost)
            if c.agenda.progress >= step.takes:
                effects.set_step_state(state, c, step.id, "done")
        else:
            effects.set_step_state(state, c, step.id, "done")
    if ok:
        speech_mod.promise_kept(state, defs, c.id, act, None, main)
        speech_mod.condition_met(state, defs, c.id, act)
    if act.verb not in ("wait",):
        c.statuses = (
            [s for s in c.statuses if s.kind not in ("impeded", "alerted")]
            if act.verb != "hide"
            else [s for s in c.statuses if s.kind != "impeded"]
        )
    if tense:
        state.step += 1
    effects.close_finished(state, defs)
    return out


# 冲着别人去的动作（上手，或当面递、亮、照料、放开、传授）：一做就藏不住了
_CONTACT_VERBS = frozenset({"restrain", "block", "give", "show", "help", "release", "teach"})


def _reveals(act: ActionInst) -> bool:
    """躲着的人做这个动作，是不是就此现了身（6.9：动作不被看见，直到出声或被搜出）。

    出声的（attack）、冲着别人去的（上手拿人、拦、推人、递、亮、照料、放开、传授、对别人用东西）都算；
    自己一个人悄悄做的（看、听、查、搜、拿、放、推机关、自己用东西、歇、读、练、等）不算。
    偷另算：得手没有人看见，失手才现身（``R.r_steal``）。走动在 ``effects.move_char`` 里已经解除。
    """
    if act.loud or act.verb in _CONTACT_VERBS:
        return True
    t = act.params.get("target")
    if act.verb == "push":
        return t is not None
    if act.verb == "use":
        return t is not None and t != act.actor
    return False


# ---------------------------------------------------------------- verb resolvers


class R:
    """一个动作的结算。每个 r_<verb> 只经由效果改写状态，返回产生的事件。"""

    def __init__(
        self, state: WorldState, defs: Defs, action: ActionInst, actor: Any, source: str, step: str | None
    ):
        self.state = state
        self.defs = defs
        self.a = action
        self.c = actor
        self.me = actor.id
        self.p = action.params
        self.source = source
        self.step = step

    def fx(self, effs: list[dict], ctx: Ctx | None = None) -> list[Event]:
        return effects.apply_effects(
            self.state, self.defs, effs, ctx or Ctx(self_id=self.me), source=self.source, step=self.step
        )

    def run(self, effs: list[dict], ctx: Ctx | None = None) -> effects.Run:
        run = effects.Run(
            self.state, self.defs, ctx or Ctx(self_id=self.me), source=self.source, step=self.step
        )
        run.run_list(effs)
        return run

    def say(self, text: str, *, verb: str | None = None, outcome: str = "ok", **kw: Any) -> dict:
        d = {"actor": self.me, "verb": verb or self.a.verb, "text": text, "outcome": outcome}
        params = dict(self.p)
        params.update(kw.pop("params", {}))
        d["params"] = params
        d.update(kw)
        return {"emit": d}

    # ---- movement

    def _fall_branch(self, frm: str, e: Exit) -> list[dict]:
        land = e.fall_to or (e.to if e.check == "descend" else frm)
        me = self.me
        return [
            self.say(f"⟦{me}⟧一失手，坠了下去", verb="fall", outcome="fail", loud=True),
            {
                "check": {
                    "kind": "fall",
                    "who": me,
                    "loc": frm,
                    "ok": [
                        {"move": [me, land]},
                        {"injure": [me, 2]},
                        self.say(f"⟦{me}⟧摔在⟦{land}⟧，捡回一条命", verb="land"),
                    ],
                    "fail": [{"move": [me, land]}, {"kill": [me, f"fall:{frm}"]}],
                }
            },
        ]

    def _traverse(self, e: Exit, depart: str, arrive: str, pre: list[dict] | None = None) -> list[Event]:
        frm = self.c.loc
        me = self.me
        pre = list(pre or [])
        if e.edge:
            known = refs.lookup(self.state, self.defs, e.to) is not None
            gone = f"⟦{me}⟧往⟦{e.to}⟧去了，走出了这一带" if known else f"⟦{me}⟧走出了这一带"
            effs = pre + [self.say(gone), {"leave": me}]
            if me == self.state.player:
                effs.append({"end": "left"})
            return self.fx(effs)
        if e.check:
            return self.fx(
                pre
                + [
                    self.say(depart),
                    {
                        "check": {
                            "kind": e.check,
                            "who": me,
                            "ok": [{"move": [me, e.to]}, self.say(arrive, verb="arrive")],
                            "fail": self._fall_branch(frm, e),
                        }
                    },
                ]
            )
        return self.fx(pre + [self.say(depart), {"move": [me, e.to]}, self.say(arrive, verb="arrive")])

    def _blockers(self) -> list[Any]:
        """拦着、追着自己的人（``danger.blockers``）：有他们在，走开就是逃（6.1）。"""
        return danger.blockers(self.state, self.me)

    def r_go(self) -> list[Event]:
        e = actions.find_exit(self.state, self.defs, self.c, self.p["exit"])
        assert e is not None
        me = self.me
        blockers = self._blockers()
        if blockers:
            # 有人拦着、追着：这一步按逃跑判定（6.4），成了照常过出口（含出图、要判定的出口），不成被追上
            seen = {o.id for o in blockers}
            pursuers = blockers + [o for o in self._pursuers() if o.id not in seen]
            return self._flee_to(e, pursuers, f"⟦{me}⟧想走，被⟦{{top}}⟧拦了下来")
        return self._traverse(e, f"⟦{me}⟧往⟦{e.to}⟧去了", f"⟦{me}⟧来到⟦{e.to}⟧")

    def r_follow(self) -> list[Event]:
        t = self.p["target"]
        if self.c.following == t:
            return self.fx([{"follow": [self.me, None]}, self.say(f"⟦{self.me}⟧不再跟着⟦{t}⟧")])
        return self.fx([{"follow": [self.me, t]}, self.say(f"⟦{self.me}⟧跟上了⟦{t}⟧")])

    def _pursuers(self) -> list[Any]:
        ps = danger.hostiles_toward(self.state, self.me)
        if not ps:
            cap = next((s for s in self.c.statuses if s.kind in ("captive", "bound") and s.source), None)
            if cap is not None and cap.source in self.state.chars:
                g = self.state.chars[cap.source]
                if g.loc == self.c.loc and perception.conscious(g):
                    ps = [g]
        return ps

    def r_flee(self) -> list[Event]:
        e = actions.find_exit(self.state, self.defs, self.c, self.p["exit"])
        assert e is not None
        return self._flee_to(e, self._pursuers(), f"⟦{self.me}⟧想逃，被⟦{{top}}⟧追上")

    def _flee_to(self, e: Exit, pursuers: list[Any], caught_lead: str) -> list[Event]:
        """逃跑判定（6.4）：成了过出口；不成，有人追就被追上（下场按追者的意图），没人追只是没逃开。"""
        me = self.me
        chance = checks.flee_chance(self.state, self.defs, self.c, pursuers, unguarded=not pursuers)
        res = checks.roll_against(self.state, "flee", chance)
        if res.ok:
            pre: list[dict] = [{"follow": [me, None]}]
            if has_status(self.c, "captive"):
                pre.insert(0, {"remove_status": [me, "captive"]})
            return self._traverse(e, f"⟦{me}⟧拔腿往⟦{e.to}⟧逃去", f"⟦{me}⟧逃到了⟦{e.to}⟧", pre)
        if not pursuers:
            return self.fx([self.say(f"⟦{me}⟧慌不择路，没能逃开", outcome="fail", params={"chance": chance})])
        top = max(pursuers, key=lambda x: growth.power(self.state, self.defs, x))
        f = conflict.caught(self.state, self.defs, top, self.c)
        return self.fx(
            conflict.outcome_effects(f)
            + [
                self.say(
                    conflict.fight_text(f, caught_lead.format(top=top.id)),
                    outcome="fail",
                    kinds=[events.kind("harmed", who=me, by=top.id)] if f.outcome != "push" else [],
                    params={"chance": chance, "winner": top.id, "outcome": f.outcome},
                )
            ]
        )

    def r_struggle(self) -> list[Event]:
        me = self.me
        guards = self._pursuers()
        chance = checks.flee_chance(self.state, self.defs, self.c, guards, unguarded=not guards)
        res = checks.roll_against(self.state, "struggle", chance)
        if not res.ok:
            return self.fx([self.say(f"⟦{me}⟧挣了几下，没能挣脱", outcome="fail", params={"chance": chance})])
        if has_status(self.c, "bound"):
            return self.fx([{"remove_status": [me, "bound"]}, self.say(f"⟦{me}⟧挣脱了身上的绳索")])
        return self.fx([{"remove_status": [me, "captive"]}, self.say(f"⟦{me}⟧趁人不备脱了身")])

    def r_hide(self) -> list[Event]:
        me = self.me
        f = self.p.get("feature")
        cover = self.state.locations[self.c.loc].features[f].cover if f else 0
        where = f"⟦{self.c.loc}#{f}⟧" if f else "暗处"
        return self.fx(
            [
                {
                    "check": {
                        "kind": "hide",
                        "who": me,
                        "cover": cover,
                        "ok": [
                            {"add_status": [me, "hidden", {"source": f or me}]},
                            self.say(f"⟦{me}⟧躲进了{where}", hidden=True),
                        ],
                        "fail": [
                            self.say(
                                f"⟦{me}⟧想躲起来，却被人看在眼里",
                                outcome="fail",
                                kinds=[events.kind("spotted", who=me)],
                            )
                        ],
                    }
                }
            ]
        )

    # ---- senses

    def r_look(self) -> list[Event]:
        me = self.me
        effs: list[dict] = [self.say(f"⟦{me}⟧四下打量")]
        if me == self.state.player:
            effs.insert(0, {"mark": ["visited", self.c.loc]})
        return self.fx(effs)

    def r_listen(self) -> list[Event]:
        me, c = self.me, self.c
        lines: list[str] = []
        loc = self.state.locations[c.loc]
        for e in loc.exits:
            if e.to not in self.state.locations:
                continue
            n = sum(1 for o in perception.present(self.state, e.to) if perception.conscious(o))
            if n:
                lines.append(f"⟦{e.to}⟧那边有{'人声' if n == 1 else '好几个人的声音'}")
        learned: list[dict] = []
        for ev in reversed(self.state.events_tail):
            if ev.tick < self.state.tick - 1:
                break
            sp = ev.speech
            if ev.loc == c.loc and sp is not None and sp.whisper and me not in sp.to and ev.actor != me:
                for fid in sp.fact_refs:
                    learned.append({"learn": [me, fid, 0.5, f"told:{ev.actor}"]})
                lines.append(f"隐约听见⟦{ev.actor}⟧低声说的几个字")
        return self.fx(learned + [self.say(f"⟦{me}⟧侧耳细听", params={"heard_lines": lines})])

    def r_examine(self) -> list[Event]:
        me, c = self.me, self.c
        effs: list[dict] = []
        if "target" in self.p:
            t = self.p["target"]
            effs.append(self.say(f"⟦{me}⟧仔细打量⟦{t}⟧"))
            key = f"char:{t}"
        elif "item" in self.p:
            iid = self.p["item"]
            it = self.state.items[iid]
            key = f"item:{iid}"
            if c.literacy >= 1 and ("manual" in it.tags or it.teaches) and me == self.state.player:
                effs.append({"know_term": iid})
                for art in it.teaches:
                    effs.append({"know_term": art})
            for fid in it.props.get("reveals", []):
                effs.append({"learn": [me, fid, 1.0, "saw"]})
            effs.append(self.say(f"⟦{me}⟧细看⟦{iid}⟧"))
        else:
            fid_ = self.p["feature"]
            feat = self.state.locations[c.loc].features[fid_]
            key = f"feature:{c.loc}#{fid_}"
            if predicates.holds(self.state, self.defs, feat.pre, Ctx(self_id=me)):
                for fid in feat.reveals:
                    effs.append({"learn": [me, fid, 1.0, "saw"]})
            effs.append(self.say(f"⟦{me}⟧细看⟦{c.loc}#{fid_}⟧"))
        if me == self.state.player:
            effs.insert(0, {"mark": ["examined", key]})
        return self.fx(effs)

    def r_search(self) -> list[Event]:
        me, c = self.me, self.c
        f = self.p.get("feature")
        loc = self.state.locations[c.loc]
        effs: list[dict] = []
        found: list[str] = []
        for it in self.state.items.values():
            if it.in_world and it.holder is None and it.loc == c.loc and it.feature is not None:
                if f is None or it.feature == f:
                    effs.append({"spawn": [it.id, c.loc]})
                    found.append(f"⟦{it.id}⟧")
        if f is None:
            for e in loc.exits:
                if e.hidden:
                    fid = f"exit:{c.loc}:{e.to}"
                    b = c.beliefs.get(fid)
                    if fid in self.state.facts and (b is None or b.confidence < predicates.BELIEVE):
                        effs.append({"learn": [me, fid, 1.0, "saw"]})
                        found.append(f"一条通往⟦{e.to}⟧的暗路")
        spotted: list[str] = []
        for o in perception.present(self.state, c.loc, exclude=me):
            hs = next((s for s in o.statuses if s.kind == "hidden"), None)
            if hs is not None and (f is None or hs.source == f):
                effs.append({"remove_status": [o.id, "hidden"]})
                spotted.append(o.id)
        where = f"⟦{c.loc}#{f}⟧里" if f else "四处"
        text = f"⟦{me}⟧在{where}翻找" + ("，找到了" + "、".join(found) if found else "，一无所获")
        evs = self.fx(effs + [self.say(text, outcome="ok" if found else "fail")])
        for o in spotted:
            evs += self.fx(
                [
                    self.say(
                        f"⟦{o}⟧被⟦{me}⟧发现了",
                        verb="spot",
                        params={"target": o},
                        kinds=[events.kind("spotted", who=o)],
                    )
                ]
            )
        return evs

    # ---- items

    def r_take(self) -> list[Event]:
        iid = self.p["item"]
        return self.fx([{"give": [None, self.me, iid]}, self.say(f"⟦{self.me}⟧拿起了⟦{iid}⟧")])

    def r_drop(self) -> list[Event]:
        iid = self.p["item"]
        return self.fx([{"give": [self.me, self.c.loc, iid]}, self.say(f"⟦{self.me}⟧放下了⟦{iid}⟧")])

    def r_give(self) -> list[Event]:
        iid, t = self.p["item"], self.p["target"]
        return self.fx([{"give": [self.me, t, iid]}, self.say(f"⟦{self.me}⟧把⟦{iid}⟧交给了⟦{t}⟧")])

    def r_show(self) -> list[Event]:
        iid, t = self.p["item"], self.p["target"]
        it = self.state.items[iid]
        effs: list[dict] = [{"learn": [t, fid, 1.0, "saw"]} for fid in it.props.get("reveals", [])]
        return self.fx(effs + [self.say(f"⟦{self.me}⟧把⟦{iid}⟧亮给⟦{t}⟧看")])

    def r_use(self) -> list[Event]:
        me = self.me
        iid = self.p["item"]
        it = self.state.items[iid]
        t = self.p.get("target", me)
        props = it.props
        effs: list[dict] = []
        kinds: list[dict] = []
        consumable = bool(set(it.tags) & {"food", "poison", "antidote", "medicine"}) or props.get(
            "consumable"
        )
        if "food" in it.tags or props.get("food"):
            effs.append({"eat": t})
        for st in props.get("cures", []):
            tc = self.state.chars[t]
            if any(s.kind == st for s in tc.statuses):
                effs.append({"remove_status": [t, st]})
                if t != me and (st.startswith("poisoned") or st in ("bound", "captive")):
                    kinds.append(events.kind("rescue", who=t, by=me))
                elif t != me:
                    kinds.append(events.kind("helped", who=t, by=me))
        inf = props.get("inflicts")
        if isinstance(inf, dict) and inf.get("status"):
            opts: dict = {"source": me}
            if inf.get("ticks") is not None:
                opts["ticks"] = int(inf["ticks"])
            if inf.get("level") is not None:
                opts["level"] = int(inf["level"])
            effs.append({"add_status": [t, inf["status"], opts]})
        on_use = list(props.get("on_use") or [])
        if consumable:
            effs.append({"remove": iid})
        if "food" in it.tags:
            verb_text = "吃下了"
        elif set(it.tags) & {"poison", "antidote", "medicine"}:
            verb_text = "服下了"
        else:
            verb_text = "用了"
        if t == me:
            text = f"⟦{me}⟧{verb_text}⟦{iid}⟧"
        else:
            text = f"⟦{me}⟧给⟦{t}⟧喂下了⟦{iid}⟧" if consumable else f"⟦{me}⟧对⟦{t}⟧用了⟦{iid}⟧"
        evs = self.fx(effs + [self.say(text, kinds=kinds)])
        if on_use:
            evs += self.fx(on_use, Ctx(self_id=me, roles={"target": t}))
        return evs

    def r_steal(self) -> list[Event]:
        me = self.me
        iid, t = self.p["item"], self.p["target"]
        return self.fx(
            [
                {
                    "check": {
                        "kind": "steal",
                        "who": me,
                        "target": t,
                        "ok": [
                            {"spawn": [iid, me]},
                            self.say(
                                f"⟦{me}⟧悄悄拿走了⟦{t}⟧身上的⟦{iid}⟧",
                                hidden=True,
                                kinds=[events.kind("item_change", who=t, by=me, item=iid)],
                            ),
                        ],
                        "fail": [
                            # 被当场抓住：藏不住了。先解除再记事件——见证人在记事件的那一刻就定了，
                            # 苦主得亲眼看见是谁（他的信任、怒气正是因这件事而变）
                            {"remove_status": [me, "hidden"]},
                            self.say(
                                f"⟦{me}⟧伸手去摸⟦{t}⟧的⟦{iid}⟧，被当场抓住",
                                outcome="fail",
                                kinds=[events.kind("caught_stealing", who=t, by=me)],
                            )
                        ],
                    }
                }
            ]
        )

    # ---- bodies

    def r_help(self) -> list[Event]:
        me, t = self.me, self.p["target"]
        tc = self.state.chars[t]
        effs: list[dict] = []
        k = "helped"
        if has_status(tc, "unconscious"):
            effs.append({"remove_status": [t, "unconscious"]})
            text = f"⟦{me}⟧把⟦{t}⟧唤醒过来"
        elif has_status(tc, "injured"):
            effs.append({"add_status": [t, "tended", {"ticks": 96, "source": me}]})
            text = f"⟦{me}⟧替⟦{t}⟧包扎伤处"
            if any(s.kind == "injured" and s.level >= 3 for s in tc.statuses):
                k = "rescue"
        else:
            effs.append({"add_status": [t, "tended", {"ticks": 96, "source": me}]})
            text = f"⟦{me}⟧扶着⟦{t}⟧，递水照料"
        return self.fx(effs + [self.say(text, kinds=[events.kind(k, who=t, by=me)])])

    def r_restrain(self) -> list[Event]:
        me, t = self.me, self.p["target"]
        tc = self.state.chars[t]
        if (
            danger.restrained(tc)
            or has_status(tc, "captive")
            or any(s.kind == "injured" and s.level >= 3 for s in tc.statuses)
        ):
            return self.fx([{"add_status": [t, "bound", {"source": me}]}, self.say(f"⟦{me}⟧把⟦{t}⟧捆了起来")])
        return self._fight(tc, "restrain", f"⟦{me}⟧扑上去要拿住⟦{t}⟧")

    def r_release(self) -> list[Event]:
        me, t = self.me, self.p["target"]
        return self.fx(
            [
                {"remove_status": [t, "bound"]},
                {"remove_status": [t, "captive"]},
                self.say(f"⟦{me}⟧放开了⟦{t}⟧", kinds=[events.kind("rescue", who=t, by=me)]),
            ]
        )

    def _fight(self, target: Any, intent: str, lead: str) -> list[Event]:
        f = conflict.fight(self.state, self.defs, self.c, target, intent)  # type: ignore[arg-type]
        return self.fx(
            conflict.outcome_effects(f)
            + [
                self.say(
                    conflict.fight_text(f, lead),
                    verb=self.a.verb,
                    loud=True,
                    kinds=conflict.fight_kinds(f),
                    params={"winner": f.winner, "outcome": f.outcome, "chance": f.chance, "roll": f.roll},
                )
            ]
        )

    def r_attack(self) -> list[Event]:
        t = self.state.chars[self.p["target"]]
        intent = self.p.get("intent", "injure")
        lead = f"⟦{self.me}⟧向⟦{t.id}⟧下了杀手" if intent == "kill" else f"⟦{self.me}⟧向⟦{t.id}⟧动手"
        return self._fight(t, intent, lead)

    def r_block(self) -> list[Event]:
        me, t = self.me, self.p["target"]
        return self.fx(
            [{"add_status": [t, "impeded", {"ticks": 2, "source": me}]}, self.say(f"⟦{me}⟧挡在⟦{t}⟧面前")]
        )

    def r_push(self) -> list[Event]:
        me = self.me
        if "feature" in self.p:
            fid = self.p["feature"]
            feat = self.state.locations[self.c.loc].features[fid]
            return self.fx([self.say(f"⟦{me}⟧推了推⟦{self.c.loc}#{fid}⟧")] + list(feat.push))
        t = self.p["target"]
        tc = self.state.chars[t]
        if growth.power(self.state, self.defs, self.c) + 1 >= growth.power(
            self.state, self.defs, tc
        ) or danger.restrained(tc):
            return self.fx(
                [
                    {"add_status": [t, "impeded", {"ticks": 2, "source": me}]},
                    self.say(f"⟦{me}⟧推了⟦{t}⟧一把，⟦{t}⟧踉跄了几步"),
                ]
            )
        return self.fx([self.say(f"⟦{me}⟧推了⟦{t}⟧一把，没能推动", outcome="fail")])

    # ---- growth

    def r_study(self) -> list[Event]:
        me, c = self.me, self.c
        art = self.p["art"]
        hours = int(self.p.get("hours", 1))
        teacher = self.p.get("target")
        res = growth.study_amount(self.state, self.defs, c, art, hours, teacher=teacher is not None)
        src = f"⟦{self.p['item']}⟧" if "item" in self.p else f"⟦{teacher}⟧讲"
        label = "识字" if art == growth.LITERACY else f"⟦{art}⟧"
        pre: list[dict] = []
        if me == self.state.player and art != growth.LITERACY:
            pre.append({"know_term": art})
            if "item" in self.p:
                pre.append({"know_term": self.p["item"]})
        if res.blocked:
            return self.fx(
                pre
                + [
                    self.say(
                        f"⟦{me}⟧捧着{src}琢磨{label}，{res.blocked}",
                        outcome="fail",
                        params={"blocked": res.blocked},
                    )
                ]
            )
        max_level = None if art == growth.LITERACY else 1
        kinds = [events.kind("taught", who=me, by=teacher)] if teacher else []
        before = c.literacy if art == growth.LITERACY else c.arts.get(art, 0)
        self.fx(pre + [{"train": [me, art, res.added, max_level]}])
        after = c.literacy if art == growth.LITERACY else c.arts.get(art, 0)
        text = f"⟦{me}⟧研读{src}，下了一番功夫学{label}" if "item" in self.p else f"⟦{me}⟧听{src}，学{label}"
        if after > before:
            text += "，有了长进"
        return self.fx([self.say(text, kinds=kinds, params={"progress": res.added, "level": after})])

    def r_practice(self) -> list[Event]:
        me, c = self.me, self.c
        art = self.p["art"]
        hours = int(self.p.get("hours", 1))
        res = growth.practice_amount(self.state, self.defs, c, art, hours)
        if res.blocked:
            return self.fx([self.say(f"⟦{me}⟧练了一阵⟦{art}⟧，{res.blocked}", outcome="fail")])
        before = c.arts.get(art, 0)
        self.fx([{"train": [me, art, res.added]}])
        after = c.arts.get(art, 0)
        text = f"⟦{me}⟧练习⟦{art}⟧" + ("，又有长进" if after > before else "")
        return self.fx([self.say(text, params={"progress": res.added, "level": after})])

    def r_teach(self) -> list[Event]:
        me = self.me
        t = self.p["target"]
        tc = self.state.chars[t]
        art = self.p["art"]
        hours = int(self.p.get("hours", 1))
        res = growth.study_amount(self.state, self.defs, tc, art, hours, teacher=True)
        label = "识字" if art == growth.LITERACY else f"⟦{art}⟧"
        pre: list[dict] = []
        if t == self.state.player and art != growth.LITERACY:
            pre.append({"know_term": art})
        if res.blocked:
            return self.fx(pre + [self.say(f"⟦{me}⟧向⟦{t}⟧讲解{label}，⟦{t}⟧{res.blocked}", outcome="fail")])
        max_level = None if art == growth.LITERACY else 1
        before = tc.literacy if art == growth.LITERACY else tc.arts.get(art, 0)
        self.fx(pre + [{"train": [t, art, res.added, max_level]}])
        after = tc.literacy if art == growth.LITERACY else tc.arts.get(art, 0)
        text = f"⟦{me}⟧向⟦{t}⟧传授{label}" + ("，⟦" + t + "⟧有了长进" if after > before else "")
        return self.fx(
            [
                self.say(
                    text,
                    kinds=[events.kind("taught", who=t, by=me)],
                    params={"progress": res.added, "level": after},
                )
            ]
        )

    # ---- time

    def r_wait(self) -> list[Event]:
        return self.fx([self.say(f"⟦{self.me}⟧静静等着")])

    def r_rest(self) -> list[Event]:
        me = self.me
        ticks = int(self.p.get("hours", 1)) * 8
        return self.fx(
            [
                {"rest": [me, ticks]},
                {"add_status": [me, "asleep", {"ticks": ticks}]},
                self.say(f"⟦{me}⟧找了个地方歇下"),
            ]
        )

    # ---- situations

    def r_setpiece(self) -> list[Event]:
        sid, name = self.p["situation"], self.p["name"]
        s = self.state.situations[sid]
        d = self.defs.situations[s.kind]
        a = d.actions[name]
        ctx = Ctx(self_id=self.me, roles=dict(s.roles), situation=sid)
        evs = effects.apply_effects(self.state, self.defs, a.effects, ctx, source=self.source, step=self.step)
        if not evs:
            desc = refs.fill_roles(a.desc or name, s.roles, self.me)
            evs = self.fx(
                [self.say(f"⟦{self.me}⟧{desc}" if not desc.startswith("⟦") else desc, verb=name)], ctx
            )
        return evs


__all__ = [
    "apply",
    "run_effects",
    "settle_offscreen",
    "resolve_action",
    "mark_offscreen",
    "snapshot",
    "Snapshot",
    "R",
    "ActionInst",
    "Claim",
]
