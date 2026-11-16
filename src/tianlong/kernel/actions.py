"""动词表（方案 6.1）与合法动作枚举（方案 6.2）。

``legal_actions`` 的排序：★ 在前，``wait`` 第二，其余在后。``for_model=True`` 时压到 12 条以内。
参数键：target, target2, exit（出口通往的地点 id）, item, feature（本地景物 id）, art, ticks, hours,
intent（attack：injure / kill）, name 与 situation（setpiece）。
"""

from __future__ import annotations

import heapq
from typing import Any

from pydantic import BaseModel

from tianlong.kernel import bottom, checks, danger, growth, perception, predicates, refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import (
    TICKS_PER_DAY,
    TICKS_PER_HOUR,
    Character,
    Ctx,
    Exit,
    Speech,
    Step,
    WorldState,
    has_status,
    injury_level,
)

MODEL_CAP = 12
WAIT_TICKS = (1, 8)
STUDY_HOURS = (1, 4)
REST_HOURS = (2, 8)
MAX_HOURS = 12
BENIGN_TAGS = ("food", "antidote", "medicine")
CONDITIONS_HELPABLE = ("injured", "unconscious", "hungry", "exhausted")


class ActionInst(BaseModel):
    id: str = ""  # 'a01'…，legal_actions 每次调用时重新编号
    actor: str
    verb: str  # 方案 6.1 的动词，或 'setpiece'
    params: dict
    cost: int  # 刻；紧张场面里为 0
    loud: bool = False
    lethal: bool = False  # 方案 6.10：先警示后结算
    star: bool = False  # 方案 6.2 第 4 步
    tags: list[str] = []
    desc: str  # 一句中性中文描述，实体用 ⟦id⟧ 引用
    signature: str  # verb 与参数的规范串


class VerbSpec(BaseModel):
    name: str
    cost: int | None  # None：按参数定（出口、时长）
    loud: bool = False
    risk: int = 0  # 0 无险 … 3 性命攸关
    resolver: str  # resolve.py 里的函数名
    label: str


VERBS: dict[str, VerbSpec] = {
    v.name: v
    for v in [
        VerbSpec(name="go", cost=None, risk=1, resolver="r_go", label="走"),
        VerbSpec(name="follow", cost=0, resolver="r_follow", label="跟随"),
        VerbSpec(name="flee", cost=0, risk=2, resolver="r_flee", label="逃"),
        VerbSpec(name="hide", cost=1, risk=1, resolver="r_hide", label="躲"),
        VerbSpec(name="look", cost=1, resolver="r_look", label="环视"),
        VerbSpec(name="listen", cost=1, resolver="r_listen", label="细听"),
        VerbSpec(name="examine", cost=1, resolver="r_examine", label="细看"),
        VerbSpec(name="search", cost=2, resolver="r_search", label="翻找"),
        VerbSpec(name="say", cost=1, resolver="r_wait", label="说"),
        VerbSpec(name="take", cost=0, resolver="r_take", label="拿"),
        VerbSpec(name="drop", cost=0, resolver="r_drop", label="放下"),
        VerbSpec(name="give", cost=0, resolver="r_give", label="给"),
        VerbSpec(name="show", cost=0, resolver="r_show", label="出示"),
        VerbSpec(name="use", cost=1, risk=1, resolver="r_use", label="用"),
        VerbSpec(name="steal", cost=1, risk=2, resolver="r_steal", label="偷"),
        VerbSpec(name="help", cost=2, resolver="r_help", label="帮"),
        VerbSpec(name="restrain", cost=1, risk=2, resolver="r_restrain", label="捆"),
        VerbSpec(name="release", cost=1, resolver="r_release", label="放"),
        VerbSpec(name="attack", cost=0, loud=True, risk=3, resolver="r_attack", label="动手"),
        VerbSpec(name="block", cost=0, risk=1, resolver="r_block", label="挡"),
        VerbSpec(name="push", cost=0, risk=1, resolver="r_push", label="推"),
        VerbSpec(name="study", cost=None, resolver="r_study", label="研读"),
        VerbSpec(name="practice", cost=None, resolver="r_practice", label="练习"),
        VerbSpec(name="teach", cost=None, resolver="r_teach", label="传授"),
        VerbSpec(name="wait", cost=None, resolver="r_wait", label="等"),
        VerbSpec(name="rest", cost=None, resolver="r_rest", label="歇息"),
        VerbSpec(name="struggle", cost=1, risk=1, resolver="r_struggle", label="挣扎"),
        VerbSpec(name="setpiece", cost=None, resolver="r_setpiece", label="情境"),
    ]
}


def signature(verb: str, params: dict) -> str:
    return verb + ":" + ",".join(f"{k}={params[k]}" for k in sorted(params))


# ---------------------------------------------------------------- helpers


def can_act(c: Character | None) -> bool:
    return c is not None and perception.on_map(c) and perception.conscious(c)


def tense_for(state: WorldState, c: Character) -> bool:
    p = state.chars.get(state.player)
    return state.scene_mode == "tense" and p is not None and c.loc == p.loc


def exit_known(state: WorldState, c: Character, frm: str, e: Exit) -> bool:
    if not e.hidden:
        return True
    b = c.beliefs.get(f"exit:{frm}:{e.to}")
    return b is not None and b.confidence >= predicates.BELIEVE


def usable_exits(state: WorldState, defs: Defs, c: Character, frm: str | None = None) -> list[Exit]:
    frm = frm or c.loc
    loc = state.locations.get(frm)
    if loc is None:
        return []
    ctx = Ctx(self_id=c.id)
    return [
        e for e in loc.exits if exit_known(state, c, frm, e) and predicates.holds(state, defs, e.pre, ctx)
    ]


def find_exit(state: WorldState, defs: Defs, c: Character, to: str) -> Exit | None:
    for e in usable_exits(state, defs, c):
        if e.to == to:
            return e
    return None


def travel_cost(state: WorldState, c: Character, e: Exit) -> int:
    speed = max(
        (int(state.items[i].props.get("speed", 1)) for i in c.inventory if i in state.items), default=1
    )
    return max(1, e.cost // max(1, speed)) if e.cost > 0 else 0


def edge_text(state: WorldState, defs: Defs, e: Exit) -> str:
    """通往图外的出口怎么说（K10）：图外那头有名字（词表里有这一条）就带上，没有就只说「离开这一带」。"""
    if refs.lookup(state, defs, e.to) is not None:
        return f"离开这一带，往⟦{e.to}⟧去"
    return "离开这一带"


def exit_lethal(state: WorldState, defs: Defs, c: Character, e: Exit) -> bool:
    return e.check is not None and checks.climb_chance(state, defs, c) < danger.LETHAL_CHANCE


def flee_available(state: WorldState, c: Character) -> bool:
    return bool(danger.hostiles_toward(state, c.id)) or has_status(c, "alerted") or has_status(c, "captive")


def path(state: WorldState, defs: Defs, actor: str, frm: str, to: str) -> list[Exit]:
    """最短路（按 cost），跳过此人走不了的出口（不知道的隐藏出口、前提不成立的、通往图外的）。"""
    if frm == to:
        return []
    c = state.chars[actor]
    dist: dict[str, int] = {frm: 0}
    prev: dict[str, tuple[str, Exit]] = {}
    heap: list[tuple[int, str]] = [(0, frm)]
    while heap:
        d, here = heapq.heappop(heap)
        if here == to:
            break
        if d > dist.get(here, 1 << 30):
            continue
        for e in usable_exits(state, defs, c, here):
            if e.edge and e.to != to:
                continue
            if e.to not in state.locations and e.to != to:
                continue
            nd = d + max(1, e.cost)
            if nd < dist.get(e.to, 1 << 30):
                dist[e.to] = nd
                prev[e.to] = (here, e)
                heapq.heappush(heap, (nd, e.to))
    if to not in prev:
        return []
    out: list[Exit] = []
    node = to
    while node != frm:
        here, e = prev[node]
        out.append(e)
        node = here
    out.reverse()
    return out


def step_ready(state: WorldState, defs: Defs, c: Character, st: Step) -> bool:
    if st.start is not None and state.tick < st.start:
        return False
    if st.deadline is not None and state.tick > st.deadline:
        return False
    return predicates.holds(state, defs, st.pre, Ctx(self_id=c.id))


def following_step(state: WorldState, c: Character, st: Step) -> bool:
    """这一步是「跟着某人」，而且他眼下正跟着那人：接下来只要跟着（等满 ``takes``），不必再「跟上」一次。"""
    if st.do.get("verb") != "follow" or c.following is None:
        return False
    return predicates.resolve_id(st.do.get("target"), state, Ctx(self_id=c.id)) == c.following


def matches_do(
    action: ActionInst,
    do: dict,
    ctx: Ctx,
    *,
    state: WorldState | None = None,
    speech: Speech | None = None,
) -> bool:
    """动作是否符合打算一步的 ``do``（ARCHITECTURE 2.4）。``say`` 看 speech。"""

    def rid(v: Any) -> Any:
        if state is not None:
            return predicates.resolve_id(v, state, ctx)
        if v == "self":
            return ctx.self_id
        if isinstance(v, str) and v.startswith("$"):
            return ctx.roles.get(v[1:])
        return v

    verb = do.get("verb")
    if verb is None:
        return False
    if verb == "say":
        if speech is None:
            return False
        if do.get("act") and speech.act != do["act"]:
            return False
        if do.get("to") and rid(do["to"]) not in speech.to:
            return False
        fact = do.get("fact")
        if fact and rid(fact) not in speech.fact_refs:
            return False
        return True
    if action.verb != verb:
        return False
    p = action.params
    for key in ("target", "target2", "item", "exit", "feature", "art", "name"):
        if key in do and rid(do[key]) != p.get(key):
            return False
    if "to" in do and rid(do["to"]) != p.get("exit", p.get("target")):
        return False
    if "situation" in do:
        sid = str(p.get("situation", ""))
        if do["situation"] not in (sid, sid.split("#")[0]):
            return False
    if "intent" in do and do["intent"] != p.get("intent", "injure"):
        return False
    return True


def _hours_text(n: int) -> str:
    from tianlong.kernel import timeflow

    return "两" if n == 2 else timeflow.cn_num(n)


# ---------------------------------------------------------------- build


class _B(BaseModel):
    cost: int
    desc: str
    loud: bool = False
    lethal: bool = False


def _person(state: WorldState, c: Character, tid: Any, *, alive: bool = True) -> Character | None:
    t = state.chars.get(tid) if isinstance(tid, str) else None
    if t is None or t.id == c.id or t.loc != c.loc or not t.onstage:
        return None
    if alive and not perception.on_map(t):
        return None
    if not perception.visible_to(c, t):
        return None
    return t


def _item_visible(state: WorldState, c: Character, iid: Any) -> bool:
    it = state.items.get(iid) if isinstance(iid, str) else None
    if it is None or not it.in_world:
        return False
    if it.holder == c.id:
        return True
    if it.holder is None:
        return it.loc == c.loc and it.feature is None
    h = state.chars.get(it.holder)
    return h is not None and h.loc == c.loc and perception.visible_to(c, h)


def _hours(params: dict) -> int | None:
    h = params.get("hours", 1)
    if not isinstance(h, int) or h < 1 or h > MAX_HOURS:
        return None
    return h


def _teacher_ok(state: WorldState, defs: Defs, teacher: Character, student: Character, art: str) -> bool:
    if not growth.can_teach(state, defs, teacher, student, art):
        return False
    if student.superior == teacher.id:
        return True
    for r in state.requests.values():
        if (
            r.frm == student.id
            and r.to == teacher.id
            and r.status == "agreed"
            and r.action.get("verb") == "teach"
            and (r.action.get("params") or {}).get("art") in (art, None)
        ):
            return True
    return False


def _v(state: WorldState, defs: Defs, c: Character, verb: str, p: dict) -> _B | None:  # noqa: C901
    tense = tense_for(state, c)
    inj = injury_level(c)
    captive = has_status(c, "captive")
    if verb == "go":
        if captive or inj >= 3:
            return None
        e = find_exit(state, defs, c, p.get("exit", ""))
        if e is None:
            return None
        desc = edge_text(state, defs, e) if e.edge else f"往⟦{e.to}⟧去"
        if e.check:
            desc = {
                "descend": f"缒下去，往⟦{e.to}⟧",
                "climb": f"攀上去，往⟦{e.to}⟧",
                "leap": f"纵身跃向⟦{e.to}⟧",
            }.get(e.check, desc)
        return _B(cost=travel_cost(state, c, e), desc=desc, lethal=exit_lethal(state, defs, c, e))
    if verb == "flee":
        if inj >= 3 or not flee_available(state, c):
            return None
        e = find_exit(state, defs, c, p.get("exit", ""))
        if e is None or e.edge:
            return None
        return _B(cost=0, desc=f"拔腿逃往⟦{e.to}⟧", lethal=exit_lethal(state, defs, c, e))
    if verb == "follow":
        t = _person(state, c, p.get("target"))
        if t is None or captive or inj >= 3:
            return None
        if c.following == t.id:
            return _B(cost=0, desc=f"不再跟着⟦{t.id}⟧")
        return _B(cost=0, desc=f"跟着⟦{t.id}⟧走")
    if verb == "hide":
        if has_status(c, "hidden"):
            return None
        f = p.get("feature")
        loc = state.locations[c.loc]
        if f is not None:
            feat = loc.features.get(f)
            if feat is None or feat.cover <= 0:
                return None
            return _B(cost=1, desc=f"躲进⟦{c.loc}#{f}⟧")
        if not has_status(c, "alerted"):
            return None
        return _B(cost=1, desc="就近找个地方躲起来")
    if verb == "look":
        return _B(cost=1, desc="四下看看")
    if verb == "listen":
        return _B(cost=1, desc="侧耳细听")
    if verb == "examine":
        keys = [k for k in ("target", "item", "feature") if k in p]
        if len(keys) != 1:
            return None
        if "target" in p:
            t = state.chars.get(p["target"])
            if (
                t is None
                or t.id == c.id
                or t.loc != c.loc
                or not t.onstage
                or not perception.visible_to(c, t)
            ):
                return None
            return _B(cost=1, desc=f"仔细打量⟦{t.id}⟧")
        if "item" in p:
            if not _item_visible(state, c, p["item"]):
                return None
            return _B(cost=1, desc=f"细看⟦{p['item']}⟧")
        if p["feature"] not in state.locations[c.loc].features:
            return None
        return _B(cost=1, desc=f"细看⟦{c.loc}#{p['feature']}⟧")
    if verb == "search":
        f = p.get("feature")
        if f is not None and f not in state.locations[c.loc].features:
            return None
        return _B(cost=2, desc=f"在⟦{c.loc}#{f}⟧里翻找" if f else "在这里四处翻找")
    if verb == "take":
        it = state.items.get(p.get("item", ""))
        if (
            it is None
            or not it.in_world
            or it.holder is not None
            or it.loc != c.loc
            or it.feature is not None
        ):
            return None
        return _B(cost=0, desc=f"拿起⟦{it.id}⟧")
    if verb in ("drop", "give", "show", "use"):
        it = state.items.get(p.get("item", ""))
        if it is None or it.holder != c.id:
            return None
        if verb == "drop":
            return _B(cost=0, desc=f"放下⟦{it.id}⟧")
        if verb in ("give", "show"):
            t = _person(state, c, p.get("target"))
            if t is None or not perception.conscious(t):
                return None
            if verb == "give":
                return _B(cost=0, desc=f"把⟦{it.id}⟧交给⟦{t.id}⟧")
            return _B(cost=0, desc=f"把⟦{it.id}⟧亮给⟦{t.id}⟧看")
        if "target" in p:
            t = _person(state, c, p["target"])
            if t is None:
                return None
            benign = bool(set(it.tags) & set(BENIGN_TAGS)) and not danger.item_lethal(state, it.id)
            if not benign and not (danger.restrained(t) or has_status(t, "captive")):
                return None
            return _B(cost=1, desc=f"给⟦{t.id}⟧用⟦{it.id}⟧")
        if not (
            it.props.get("on_use")
            or it.props.get("cures")
            or it.props.get("inflicts")
            or set(it.tags) & {"food", "antidote", "medicine", "poison"}
        ):
            return None
        how = (
            "吃"
            if "food" in it.tags
            else "服下"
            if set(it.tags) & {"poison", "antidote", "medicine"}
            else "用"
        )
        return _B(cost=1, desc=f"{how}⟦{it.id}⟧", lethal=danger.item_lethal(state, it.id))
    if verb == "steal":
        t = _person(state, c, p.get("target"))
        it = state.items.get(p.get("item", ""))
        if t is None or it is None or it.holder != t.id:
            return None
        if not perception.item_visible(state, c, it.id):
            return None  # 看不见、也不知道在他身上的东西，无从下手（K9）
        return _B(cost=1, desc=f"偷偷去拿⟦{t.id}⟧身上的⟦{it.id}⟧")
    if verb == "help":
        t = _person(state, c, p.get("target"))
        if t is None:
            return None
        if not any(has_status(t, k) for k in CONDITIONS_HELPABLE) and not any(
            s.kind.startswith("poisoned") for s in t.statuses
        ):
            return None
        return _B(cost=2, desc=f"照料⟦{t.id}⟧")
    if verb == "restrain":
        t = _person(state, c, p.get("target"))
        if t is None or has_status(t, "bound"):
            return None
        return _B(cost=1, desc=f"把⟦{t.id}⟧捆起来", lethal=danger.attack_lethal(state, defs, c, t))
    if verb == "release":
        t = _person(state, c, p.get("target"))
        if t is None or not (has_status(t, "bound") or has_status(t, "captive")):
            return None
        return _B(cost=1, desc=f"放开⟦{t.id}⟧")
    if verb == "attack":
        t = _person(state, c, p.get("target"))
        intent = p.get("intent", "injure")
        if t is None or intent not in ("injure", "kill"):
            return None
        desc = f"向⟦{t.id}⟧下杀手" if intent == "kill" else f"向⟦{t.id}⟧动手"
        return _B(cost=0, desc=desc, loud=True, lethal=danger.attack_lethal(state, defs, c, t))
    if verb == "block":
        t = _person(state, c, p.get("target"))
        if t is None or not perception.conscious(t):
            return None
        return _B(cost=0, desc=f"挡在⟦{t.id}⟧面前")
    if verb == "push":
        if "feature" in p:
            feat = state.locations[c.loc].features.get(p["feature"])
            if feat is None or not feat.push:
                return None
            return _B(cost=0, desc=f"去推⟦{c.loc}#{p['feature']}⟧")
        t = _person(state, c, p.get("target"))
        if t is None:
            return None
        return _B(cost=0, desc=f"推⟦{t.id}⟧一把")
    if verb in ("study", "practice", "teach", "rest"):
        if tense:
            return None
        h = _hours(p)
        if h is None:
            return None
        cost = h * TICKS_PER_HOUR
        ht = _hours_text(h)
        art = p.get("art", "")
        if verb == "rest":
            if danger.hostiles_toward(state, c.id):
                return None
            return _B(cost=cost, desc=f"歇息{ht}个时辰")
        if verb == "practice":
            if growth.art_level(c, art) < 1 or growth.stage_need(defs, art, growth.art_level(c, art)) is None:
                return None
            return _B(cost=cost, desc=f"练习⟦{art}⟧{ht}个时辰")
        if verb == "teach":
            t = _person(state, c, p.get("target"))
            if t is None or not perception.conscious(t) or not _teacher_ok(state, defs, c, t, art):
                return None
            label = "识字" if art == growth.LITERACY else f"⟦{art}⟧"
            return _B(cost=cost, desc=f"向⟦{t.id}⟧传授{label}{ht}个时辰")
        # study
        if "item" in p:
            it = state.items.get(p["item"])
            if it is None or it.holder != c.id or art not in it.teaches or art not in defs.arts:
                return None
            if growth.art_level(c, art) >= 1:
                return None
            return _B(cost=cost, desc=f"研读⟦{it.id}⟧{ht}个时辰")
        t = _person(state, c, p.get("target"))
        if t is None or not perception.conscious(t) or not _teacher_ok(state, defs, t, c, art):
            return None
        if art != growth.LITERACY and growth.art_level(c, art) >= 1:
            return None
        label = "识字" if art == growth.LITERACY else f"⟦{art}⟧"
        return _B(cost=cost, desc=f"跟⟦{t.id}⟧学{label}{ht}个时辰")
    if verb == "wait":
        n = p.get("ticks", 1)
        if not isinstance(n, int) or n < 1 or n > TICKS_PER_DAY:
            return None
        if n % TICKS_PER_HOUR == 0:
            return _B(cost=n, desc=f"等上{_hours_text(n // TICKS_PER_HOUR)}个时辰")
        return _B(cost=n, desc="静静等一会儿")
    if verb == "struggle":
        if not (has_status(c, "bound") or has_status(c, "captive")):
            return None
        return _B(cost=1, desc="拼命挣脱")
    return None


def build_speech_wait(state: WorldState, defs: Defs, actor: str) -> ActionInst | None:
    """只说话、不动时用来带那句话的动作（K12）：等 1 刻（ARCHITECTURE 2.3；紧张场面里耗时为 0）。

    ``resolve.apply(state, defs, build_speech_wait(...), speech, source=...)`` 只记那句话，不另记「等」。
    """
    return build(state, defs, actor, "wait", {"ticks": 1})


def build(state: WorldState, defs: Defs, actor: str, verb: str, params: dict) -> ActionInst | None:
    """按此刻的状态构造一个动作实例；不合法返回 None。不看底线（底线在 legal_actions 与 is_legal 里过滤）。

    ``verb == 'say'``：「说」本身不是身体动作，返回带话用的 ``wait`` 1 刻（同 ``build_speech_wait``，
    ``params`` 不看）；话的内容另由 ``Speech`` 给出。
    """
    c = state.chars.get(actor)
    if not can_act(c) or c is None:
        return None
    if verb == "say":
        return build_speech_wait(state, defs, actor)
    if has_status(c, "bound") and verb not in ("wait", "struggle"):
        return None
    if verb == "setpiece":
        return _setpiece(state, defs, c, params)
    b = _v(state, defs, c, verb, dict(params))
    if b is None:
        return None
    cost = 0 if tense_for(state, c) else b.cost
    return ActionInst(
        actor=actor,
        verb=verb,
        params=dict(params),
        cost=cost,
        loud=b.loud or VERBS[verb].loud,
        lethal=b.lethal,
        desc=b.desc,
        signature=signature(verb, params),
    )


def _setpiece(state: WorldState, defs: Defs, c: Character, params: dict) -> ActionInst | None:
    sid, name = params.get("situation"), params.get("name")
    s = state.situations.get(sid or "")
    if s is None or s.loc != c.loc:
        return None
    d = defs.situations.get(s.kind)
    if d is None or name not in d.actions:
        return None
    a = d.actions[name]
    if s.roles.get(a.role) != c.id:
        return None
    sctx = Ctx(self_id=c.id, roles=dict(s.roles), situation=s.id)
    if not predicates.holds(state, defs, a.pre, sctx):
        return None
    text = a.desc
    if not text:
        for eff in a.effects:
            if isinstance(eff, dict) and "emit" in eff:
                text = str(eff["emit"].get("text", ""))
                break
    desc = refs.fill_roles(text or name, s.roles, c.id)
    return ActionInst(
        actor=c.id,
        verb="setpiece",
        params={"situation": s.id, "name": name},
        cost=0 if tense_for(state, c) else a.cost,
        loud=a.loud,
        lethal=a.lethal,
        star=a.lean,
        tags=["situation"],
        desc=desc,
        signature=signature("setpiece", {"situation": s.id, "name": name}),
    )


def blocked_by_bottom(state: WorldState, defs: Defs, action: ActionInst) -> bool:
    """人物底线（5.4 的 blocks）：命中的动作从此人的清单里去掉。

    匹配规则见 ``kernel/bottom.py``（与请求裁决的 never 共用）：block 写出来的每个键都要对得上，
    例如 ``{verb: attack, act: kill}`` 只挡下杀手，不挡寻常动手。``say`` 的底线在 ``speech`` 里查。
    """
    if action.verb == "say":
        return False
    c = state.chars[action.actor]
    return bottom.blocked(state, defs, c, action.verb, action.params)


def is_legal(state: WorldState, defs: Defs, action: ActionInst) -> ActionInst | None:
    """6.3 第 1 步：重新校验。合法则返回按此刻状态重建的实例（保留 star 与 tags）。"""
    fresh = build(state, defs, action.actor, action.verb, action.params)
    if fresh is None or blocked_by_bottom(state, defs, fresh):
        return None
    fresh.id = action.id
    fresh.star = action.star
    fresh.tags = list(action.tags) or fresh.tags
    return fresh


# ---------------------------------------------------------------- enumeration


def _candidates(state: WorldState, defs: Defs, c: Character) -> list[tuple[str, dict]]:  # noqa: C901
    out: list[tuple[str, dict]] = []
    loc = state.locations[c.loc]
    people = [o for o in perception.present(state, c.loc, exclude=c.id) if perception.visible_to(c, o)]
    bodies = [
        o
        for o in state.chars.values()
        if o.loc == c.loc and o.onstage and o.id != c.id and not perception.on_map(o)
    ]
    seen = perception.seen_entities(state, c)
    for t in WAIT_TICKS[:1] if tense_for(state, c) else WAIT_TICKS:
        out.append(("wait", {"ticks": t}))
    out.append(("look", {}))
    out.append(("listen", {}))
    for e in usable_exits(state, defs, c):
        out.append(("go", {"exit": e.to}))
        if not e.edge:
            out.append(("flee", {"exit": e.to}))
    for fid, feat in loc.features.items():
        out.append(("examine", {"feature": fid}))
        out.append(("search", {"feature": fid}))
        if feat.cover > 0:
            out.append(("hide", {"feature": fid}))
        if feat.push:
            out.append(("push", {"feature": fid}))
    out.append(("search", {}))
    out.append(("hide", {}))
    for it in state.items.values():
        if it.in_world and it.holder is None and it.loc == c.loc and it.feature is None:
            out.append(("take", {"item": it.id}))
            out.append(("examine", {"item": it.id}))
    for iid in c.inventory:
        it = state.items[iid]
        out.append(("examine", {"item": iid}))
        out.append(("use", {"item": iid}))
        out.append(("drop", {"item": iid}))
        for art in it.teaches:
            for h in STUDY_HOURS:
                out.append(("study", {"item": iid, "art": art, "hours": h}))
    for art, lvl in c.arts.items():
        if lvl >= 1:
            for h in STUDY_HOURS:
                out.append(("practice", {"art": art, "hours": h}))
    for t in people:
        out.append(("examine", {"target": t.id}))
        out.append(("follow", {"target": t.id}))
        out.append(("help", {"target": t.id}))
        out.append(("release", {"target": t.id}))
        out.append(("block", {"target": t.id}))
        out.append(("push", {"target": t.id}))
        out.append(("restrain", {"target": t.id}))
        out.append(("attack", {"target": t.id, "intent": "injure"}))
        out.append(("attack", {"target": t.id, "intent": "kill"}))
        for iid in c.inventory:
            out.append(("give", {"item": iid, "target": t.id}))
            out.append(("show", {"item": iid, "target": t.id}))
            out.append(("use", {"item": iid, "target": t.id}))
        for iid in t.inventory:
            if perception.item_visible(state, c, iid, seen=seen):
                out.append(("steal", {"item": iid, "target": t.id}))
        arts = set(t.arts) | {growth.LITERACY}
        for art in sorted(arts):
            for h in STUDY_HOURS:
                out.append(("study", {"target": t.id, "art": art, "hours": h}))
        my_arts = set(c.arts) | {growth.LITERACY}
        for art in sorted(my_arts):
            for h in STUDY_HOURS:
                out.append(("teach", {"target": t.id, "art": art, "hours": h}))
    for b in bodies:
        out.append(("examine", {"target": b.id}))
    for h in REST_HOURS:
        out.append(("rest", {"hours": h}))
    out.append(("struggle", {}))
    return out


def _star_targets(state: WorldState, defs: Defs, c: Character) -> tuple[Step | None, str | None]:
    """当前这一步：若人不在 at，返回最短路上第一个出口通往的地点。"""
    st = danger.current_step(c)
    if st is None or not step_ready(state, defs, c, st):
        return None, None
    if st.at and st.at != c.loc:
        route = path(state, defs, c.id, c.loc, st.at)
        return st, (route[0].to if route else None)
    return st, None


def legal_actions(state: WorldState, defs: Defs, actor: str, *, for_model: bool = False) -> list[ActionInst]:
    c = state.chars.get(actor)
    if not can_act(c) or c is None:
        return []
    seen: set[str] = set()
    acts: list[ActionInst] = []
    for verb, params in _candidates(state, defs, c):
        a = build(state, defs, actor, verb, params)
        if a is None or a.signature in seen or blocked_by_bottom(state, defs, a):
            continue
        seen.add(a.signature)
        acts.append(a)
    if not has_status(c, "bound"):
        for s in state.situations.values():
            if s.loc != c.loc or actor not in s.roles.values():
                continue
            d = defs.situations.get(s.kind)
            if d is None:
                continue
            for name in d.actions:
                a = _setpiece(state, defs, c, {"situation": s.id, "name": name})
                if a is not None and a.signature not in seen and not blocked_by_bottom(state, defs, a):
                    seen.add(a.signature)
                    acts.append(a)
    # ★：当前这一步的 do；人不在 at 就标最短路上的那一步；情境里的 lean
    st, hop = _star_targets(state, defs, c)
    ctx = Ctx(self_id=actor)
    tailing = st is not None and following_step(state, c, st)
    for a in acts:
        if st is not None:
            if tailing:
                # 已经跟上了：这一步接下来就是跟着（等），不是「不再跟着」
                if a.verb == "wait":
                    a.star = True
                    a.tags.append("agenda")
            elif hop is not None:
                if a.verb == "go" and a.params.get("exit") == hop:
                    a.star = True
                    a.tags.append("agenda")
            elif (st.at is None or st.at == c.loc) and matches_do(a, st.do, ctx, state=state):
                a.star = True
                a.tags.append("agenda")
    # 介入：对眼前情境里的人做得出的动作（自己不在那场情境里）
    involved = {
        cid
        for s in state.situations.values()
        if s.loc == c.loc and actor not in s.roles.values()
        for cid in s.roles.values()
    }
    if involved:
        for a in acts:
            if a.params.get("target") in involved and a.verb not in ("examine", "follow"):
                a.tags.append("intervene")
    for r in state.requests.values():
        if r.frm == actor and r.status == "countered" and r.condition:
            cond = {"verb": r.condition.get("verb")}
            cond.update(r.condition.get("params") or {})
            for a in acts:
                if matches_do(a, cond, ctx, state=state):
                    a.tags.append("reply")
    stars = [a for a in acts if a.star]
    first_wait = next((a for a in acts if a.verb == "wait" and not a.star), None)
    ordered = stars + ([first_wait] if first_wait is not None else [])
    ordered += [a for a in acts if not a.star and a is not first_wait]
    if for_model:
        ordered = _compress(state, defs, c, ordered, st)
    for i, a in enumerate(ordered, 1):
        a.id = f"a{i:02d}"
    return ordered


def _compress(
    state: WorldState, defs: Defs, c: Character, acts: list[ActionInst], st: Step | None
) -> list[ActionInst]:
    """压到 12 条以内：★、wait、对眼前情境中人的动作、与当前打算相关的动作优先；其余按动词去重。"""
    keep: list[ActionInst] = []
    keep_ids: set[int] = set()

    def add(a: ActionInst) -> None:
        if id(a) not in keep_ids and len(keep) < MODEL_CAP:
            keep.append(a)
            keep_ids.add(id(a))

    wait = next((a for a in acts if a.verb == "wait"), None)
    for a in acts:
        if a.star:
            add(a)
    if wait is not None:
        if len(keep) >= MODEL_CAP:
            keep[-1] = wait
            keep_ids = {id(x) for x in keep}
        else:
            add(wait)
    involved = {cid for s in state.situations.values() if s.loc == c.loc for cid in s.roles.values()}
    agenda_target = None
    if st is not None and isinstance(st.do.get("target"), str):
        agenda_target = predicates.resolve_id(st.do["target"], state, Ctx(self_id=c.id))
    for a in acts:
        if "situation" in a.tags or "reply" in a.tags:
            add(a)
    for a in acts:
        if "intervene" in a.tags:
            add(a)
    for a in acts:
        t = a.params.get("target")
        if t is not None and (t in involved or t == agenda_target):
            add(a)
    verbs_seen = {a.verb for a in keep}
    for a in acts:
        if a.verb not in verbs_seen:
            verbs_seen.add(a.verb)
            add(a)
    order = {id(a): i for i, a in enumerate(acts)}
    stars = [a for a in keep if a.star]
    others = sorted((a for a in keep if not a.star), key=lambda a: (a.verb != "wait", order[id(a)]))
    return stars + others
