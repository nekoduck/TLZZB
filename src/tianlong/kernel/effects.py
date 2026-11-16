"""效果（方案 5.3）：``apply_effects`` 是改写世界状态的唯一入口。

效果列表里写了 ``emit`` 时，其他效果（受伤、死亡、易手……）的事件性质并入那条事件；
没写 ``emit`` 时，这些效果各自生成一条中性的事件（例如「⟦x⟧受了伤」）。
跟随者的移动总是单独成一条事件。

除 5.3 的效果外，内核自己还用到几个（只增不减）：
``follow``、``mood``、``warn``、``know_term``、``mark``、``step_state``。
开局布置与上层记账另有 ``request``、``superior``、``dequeue``、``turn``、``offer``、``fact``、``mark_char``。

``check`` 可以写 ``adverse: true``：这是一次「成了反而坏事」的判定（例如看押的人趁隙逃脱），
``--lucky`` 基线跑（旗标 ``__lucky__``）里它一律不成（见 ``checks.roll_against``）。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from tianlong.kernel import checks, danger, events, growth, memory, mood, perception, predicates, refs
from tianlong.kernel.defs import Defs, SituationDef
from tianlong.kernel.predicates import KernelError, flag_key, loc_of, resolve_id
from tianlong.kernel.state import (
    Agenda,
    Belief,
    Character,
    ChronicleEntry,
    Ctx,
    Divergence,
    Event,
    Fact,
    MemoryItem,
    Relation,
    Request,
    SituationState,
    Speech,
    Status,
    Step,
    Thread,
    WorldState,
    get_status,
    has_status,
    is_alive,
    log_violation,
    next_id,
)


class EffectError(KernelError):
    pass


# 每个效果键的参数形状，给 content/validate.py 做语法检查（见 predicates.PREDICATE_SHAPES 的写法）。
# 列表里以 '?' 结尾的类型可以省略或写 null。
EFFECT_SHAPES: dict[str, Any] = {
    "set_flag": ["flagname", "any"],
    "move": ["char", "loc"],
    "enter": ["char", "loc"],
    "leave": "char",
    "give": ["char?", "charloc", "item"],
    "spawn": ["item", "charloc"],
    "remove": "item",
    "add_status": ["char", "str", "opts?"],
    "remove_status": ["char", "str"],
    "role": ["char", "str", "addremove"],
    "rel": ["char", "char", "relfield", "int"],
    "learn": ["char", "fact", "num?", "str?"],
    "train": ["char", "art", "int", "int?"],
    "art": ["char", "art", "int"],
    "stat": ["char", "statfield", "int"],
    "step": ["char", "stepdef"],
    "replan": "char",
    "open": ["situation", "loc?", "roles?"],
    "close": "str",
    "thread": "thread",
    "close_thread": "str",
    "emit": "emit",
    "injure": ["char", "int", "char?"],
    "kill": "kill",
    "check": "check",
    "schedule": ["int", "effects", "pred?"],
    "scene": "scene",
    "trace": ["loc", "str"],
    "end": "endreason",
    # 内核新增
    "follow": ["char", "char?"],
    "mood": ["char", "moodkind", "int"],
    "warn": "str",
    "know_term": "str",
    "mark": ["marklist", "str"],
    "step_state": ["char", "str", "stepstate"],
    "eat": "char",
    "rest": ["char", "int"],
    # 上层模块要用的记账类效果（D-078）：让 minds/world/storyteller 也只经效果改状态
    "reflect": ["char", "str", "any?", "int?"],
    "impression": ["char", "char", "str"],
    "agenda": ["char", "any"],
    "progress": ["char", "int"],
    "chronicle": "any",
    "diverge": "any",
    "teller": ["str", "any"],
    # 开局布置与上层记账（K4、K18、K20）：测试、评测集的布景和 game / world 的记账也只经效果改状态
    "request": "requestdef",
    "superior": ["char", "char?"],
    "dequeue": "char",
    "turn": "int",
    "offer": "options",
    "fact": "factdef",
    "mark_char": ["char", "charmark", "int?"],
}

REQUEST_KINDS = ("request", "plead", "order", "question")
REQUEST_KEYS = frozenset(
    {
        "id",
        "frm",
        "to",
        "kind",
        "action",
        "about",
        "action_class",
        "evidence",
        "fact_refs",
        "leverage",
        "attempts",
        "status",
        "condition",
    }
)
REQUEST_STATUSES = ("open", "agreed", "refused", "countered", "expired")
FACT_KEYS = frozenset({"id", "text", "tags", "gossip", "contradicts", "known_by"})
CHAR_MARKS = ("reflect_mark", "last_replan")  # mark_char 只许改这两个记账字段
FOLLOW_MOVE = "follow_move"  # 跟着别人换了地方的那条事件的动词（「开始跟随」仍是 follow）

CHECK_KINDS = ("flee", "hide", "steal", "climb", "descend", "leap", "fall", "death_save", "chance")
STAT_RANGES: dict[str, tuple[int, int]] = {
    "neili": (0, 100),
    "literacy": (0, 3),
    "luck": (0, 20),
    "aptitude": (0, 3),
    "base_power": (0, 10),
}
CAPTURE_KINDS = ("bound", "captive")
INJURY_MAX = 3
TRACES_MAX = 10
REST_UNIT = 16  # 歇息满 16 刻解除疲惫（6.9）


def parse(eff: Any) -> tuple[str, Any]:
    if not isinstance(eff, dict) or len(eff) != 1:
        raise EffectError(f"bad effect {eff!r}")
    key, arg = next(iter(eff.items()))
    if key not in EFFECT_SHAPES:
        raise EffectError(f"unknown effect {key!r}")
    return key, arg


def apply_effects(
    state: WorldState,
    defs: Defs,
    effects: list[dict],
    ctx: Ctx | None = None,
    *,
    source: str,
    step: str | None = None,
    offscreen: bool = False,
) -> list[Event]:
    run = Run(state, defs, ctx or Ctx(), source=source, step=step, offscreen=offscreen)
    run.run_list(effects)
    return run.events


class Run:
    """一次效果执行：记下产生的事件与掷过的判定。"""

    def __init__(
        self,
        state: WorldState,
        defs: Defs,
        ctx: Ctx,
        *,
        source: str,
        step: str | None = None,
        offscreen: bool = False,
    ) -> None:
        self.state = state
        self.defs = defs
        self.ctx = ctx
        self.source = source
        self.step = step
        self.offscreen = offscreen
        self.events: list[Event] = []
        self.checks: list[checks.CheckResult] = []
        self._merge: list[bool] = []
        self._pending: list[list[dict]] = []

    # ------------------------------------------------------------ plumbing

    def rid(self, arg: Any) -> Any:
        return resolve_id(arg, self.state, self.ctx)

    def char(self, arg: Any) -> Character:
        cid = self.rid(arg)
        c = self.state.chars.get(cid) if isinstance(cid, str) else None
        if c is None:
            raise EffectError(f"unknown character {arg!r} -> {cid!r}")
        return c

    def loc(self, arg: Any) -> str:
        loc = loc_of(arg, self.state, self.ctx)
        if loc is None or loc not in self.state.locations:
            raise EffectError(f"unknown location {arg!r}")
        return loc

    def soft_fail(self, why: str) -> None:
        log_violation(self.state, f"effect skipped: {why}")

    def run_list(self, effs: list[dict] | None) -> None:
        if not effs:
            return
        if not isinstance(effs, list):
            raise EffectError(f"effects must be a list, got {effs!r}")
        has_emit = any(isinstance(e, dict) and "emit" in e for e in effs)
        self._merge.append(has_emit)
        self._pending.append([])
        last: Event | None = None
        try:
            for eff in effs:
                key, arg = parse(eff)
                if key == "emit":
                    kinds = self._pending[-1]
                    self._pending[-1] = []
                    last = self._emit(arg, kinds)
                else:
                    getattr(self, f"e_{key}")(arg)
            leftover = self._pending[-1]
            if leftover and last is not None:
                events.add_kinds(self.state, self.defs, last, leftover)
        finally:
            self._merge.pop()
            self._pending.pop()

    def implicit(
        self,
        *,
        actor: str | None,
        verb: str,
        text: str,
        loc: str,
        kinds: list[dict] | None = None,
        standalone: bool = False,
        params: dict | None = None,
        loud: bool = False,
    ) -> None:
        """一个效果自带的事件：本列表写了 emit 就只并入性质，否则单独记一条。"""
        if self._merge and self._merge[-1] and not standalone:
            self._pending[-1].extend(kinds or [])
            return
        self.events.append(
            events.emit(
                self.state,
                self.defs,
                actor=actor,
                verb=verb,
                text=text,
                loc=loc,
                kinds=kinds,
                source=self.source,
                step=self.step,
                offscreen=self.offscreen,
                params=params,
                loud=loud,
            )
        )

    # ------------------------------------------------------------ emit

    def _emit(self, arg: dict, extra_kinds: list[dict]) -> Event:
        if not isinstance(arg, dict):
            raise EffectError(f"emit: expects a mapping, got {arg!r}")
        actor = self.rid(arg.get("actor")) if arg.get("actor") is not None else None
        if actor is not None and actor not in self.state.chars:
            raise EffectError(f"emit: unknown actor {actor!r}")
        text = refs.fill_roles(str(arg.get("text", "")), self.ctx.roles, self.ctx.self_id)
        loc = None
        if arg.get("loc") is not None:
            loc = self.loc(arg["loc"])
        elif actor is not None:
            loc = self.state.chars[actor].loc
        elif self.ctx.situation and self.ctx.situation in self.state.situations:
            loc = self.state.situations[self.ctx.situation].loc
        else:
            for cid in self.ctx.roles.values():
                if cid in self.state.chars:
                    loc = self.state.chars[cid].loc
                    break
        if loc is None:
            raise EffectError("emit: cannot place the event")
        kinds: list[dict] = []
        for k in arg.get("kinds") or []:
            kk = dict(k)
            for f in ("who", "by"):
                if f in kk:
                    kk[f] = self.rid(kk[f])
            kinds.append(kk)
        kinds.extend(extra_kinds)
        params = {}
        for pk, pv in (arg.get("params") or {}).items():
            params[pk] = self.rid(pv) if isinstance(pv, str) else pv
        ev = events.emit(
            self.state,
            self.defs,
            actor=actor,
            verb=str(arg.get("verb", "event")),
            text=text,
            loc=loc,
            params=params,
            outcome=str(arg.get("outcome", "ok")),
            source=self.source,
            step=self.step,
            offscreen=self.offscreen,
            loud=bool(arg.get("loud", False)),
            hidden=bool(arg.get("hidden", False)),
            kinds=kinds,
            sound=arg.get("sound"),
            overheard=arg.get("overheard"),
            speech=arg.get("speech"),
        )
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------ flags & places

    def e_set_flag(self, arg: Any) -> None:
        if not isinstance(arg, list) or len(arg) != 2:
            raise EffectError(f"set_flag: bad arg {arg!r}")
        self.state.flags[flag_key(str(arg[0]), self.ctx)] = arg[1]

    def e_move(self, arg: Any) -> None:
        c = self.char(arg[0])
        to = self.loc(arg[1])
        move_char(self, c, to)

    def e_enter(self, arg: Any) -> None:
        c = self.char(arg[0])
        to = self.loc(arg[1])
        if not is_alive(c):
            self.soft_fail(f"enter: {c.id} is dead")
            return
        c.onstage = True
        c.loc = to
        self.implicit(actor=c.id, verb="enter", text=f"⟦{c.id}⟧来到⟦{to}⟧", loc=to)
        if c.id == self.state.player:
            recompute_scene(self.state, self.defs)

    def e_leave(self, arg: Any) -> None:
        c = self.char(arg)
        if not c.onstage:
            return
        loc = c.loc
        self.implicit(actor=c.id, verb="leave", text=f"⟦{c.id}⟧离开了这一带", loc=loc)
        c.onstage = False
        stop_following(self.state, c)
        for o in self.state.chars.values():
            if o.following == c.id:
                stop_following(self.state, o)
        _drop_roles(self.state, self.defs, c.id)

    def e_follow(self, arg: Any) -> None:
        c = self.char(arg[0])
        target = self.rid(arg[1]) if len(arg) > 1 else None
        if not target:
            stop_following(self.state, c)
            return
        c.following = target

    def e_trace(self, arg: Any) -> None:
        loc = self.loc(arg[0])
        L = self.state.locations[loc]
        L.traces.append(refs.fill_roles(str(arg[1]), self.ctx.roles, self.ctx.self_id))
        if len(L.traces) > TRACES_MAX:
            del L.traces[: len(L.traces) - TRACES_MAX]

    def e_scene(self, arg: Any) -> None:
        if arg not in ("calm", "tense"):
            raise EffectError(f"scene: bad mode {arg!r}")
        self.state.scene_mode = arg

    def e_end(self, arg: Any) -> None:
        if arg not in ("closed", "dead", "left"):
            raise EffectError(f"end: bad reason {arg!r}")
        self.state.ended = arg

    def e_mark(self, arg: Any) -> None:
        which, key = arg
        lst = {"visited": self.state.visited, "examined": self.state.examined}.get(which)
        if lst is None:
            raise EffectError(f"mark: bad list {which!r}")
        if key not in lst:
            lst.append(key)

    def e_warn(self, arg: Any) -> None:
        danger.mark_warned(self.state, str(arg))

    def e_know_term(self, arg: Any) -> None:
        perception.learn_term(self.state, str(self.rid(arg)))

    # ------------------------------------------------------------ items

    def item(self, arg: Any) -> Any:
        iid = self.rid(arg)
        it = self.state.items.get(iid) if isinstance(iid, str) else None
        if it is None:
            raise EffectError(f"unknown item {arg!r}")
        return it

    def e_give(self, arg: Any) -> None:
        if not isinstance(arg, list) or len(arg) != 3:
            raise EffectError(f"give: bad arg {arg!r}")
        frm = self.rid(arg[0]) if arg[0] is not None else None
        to = self.rid(arg[1])
        it = self.item(arg[2])
        if not it.in_world:
            self.soft_fail(f"give: {it.id} is not in the world")
            return
        if frm is not None and it.holder != frm:
            self.soft_fail(f"give: {it.id} is not held by {frm}")
            return
        if frm is None and it.holder is not None:
            self.soft_fail(f"give: {it.id} is held by {it.holder}")
            return
        place_item(self, it.id, to)
        if to in self.state.chars:
            kinds = [events.kind("item_change", who=frm, by=to, item=it.id)]
            if frm is not None and frm != to:
                kinds.append(events.kind("gift", who=to, by=frm))
                text = f"⟦{frm}⟧把⟦{it.id}⟧交给了⟦{to}⟧"
            else:
                text = f"⟦{to}⟧拿起了⟦{it.id}⟧"
            self.implicit(
                actor=frm or to,
                verb="give" if frm else "take",
                text=text,
                loc=self.state.chars[to].loc,
                kinds=kinds,
            )
        elif frm is not None:
            self.implicit(
                actor=frm,
                verb="drop",
                text=f"⟦{frm}⟧放下了⟦{it.id}⟧",
                loc=self.state.chars[frm].loc,
                kinds=[events.kind("item_change", who=frm, item=it.id)],
            )

    def e_spawn(self, arg: Any) -> None:
        it = self.item(arg[0])
        dest = self.rid(arg[1])
        it.in_world = True
        place_item(self, it.id, dest)

    def e_remove(self, arg: Any) -> None:
        it = self.item(arg)
        _detach(self.state, it.id)
        it.holder = None
        it.loc = None
        it.feature = None
        it.in_world = False

    # ------------------------------------------------------------ statuses & bodies

    def e_add_status(self, arg: Any) -> None:
        c = self.char(arg[0])
        kind = str(arg[1])
        opts = arg[2] if len(arg) > 2 and isinstance(arg[2], dict) else {}
        add_status(self, c, kind, opts)

    def e_remove_status(self, arg: Any) -> None:
        c = self.char(arg[0])
        kind = str(arg[1])
        if not has_status(c, kind):
            return
        c.statuses = [s for s in c.statuses if s.kind != kind]
        if kind in CAPTURE_KINDS or kind.startswith("poisoned"):
            self.implicit(
                actor=None,
                verb="relieved",
                text=_RELIEF_TEXT.get(kind, "⟦{c}⟧脱了困").format(c=c.id),
                loc=c.loc,
                kinds=[events.kind("relieved", who=c.id, by=self.ctx.self_id)],
            )

    def e_injure(self, arg: Any) -> None:
        c = self.char(arg[0])
        by = self.rid(arg[2]) if len(arg) > 2 and arg[2] else self.ctx.self_id
        injure(self, c, int(arg[1]), by=by)

    def e_kill(self, arg: Any) -> None:
        if isinstance(arg, list):
            c = self.char(arg[0])
            cause = str(self.rid(arg[1])) if len(arg) > 1 and arg[1] else None
        else:
            c = self.char(arg)
            cause = None
        by = cause if cause in self.state.chars else self.ctx.self_id
        kill(self, c, by=by, cause=cause)

    def e_eat(self, arg: Any) -> None:
        c = self.char(arg)
        c.last_meal = self.state.tick
        c.statuses = [s for s in c.statuses if s.kind != "hungry"]

    def e_rest(self, arg: Any) -> None:
        """歇息 ticks 刻：满 16 刻解除疲惫；有人照料时每 16 刻伤势减一级。"""
        c = self.char(arg[0])
        ticks = int(arg[1])
        c.awake_since = self.state.tick + ticks
        if ticks >= REST_UNIT:
            c.statuses = [s for s in c.statuses if s.kind != "exhausted"]
        inj = get_status(c, "injured")
        if inj is not None and (has_status(c, "tended") or inj.level == 1):
            # 有人照料时每 16 刻减一级；轻伤（1 级）独自歇满 16 刻也能好（D-077）。
            heal = ticks // REST_UNIT
            if heal >= inj.level:
                c.statuses = [s for s in c.statuses if s.kind != "injured"]
            elif heal:
                inj.level -= heal

    def e_mood(self, arg: Any) -> None:
        c = self.char(arg[0])
        mood.adj_mood(c, str(arg[1]), int(arg[2]))

    # ------------------------------------------------------------ minds & growth

    def e_role(self, arg: Any) -> None:
        c = self.char(arg[0])
        role, op = str(arg[1]), arg[2]
        if op in ("add", True):
            if role not in c.roles:
                c.roles.append(role)
        elif op in ("remove", False):
            if role in c.roles:
                c.roles.remove(role)
        else:
            raise EffectError(f"role: bad op {op!r}")

    def e_rel(self, arg: Any) -> None:
        a = self.char(arg[0])
        b = self.char(arg[1])
        field = str(arg[2])
        if field not in mood.REL_RANGES:
            raise EffectError(f"rel: bad field {field!r}")
        mood.adj_rel(self.state, a.id, b.id, field, int(arg[3]))

    def e_learn(self, arg: Any) -> None:
        c = self.char(arg[0])
        fid = self.rid(arg[1])
        conf = float(arg[2]) if len(arg) > 2 and arg[2] is not None else 1.0
        src = str(arg[3]) if len(arg) > 3 and arg[3] else "inferred"
        learn(self.state, c, fid, conf, src, self.defs)

    def e_train(self, arg: Any) -> None:
        c = self.char(arg[0])
        art = str(arg[1])
        if art != growth.LITERACY and art not in self.defs.arts:
            raise EffectError(f"train: unknown art {art!r}")
        max_level = int(arg[3]) if len(arg) > 3 and arg[3] is not None else None
        growth.apply_progress(self.defs, c, art, int(arg[2]), max_level=max_level)

    def e_art(self, arg: Any) -> None:
        c = self.char(arg[0])
        art = str(arg[1])
        if art not in self.defs.arts:
            raise EffectError(f"art: unknown art {art!r}")
        n = max(0, min(int(arg[2]), len(self.defs.arts[art].stages)))
        c.arts[art] = max(c.arts.get(art, 0), n)

    def e_stat(self, arg: Any) -> None:
        c = self.char(arg[0])
        field = str(arg[1])
        if field not in STAT_RANGES:
            raise EffectError(f"stat: bad field {field!r}")
        lo, hi = STAT_RANGES[field]
        setattr(c, field, max(lo, min(hi, getattr(c, field) + int(arg[2]))))

    def e_step(self, arg: Any) -> None:
        c = self.char(arg[0])
        sd = dict(arg[1])
        sd.setdefault("id", next_id(self.state, "S"))
        sd.setdefault("text", str(sd.get("do", {}).get("verb", "")))
        sd.setdefault("origin", "rule")
        try:
            st = Step.model_validate(sd)
        except ValidationError as e:
            raise EffectError(f"step: bad step {arg[1]!r}: {e}") from e
        insert_step(self.state, c, st)

    def e_step_state(self, arg: Any) -> None:
        c = self.char(arg[0])
        set_step_state(self.state, c, str(arg[1]), str(arg[2]))

    def e_agenda(self, arg: Any) -> None:
        """整份替换此人的打算（重新打算落地，8.3 第 5 步）；第二项为 null 表示清空。"""
        c = self.char(arg[0])
        if arg[1] is None:
            c.agenda = None
            return
        data = arg[1] if isinstance(arg[1], dict) else arg[1].model_dump()
        try:
            c.agenda = Agenda.model_validate(data)
        except ValidationError as e:
            raise EffectError(f"agenda: bad agenda {arg[1]!r}: {e}") from e

    def e_progress(self, arg: Any) -> None:
        """给此人当前这一步记下花了多少刻（离场结算，8.2）；写 0 表示从头算。"""
        c = self.char(arg[0])
        if c.agenda is None:
            return
        n = int(arg[1])
        c.agenda.progress = 0 if n == 0 else max(0, c.agenda.progress + n)

    def e_reflect(self, arg: Any) -> None:
        """夜间反思（7.10）：写一条 kind=reflection 的记忆。"""
        c = self.char(arg[0])
        about = arg[2] if len(arg) > 2 and arg[2] is not None else []
        if isinstance(about, str):
            about = [about]
        sal = int(arg[3]) if len(arg) > 3 and arg[3] is not None else 6
        memory.write(
            self.state,
            c,
            MemoryItem(
                tick=self.state.tick,
                kind="reflection",
                text=str(arg[1]),
                entities=[str(self.rid(a)) for a in about],
                salience=max(1, min(10, sal)),
            ),
        )

    def e_impression(self, arg: Any) -> None:
        """一句印象，只给模型看（5.1 Relation.impression）。数值不动。"""
        a = self.char(arg[0])
        b = self.char(arg[1])
        r = a.rel.get(b.id)
        if r is None:
            a.rel[b.id] = Relation(impression=str(arg[2]))
        else:
            r.impression = str(arg[2])

    def e_chronicle(self, arg: Any) -> None:
        d = dict(arg)
        d.setdefault("tick", self.state.tick)
        d["text"] = refs.fill_roles(str(d.get("text", "")), self.ctx.roles, self.ctx.self_id)
        try:
            self.state.chronicle.append(ChronicleEntry.model_validate(d))
        except ValidationError as e:
            raise EffectError(f"chronicle: bad entry {arg!r}: {e}") from e

    def e_diverge(self, arg: Any) -> None:
        d = dict(arg)
        d.setdefault("tick", self.state.tick)
        if any(x.mark == d.get("mark") for x in self.state.divergences):
            return
        try:
            self.state.divergences.append(Divergence.model_validate(d))
        except ValidationError as e:
            raise EffectError(f"diverge: bad entry {arg!r}: {e}") from e

    def e_teller(self, arg: Any) -> None:
        """讲述者的记账：张力、上次出牌的时刻、今日已出几张（5.1 WorldState.storyteller）。"""
        self.state.storyteller[str(arg[0])] = arg[1]

    def e_replan(self, arg: Any) -> None:
        c = self.char(arg)
        events.queue_replan(self.state, c.id)

    # ------------------------------------------------------------ setup & bookkeeping (K4, K18, K20)

    def e_request(self, arg: Any) -> None:
        """建一桩请求并立即裁决（6.6）：算分、凭据、筹码、重提都与 ``speech.make_request`` 相同。

        ``{request: {frm, to, action: {verb, params}, kind?, action_class?, evidence?: [物件],
        fact_refs?: [事实], leverage?: {reward, urgent, threat}, attempts?, status?, condition?, id?}}``。
        ``kind: question`` 是一个待答的问题（``about`` 写问的是什么）。``status: agreed`` 按答应处理
        （那件事插到对方打算的最前面，并记一桩许诺）；``countered`` 要带 ``condition``。不产生事件。
        """
        from tianlong.kernel import speech

        if not isinstance(arg, dict) or set(arg) - REQUEST_KEYS:
            raise EffectError(f"request: bad arg {arg!r}")
        frm = self.char(arg.get("frm", "self"))
        to = self.char(arg.get("to"))
        if frm.id == to.id:
            raise EffectError("request: frm and to are the same person")
        kind = str(arg.get("kind", "request"))
        if kind not in REQUEST_KINDS:
            raise EffectError(f"request: bad kind {kind!r}")
        if kind == "question":
            about = self.rid(arg.get("about"))
            r = Request(
                id=next_id(self.state, "R"),
                frm=frm.id,
                to=to.id,
                action={"verb": "say", "params": {"act": "tell", "about": about}},
                action_class="trivial",
                verdict="may",
                kind="question",
                created=self.state.tick,
            )
            self.state.requests[r.id] = r
        else:
            action = arg.get("action")
            if not isinstance(action, dict) or not action.get("verb"):
                raise EffectError(f"request: needs action {{verb, params}}, got {action!r}")
            params = {k: self.rid(v) for k, v in dict(action.get("params") or {}).items()}
            sp = Speech(
                act=kind,
                to=[to.id],
                ask_action={"verb": str(action["verb"]), "params": params},
                action_class=arg.get("action_class"),
                evidence=[str(self.rid(x)) for x in arg.get("evidence") or []],
                fact_refs=[str(self.rid(x)) for x in arg.get("fact_refs") or []],
                leverage=dict(arg.get("leverage") or {}),
            )
            r, _nag = speech.make_request(self.state, self.defs, frm, to, sp, kind)
            if arg.get("attempts") is not None:
                r.attempts = max(1, int(arg["attempts"]))
                speech.score_request(self.state, self.defs, r)
        new_id = arg.get("id")
        if new_id is not None and str(new_id) != r.id:
            new_id = str(new_id)
            if new_id in self.state.requests:
                raise EffectError(f"request: id {new_id!r} is taken")
            del self.state.requests[r.id]
            r.id = new_id
            self.state.requests[new_id] = r
        status = arg.get("status")
        if status is None or status == "open":
            return
        if status not in REQUEST_STATUSES:
            raise EffectError(f"request: bad status {status!r}")
        if status == "agreed":
            speech.accept(self.state, self.defs, r)
        elif status == "countered":
            cond = arg.get("condition")
            if not isinstance(cond, dict) or "verb" not in cond:
                raise EffectError("request: status countered needs a condition {verb, params}")
            cparams = {k: self.rid(v) for k, v in dict(cond.get("params") or {}).items()}
            r.condition = {"verb": str(cond["verb"]), "params": cparams}
            r.status = "countered"
        else:
            r.status = status

    def e_superior(self, arg: Any) -> None:
        """``{superior: [c, boss]}``：c 从此听命于 boss；boss 写 null 即不再听命于谁。"""
        c = self.char(arg[0])
        boss = arg[1] if len(arg) > 1 else None
        if boss is None:
            c.superior = None
            return
        b = self.char(boss)
        if b.id == c.id:
            raise EffectError(f"superior: {c.id} cannot be their own superior")
        c.superior = b.id

    def e_dequeue(self, arg: Any) -> None:
        """把人从 ``replan_queue`` 里拿掉（world/replan 处理完这个人之后）。"""
        cid = self.rid(arg)
        if cid in self.state.replan_queue:
            self.state.replan_queue.remove(cid)

    def e_turn(self, arg: Any) -> None:
        """``{turn: n}``：回合数（game 每回合记一次）。"""
        n = int(arg)
        if n < 0:
            raise EffectError(f"turn: bad value {arg!r}")
        self.state.turn = n

    def e_offer(self, arg: Any) -> None:
        """``{offer: [选项…]}``：这一回合摆给玩家的选项（``pending_options``）；空表即清空。"""
        if arg is None:
            arg = []
        if not isinstance(arg, list) or not all(isinstance(x, dict) for x in arg):
            raise EffectError(f"offer: expects a list of mappings, got {arg!r}")
        self.state.pending_options[:] = [dict(x) for x in arg]

    def e_fact(self, arg: Any) -> None:
        """``{fact: {id, text, tags?, gossip?, contradicts?, known_by?}}``：往事实库添一条（同 id 则替换）。

        ``known_by`` 里的人随即确信它（置信度 1.0，来源 saw）。文字里的 ``{角色位}`` 照常替换。
        """
        if not isinstance(arg, dict) or set(arg) - FACT_KEYS or not arg.get("id"):
            raise EffectError(f"fact: bad arg {arg!r}")
        d = {k: v for k, v in arg.items() if k != "known_by"}
        d["id"] = str(d["id"])
        d["text"] = refs.fill_roles(str(d.get("text", "")), self.ctx.roles, self.ctx.self_id)
        try:
            f = Fact.model_validate(d)
        except ValidationError as e:
            raise EffectError(f"fact: bad fact {arg!r}: {e}") from e
        self.state.facts[f.id] = f
        for who in arg.get("known_by") or []:
            learn(self.state, self.char(who), f.id, 1.0, "saw")

    def e_mark_char(self, arg: Any) -> None:
        """``{mark_char: [c, 字段, 值]}``：只许改记账字段 ``reflect_mark``（夜间反思读到第几条记忆）
        与 ``last_replan``（上次重新打算的时刻，可写 null）。"""
        if not isinstance(arg, list) or len(arg) < 2:
            raise EffectError(f"mark_char: bad arg {arg!r}")
        c = self.char(arg[0])
        field = str(arg[1])
        value = arg[2] if len(arg) > 2 else None
        if field not in CHAR_MARKS:
            raise EffectError(f"mark_char: {field!r} is not a bookkeeping field")
        if field == "reflect_mark":
            if value is None or int(value) < 0:
                raise EffectError(f"mark_char: bad reflect_mark {value!r}")
            c.reflect_mark = int(value)
        else:
            c.last_replan = None if value is None else int(value)

    # ------------------------------------------------------------ situations & threads

    def e_open(self, arg: Any) -> None:
        if isinstance(arg, str):
            arg = [arg]
        kind = str(arg[0])
        loc = arg[1] if len(arg) > 1 else None
        roles = arg[2] if len(arg) > 2 and isinstance(arg[2], dict) else {}
        open_situation(self, kind, loc, roles)

    def e_close(self, arg: Any) -> None:
        sid = str(arg)
        if sid in (".", "$self", "self") and self.ctx.situation:
            sid = self.ctx.situation
        if sid in self.state.situations:
            close_situation(self.state, self.defs, sid)
            return
        for s in [s for s in self.state.situations.values() if s.kind == sid]:
            close_situation(self.state, self.defs, s.id)

    def e_thread(self, arg: Any) -> None:
        d = dict(arg)
        d.setdefault("id", next_id(self.state, "T"))
        d["text"] = refs.fill_roles(str(d.get("text", "")), self.ctx.roles, self.ctx.self_id)
        d["refs"] = [self.rid(r) for r in d.get("refs", [])]
        try:
            t = Thread.model_validate(d)
        except ValidationError as e:
            raise EffectError(f"thread: bad thread {arg!r}: {e}") from e
        self.state.threads[t.id] = t

    def e_close_thread(self, arg: Any) -> None:
        t = self.state.threads.get(str(arg))
        if t is not None:
            t.closed = True

    # ------------------------------------------------------------ dice & time

    def e_check(self, arg: Any) -> None:
        if not isinstance(arg, dict) or "kind" not in arg:
            raise EffectError(f"check: bad arg {arg!r}")
        kind = str(arg["kind"])
        who = self.char(arg.get("who", "self"))
        chance = check_chance(self, kind, who, arg)
        res = checks.roll_against(self.state, kind, chance, adverse=bool(arg.get("adverse")))
        self.checks.append(res)
        sub = Run(
            self.state,
            self.defs,
            self.ctx.with_roles(checked=who.id),
            source=self.source,
            step=self.step,
            offscreen=self.offscreen,
        )
        sub.run_list(arg.get("ok") if res.ok else arg.get("fail"))
        self.events.extend(sub.events)
        self.checks.extend(sub.checks)

    def e_schedule(self, arg: Any) -> None:
        if not isinstance(arg, list) or len(arg) < 2:
            raise EffectError(f"schedule: bad arg {arg!r}")
        delay = int(arg[0])
        effs = arg[1]
        pre = arg[2] if len(arg) > 2 else None
        self.state.scheduled.append(
            {
                "at": self.state.tick + delay,
                "effects": effs,
                "pre": pre,
                "self": self.ctx.self_id,
                "roles": dict(self.ctx.roles),
                "situation": self.ctx.situation,
                "source": self.source,
            }
        )


_RELIEF_TEXT = {
    "bound": "⟦{c}⟧身上的绳索解开了",
    "captive": "⟦{c}⟧不再被人看押",
}


# ---------------------------------------------------------------- helpers used by resolvers


def _detach(state: WorldState, item_id: str) -> None:
    it = state.items[item_id]
    if it.holder and it.holder in state.chars:
        inv = state.chars[it.holder].inventory
        if item_id in inv:
            inv.remove(item_id)


def place_item(run: Run, item_id: str, dest: Any, feature: str | None = None) -> None:
    """把物品放到某人手里或某处地上（内部用；会触发 on_obtain）。"""
    state = run.state
    it = state.items[item_id]
    _detach(state, item_id)
    if isinstance(dest, str) and dest in state.chars:
        it.holder = dest
        it.loc = None
        it.feature = None
        state.chars[dest].inventory.append(item_id)
        obtained = f"obtained:{item_id}"
        on_obtain = it.props.get("on_obtain")
        if on_obtain and not state.flags.get(obtained):
            state.flags[obtained] = True
            sub = Run(
                state, run.defs, Ctx(self_id=dest), source=run.source, step=run.step, offscreen=run.offscreen
            )
            sub.run_list(on_obtain)
            run.events.extend(sub.events)
    else:
        loc = loc_of(dest, state, run.ctx)
        if loc is None or loc not in state.locations:
            raise EffectError(f"cannot place {item_id} at {dest!r}")
        it.holder = None
        it.loc = loc
        it.feature = feature


def move_char(run: Run, c: Character, to: str, *, followers: bool = True) -> None:
    state = run.state
    frm = c.loc
    if frm == to:
        return
    c.loc = to
    if has_status(c, "hidden"):
        c.statuses = [s for s in c.statuses if s.kind != "hidden"]
    run.implicit(actor=c.id, verb="arrive", text=f"⟦{c.id}⟧来到⟦{to}⟧", loc=to)
    if c.id == state.player:
        if to not in state.visited:
            state.visited.append(to)
        recompute_scene(state, run.defs)
    if not followers:
        return
    for f in list(state.chars.values()):
        if f.following != c.id or f.loc != frm or not can_walk(f):
            continue
        f.loc = to
        if has_status(f, "hidden"):
            f.statuses = [s for s in f.statuses if s.kind != "hidden"]
        run.implicit(
            actor=f.id,
            verb=FOLLOW_MOVE,
            text=f"⟦{f.id}⟧跟着⟦{c.id}⟧来到⟦{to}⟧",
            loc=to,
            standalone=True,
            params={"target": c.id, "exit": to, "from": frm},
        )
        if f.id == state.player:
            if to not in state.visited:
                state.visited.append(to)
            recompute_scene(state, run.defs)


def can_walk(c: Character) -> bool:
    if not perception.on_map(c) or not perception.conscious(c):
        return False
    if has_status(c, "bound") or has_status(c, "captive"):
        return False
    s = get_status(c, "injured")
    return s is None or s.level < INJURY_MAX


def add_status(run: Run, c: Character, kind: str, opts: dict | None = None) -> None:
    state = run.state
    opts = opts or {}
    if kind.startswith("poisoned") and has_status(c, "immune_poison"):
        return
    until = opts.get("until")
    if until is None and opts.get("ticks") is not None:
        until = state.tick + int(opts["ticks"])
    level = int(opts.get("level", 1))
    source = run.rid(opts["source"]) if opts.get("source") else run.ctx.self_id
    old = get_status(c, kind)
    if old is not None:
        old.level = max(old.level, level)
        if until is not None:
            old.until = until if old.until is None else max(old.until, until)
        return
    c.statuses.append(Status(kind=kind, until=until, level=level, source=source))
    if kind in CAPTURE_KINDS:
        text = "⟦{c}⟧被捆了起来" if kind == "bound" else "⟦{c}⟧被看押起来"
        run.implicit(
            actor=source if source in state.chars else None,
            verb="captured",
            text=text.format(c=c.id),
            loc=c.loc,
            kinds=[events.kind("captured", who=c.id, by=source)],
        )
    elif kind.startswith("poisoned"):
        run.implicit(
            actor=None,
            verb="poisoned",
            text=f"⟦{c.id}⟧中了毒",
            loc=c.loc,
            kinds=[events.kind("poisoned", who=c.id, by=source)],
        )
    elif kind == "unconscious":
        run.implicit(
            actor=None,
            verb="faint",
            text=f"⟦{c.id}⟧昏了过去",
            loc=c.loc,
            kinds=[events.kind("harmed", who=c.id, by=source)],
        )


def injure(run: Run, c: Character, level: int, *, by: str | None) -> None:
    s = get_status(c, "injured")
    if s is None:
        c.statuses.append(Status(kind="injured", level=max(1, min(INJURY_MAX, level)), source=by))
    else:
        # 伤上加伤（D-076）：更重的一击把伤势提到那一级；不比现有的重，则再加一级。
        s.level = max(1, min(INJURY_MAX, level if level > s.level else s.level + 1))
        s.source = by or s.source
    lvl = get_status(c, "injured").level  # type: ignore[union-attr]
    text = f"⟦{c.id}⟧受了伤" if lvl < INJURY_MAX else f"⟦{c.id}⟧受了重伤"
    run.implicit(
        actor=None, verb="injured", text=text, loc=c.loc, kinds=[events.kind("harmed", who=c.id, by=by)]
    )


def kill(run: Run, c: Character, *, by: str | None, cause: str | None = None) -> None:
    """死亡（ARCHITECTURE 2.1）。对玩家：同一来源第一次降级为重伤（中毒到期除外）。"""
    state = run.state
    if not is_alive(c):
        return
    if c.id == state.player and not (cause or "").startswith("poison"):
        key = cause or by or run.source
        if danger.buffered(state, key):
            danger.mark_warned(state, f"kill:{key}")
            s = get_status(c, "injured")
            if s is None:
                c.statuses.append(Status(kind="injured", level=INJURY_MAX, source=by))
            else:
                s.level = INJURY_MAX
            run.implicit(
                actor=None,
                verb="injured",
                text=f"⟦{c.id}⟧受了重伤，险些丧命",
                loc=c.loc,
                kinds=[events.kind("harmed", who=c.id, by=by)],
            )
            return
    loc = c.loc
    c.statuses = [s for s in c.statuses if s.kind not in ("hidden", "asleep")]
    c.statuses.append(Status(kind="dead", source=by))
    if c.agenda is not None:
        for st in c.agenda.steps:
            if st.state == "pending":
                st.state = "void"
        c.agenda.status = "done"
    for r in state.requests.values():
        if r.status in ("open", "countered") and c.id in (r.frm, r.to):
            r.status = "expired"
    for p in state.promises.values():
        if p.status == "open" and c.id in (p.frm, p.to):
            p.status = "broken"
    for iid in list(c.inventory):
        it = state.items[iid]
        it.holder = None
        it.loc = loc
        it.feature = None
    c.inventory.clear()
    c.following = None
    for o in state.chars.values():
        if o.following == c.id:
            stop_following(state, o)
        if o.id != c.id and (c.id in o.rel or o.superior == c.id or c.superior == o.id):
            events.queue_replan(state, o.id)
    if c.id in state.replan_queue:
        state.replan_queue.remove(c.id)
    run.implicit(
        actor=None, verb="die", text=f"⟦{c.id}⟧死了", loc=loc, kinds=[events.kind("death", who=c.id, by=by)]
    )
    if c.id == state.player:
        state.ended = "dead"


def learn(
    state: WorldState, c: Character, fid: str, conf: float, source: str, defs: Defs | None = None
) -> bool:
    """获知事实：置信度只升不降（除非显式改写）。返回是否新近相信（越过 0.6）。

    给了 ``defs`` 时：新近相信的这件事若与他的所求有关（``events.concerned``），他入重新打算的队列（8.3）。
    """
    if fid not in state.facts:
        raise EffectError(f"learn: unknown fact {fid!r}")
    conf = max(0.0, min(1.0, conf))
    old = c.beliefs.get(fid)
    if old is not None and old.confidence >= conf:
        return False
    c.beliefs[fid] = Belief(confidence=conf, source=source, tick=state.tick)
    if c.id == state.player and conf >= predicates.BELIEVE:
        perception.learn_terms_from_text(state, state.facts[fid].text)
    fresh = conf >= predicates.BELIEVE and (old is None or old.confidence < predicates.BELIEVE)
    if fresh and defs is not None and events.concerned(state, defs, c, fid):
        events.queue_replan(state, c.id)
    return fresh


def stop_following(state: WorldState, c: Character) -> None:
    """不再跟着谁。眼下这一步若正是「跟着那人」（打算里写的，或答应下来的），这一步就此作废，
    他按当时的处境重新打算（8.3）：跟的人走了、死了，或自己被拿住、逃开了，都走这里。"""
    t = c.following
    c.following = None
    ag = c.agenda
    if t is None or ag is None or ag.status != "active" or ag.cursor >= len(ag.steps):
        return
    st = ag.steps[ag.cursor]
    if st.state != "pending" or st.do.get("verb") != "follow":
        return
    if predicates.resolve_id(st.do.get("target"), state, Ctx(self_id=c.id)) == t:
        set_step_state(state, c, st.id, "void")


def insert_step(state: WorldState, c: Character, st: Step) -> None:
    if c.agenda is None:
        c.agenda = Agenda(goal=st.text, steps=[st], cursor=0)
        return
    ag = c.agenda
    ag.steps.insert(ag.cursor, st)
    ag.progress = 0
    ag.status = "active"
    ag.waiting_for = ""


def set_step_state(state: WorldState, c: Character, step_id: str, new: str) -> None:
    ag = c.agenda
    if ag is None:
        return
    for st in ag.steps:
        if st.id == step_id and st.state == "pending":
            st.state = new  # type: ignore[assignment]
            break
    else:
        return
    while ag.cursor < len(ag.steps) and ag.steps[ag.cursor].state != "pending":
        ag.cursor += 1
        ag.progress = 0
    if ag.cursor >= len(ag.steps):
        ag.status = "done"
        events.queue_replan(state, c.id)
    elif new == "void":
        events.queue_replan(state, c.id)


# ---------------------------------------------------------------- situations


def recompute_scene(state: WorldState, defs: Defs) -> None:
    p = state.chars.get(state.player)
    if p is None:
        return
    tense = False
    for s in state.situations.values():
        d = defs.situations.get(s.kind)
        if s.loc == p.loc and d is not None and d.scene == "tense":
            tense = True
            break
    state.scene_mode = "tense" if tense else "calm"


def _drop_roles(state: WorldState, defs: Defs, cid: str) -> None:
    """离图的人不再占情境的角色位：散掉他所在的情境。"""
    for s in [s for s in state.situations.values() if cid in s.roles.values()]:
        close_situation(state, defs, s.id)


def bind_roles(
    state: WorldState,
    defs: Defs,
    d: SituationDef,
    ctx: Ctx,
    loc_arg: Any = None,
    override: dict | None = None,
) -> tuple[str, dict[str, str]] | None:
    """按情境定义绑定角色位并定地点。绑不上返回 None。"""
    override = override or {}
    roles: dict[str, str] = {}
    picks: list[tuple[str, Any]] = []
    for name, spec in d.roles.items():
        if name in override and override[name] is not None:
            v = resolve_id(override[name], state, ctx)
        elif isinstance(spec, str):
            v = resolve_id(spec, state, ctx)
        elif isinstance(spec, dict) and "pick" in spec:
            picks.append((name, spec["pick"]))
            continue
        else:
            raise EffectError(f"situation {d.id}: bad role {name}: {spec!r}")
        if not isinstance(v, str) or v not in state.chars:
            return None
        roles[name] = v
    loc: str | None = None
    if loc_arg is not None:
        loc = loc_of(loc_arg, state, Ctx(ctx.self_id, {**ctx.roles, **roles}, ctx.situation))
    elif d.at:
        if d.at.startswith("$"):
            cid = roles.get(d.at[1:])
            loc = state.chars[cid].loc if cid else None
        else:
            loc = d.at
    else:
        for name in d.roles:
            if name in roles:
                loc = state.chars[roles[name]].loc
                break
    if loc is None or loc not in state.locations:
        return None
    for name, pick in picks:
        where = pick.get("where") if isinstance(pick, dict) else None
        prefer = pick.get("prefer") if isinstance(pick, dict) else None
        chosen = None
        for c in state.chars.values():
            if c.loc != loc or not perception.on_map(c) or c.id in roles.values():
                continue
            pctx = Ctx(ctx.self_id, {**ctx.roles, **roles, "it": c.id}, ctx.situation)
            if predicates.holds(state, defs, where, pctx):
                if prefer is None or predicates.holds(state, defs, prefer, pctx):
                    chosen = c.id  # 合条件的人里，先取合 prefer 的（例如自告奋勇的人）
                    break
                if chosen is None:
                    chosen = c.id
        if chosen is None:
            return None
        roles[name] = chosen
    return loc, roles


def open_situation(
    run: Run, kind: str, loc_arg: Any = None, override: dict | None = None
) -> SituationState | None:
    state, defs = run.state, run.defs
    d = defs.situations.get(kind)
    if d is None:
        raise EffectError(f"open: unknown situation {kind!r}")
    bound = bind_roles(state, defs, d, run.ctx, loc_arg, override)
    if bound is None:
        run.soft_fail(f"open {kind}: roles or place not bindable")
        return None
    loc, roles = bound
    for s in state.situations.values():
        if s.kind == kind and s.loc == loc and s.roles == roles:
            return s
    sid = next_id(state, f"{kind}#")
    s = SituationState(id=sid, kind=kind, loc=loc, roles=roles)
    state.situations[sid] = s
    recompute_scene(state, defs)
    return s


def close_situation(state: WorldState, defs: Defs, sid: str) -> None:
    if sid not in state.situations:
        return
    del state.situations[sid]
    prefix = f"{sid}."
    for k in [k for k in state.flags if k.startswith(prefix)]:
        del state.flags[k]
    void_setpiece_requests(state)
    recompute_scene(state, defs)


def setpiece_gone(state: WorldState, action: dict | None) -> bool:
    """所求之事是某场情境里的一招，而那场情境已经散了（再也做不出）。"""
    if not action or action.get("verb") != "setpiece":
        return False
    name = (action.get("params") or {}).get("situation")
    if not isinstance(name, str) or not name:
        return False
    return not any(name in (s.id, s.kind) for s in state.situations.values())


def void_setpiece_requests(state: WorldState) -> None:
    """情境散了以后：求人在那场情境里做的一招再也做不成。

    还没答复的请求过期；答应了还没做的那一步（``req_<请求>``）作废，请求过期，连带的许诺就此了结
    （事情已经不在了，不算失信：不扣信任、不记「答应的事没有做到」）。已经做完的不动。
    """
    for r in state.requests.values():
        if r.status not in ("open", "countered", "agreed") or not setpiece_gone(state, r.action):
            continue
        if r.status == "agreed":
            c = state.chars.get(r.to)
            ag = c.agenda if c is not None else None
            sid = f"req_{r.id}"
            if c is None or ag is None or not any(st.id == sid and st.state == "pending" for st in ag.steps):
                continue  # 那一步已经做完（或早已作废）
            set_step_state(state, c, sid, "void")
        r.status = "expired"
        for p in state.promises.values():
            if p.request == r.id and p.status == "open":
                p.status = "broken"


def situation_ctx(state: WorldState, sid: str, self_id: str | None = None) -> Ctx:
    s = state.situations[sid]
    return Ctx(self_id=self_id, roles=dict(s.roles), situation=sid)


def close_finished(state: WorldState, defs: Defs, loc: str | None = None) -> list[str]:
    """散掉 exit_when 已成立的情境（resolve 每次结算后调用）。返回散掉的 id。"""
    closed: list[str] = []
    for s in list(state.situations.values()):
        if loc is not None and s.loc != loc:
            continue
        d = defs.situations.get(s.kind)
        if d is None:
            continue
        if d.exit_when is not None and predicates.holds(state, defs, d.exit_when, situation_ctx(state, s.id)):
            close_situation(state, defs, s.id)
            closed.append(s.id)
    return closed


def tick_situations(state: WorldState, defs: Defs) -> list[str]:
    """每个回合调用一次：情境的 turns 加一；exit_when 成立或到了 max_turns 的散场（不替任何人做决定）。"""
    closed = close_finished(state, defs)
    for s in list(state.situations.values()):
        s.turns += 1
        d = defs.situations.get(s.kind)
        if d is not None and d.max_turns is not None and s.turns >= d.max_turns:
            close_situation(state, defs, s.id)
            closed.append(s.id)
    return closed


# ---------------------------------------------------------------- checks


def check_chance(run: Run, kind: str, who: Character, arg: dict) -> int:
    state, defs = run.state, run.defs
    if "chance" in arg and arg["chance"] is not None:
        return checks.clamp(int(arg["chance"]))
    if kind == "flee":
        frm = arg.get("from")
        if frm:
            pursuers = [run.char(x) for x in (frm if isinstance(frm, list) else [frm])]
        else:
            pursuers = danger.hostiles_toward(state, who.id)
        return checks.flee_chance(state, defs, who, pursuers)
    if kind == "hide":
        cover = int(arg.get("cover", 0))
        hostile = danger.hostiles_toward(state, who.id)
        seen = bool(perception.visible_people(state, who))
        return checks.hide_chance(state, defs, who, cover, len(hostile), seen)
    if kind == "steal":
        return checks.steal_chance(state, defs, who, run.char(arg.get("target")))
    if kind in ("climb", "descend", "leap"):
        return checks.climb_chance(state, defs, who)
    if kind == "fall":
        return checks.fall_survive_chance(state, defs, who, loc_of(arg.get("loc") or who.loc, state, run.ctx))
    if kind == "death_save":
        return checks.death_save_chance(who)
    raise EffectError(f"check: unknown kind {kind!r}")
