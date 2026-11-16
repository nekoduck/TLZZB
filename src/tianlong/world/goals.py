"""候选目标（方案 8.3 的 1 至 3 步）：读 ``content/goals.yaml``，由「所求 × 处境」生出候选，配步骤、打分。

这里只生成候选和把选中的候选落成步骤，不调用模型，也不改世界。格式见 goals.yaml 开头的说明。

- **列目标。** 每条规则有 ``drive``（所求的种类）与 ``when``（处境谓词）。所求带参数的
  （``protect:<人>``、``obey:<人>``、``grudge:<人>``）把参数绑成 ``$target``；``protect:friends``
  展开成此人好感不低于 30 的每个人。
- **配步骤。** ``bind`` 按当前状态绑定变量（找得到才算数）；每个目标有一两个步骤模板，模板里的
  ``$变量`` 换成具体的 id，``at`` 写人时取他此刻所在的地点。模板的 ``pre`` 与第一步的 ``pre`` 此刻
  不成立、或第一步去不了的，不进候选。
- **打分。** 规则分 = 所求的权重 × 紧迫程度 - 风险（对手比自己强几级 × 10）- 路程（时辰数 × 2）。
  「照旧做原来的打算」（只在由事件触发、当前这一步前提仍成立时给出）另有固定的分。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

from tianlong.kernel import actions, danger, growth, perception, predicates, refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_HOUR, Character, Ctx, WorldState, is_alive
from tianlong.world.common import LOG, loc_of
from tianlong.world.offscreen import effective_at

MAX_CANDIDATES = 12
KEEP_ID = "keep"
GENERIC_GOALS = ("idle", "wait")  # 照旧过日子；等一等再说（8.3「任何 × 无事」）
FRIEND = 30
DEFAULT_SCORING = {"distance_per_hour": 2, "risk_per_level": 10, "keep": 100, "within": 192}
VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class Candidate:
    id: str  # 'keep' 或 'g1'…（给 J3 的选项 id）
    rule: str  # goals.yaml 里的规则 id；照旧为 'keep'
    drive: str
    goal: str  # 一句目标（带 ⟦id⟧）
    template: str
    steps: list[dict]  # 已经填好参数的步骤（还没有分配 id）
    score: float
    group: str  # 同一目标、同一批绑定的候选共一个 group（给 C5 看「一个目标带几条步骤模板」）
    bindings: dict[str, str] = field(default_factory=dict)
    keep: bool = False

    @property
    def first_text(self) -> str:
        return self.steps[0]["text"] if self.steps else ""

    def option_text(self) -> str:
        return f"{self.goal}：{self.first_text}" if self.first_text else self.goal


# ---------------------------------------------------------------- the rule table


def table(defs: Defs) -> dict[str, Any]:
    g = defs.goals if isinstance(defs.goals, dict) else {}
    return g


def scoring(defs: Defs) -> dict[str, Any]:
    s = dict(DEFAULT_SCORING)
    s.update(table(defs).get("scoring") or {})
    return s


def weights(defs: Defs) -> dict[str, float]:
    return {str(k): float(v) for k, v in (table(defs).get("weights") or {}).items()}


def rules(defs: Defs) -> list[dict]:
    return [r for r in table(defs).get("goals") or [] if isinstance(r, dict) and r.get("id")]


def drive_text(state: WorldState, defs: Defs, drive: str) -> str:
    """所求在提示词里的说法（goals.yaml 的 labels）。"""
    kind, param = drive_kind(drive)
    label = str((table(defs).get("labels") or {}).get(kind) or drive)
    if param is None:
        return label.replace("{who}", "")
    if param == "friends":
        who = "朋友"
    elif param == "player":
        who = refs.ref(state.player)
    else:
        who = refs.ref(param)
    return label.replace("{who}", who)


def drive_kind(drive: str) -> tuple[str, str | None]:
    kind, _, param = drive.partition(":")
    return kind, (param or None)


def drive_targets(state: WorldState, c: Character, drive: str) -> list[str | None]:
    """所求的参数绑成谁。protect:friends 展开成好感不低于 30 的每个人；没有参数的为 [None]。"""
    kind, param = drive_kind(drive)
    if param is None:
        return [None]
    if param == "friends":
        out = []
        for oid, r in sorted(c.rel.items()):
            o = state.chars.get(oid)
            if r.affinity >= FRIEND and o is not None and oid != c.id and is_alive(o):
                out.append(oid)
        return out
    if param == "player":
        return [state.player]
    return [param] if param in state.chars else []


# ---------------------------------------------------------------- binders


def _dist(state: WorldState, defs: Defs, c: Character, loc: str | None) -> int:
    """从此人所在之处到 loc 的路程（刻）；到不了为很大的数。"""
    if loc is None:
        return 1 << 20
    if loc == c.loc:
        return 0
    route = actions.path(state, defs, c.id, c.loc, loc)
    if not route:
        return 1 << 20
    return sum(actions.travel_cost(state, c, e) for e in route)


def _knows_where(state: WorldState, c: Character, item_id: str) -> bool:
    """此人知不知道这样东西在哪：自己拿着、就在眼前，或相信一条提到它的事实；
    解药另算：一条事实同时提到它治的那种毒和拿着它的人（或它所在的地方），也算知道。"""
    it = state.items.get(item_id)
    if it is None or not it.in_world:
        return False
    if it.holder == c.id:
        return True
    where = loc_of(state, item_id)
    if where == c.loc:
        return True
    cures = set(it.props.get("cures") or [])
    related = {item_id} | {
        pid
        for pid, p in state.items.items()
        if isinstance(p.props.get("inflicts"), dict) and p.props["inflicts"].get("status") in cures
    }
    places = {x for x in (it.holder, it.loc, where) if x}
    for fid, b in c.beliefs.items():
        if b.confidence < predicates.BELIEVE:
            continue
        f = state.facts.get(fid)
        if f is None:
            continue
        rs = set(refs.refs_in(f.text))
        if item_id in rs or (rs & related and rs & places):
            return True
    return False


def _status_source(state: WorldState, who: str | None, kinds: tuple[str, ...], prefix: str = "") -> str | None:
    c = state.chars.get(who or "")
    if c is None:
        return None
    for s in c.statuses:
        if (s.kind in kinds or (prefix and s.kind.startswith(prefix))) and s.source in state.chars:
            src = state.chars[s.source]
            if perception.on_map(src) and src.id != c.id:
                return src.id
    return None


def _heading(state: WorldState, o: Character) -> str | None:
    """此人眼下这一步要去的别处（打算里写了 at，或这一步就是走、逃）；就地办事、没有打算的为 None。"""
    st = danger.current_step(o)
    if st is None:
        return None
    if st.at and st.at != o.loc and st.at in state.locations:
        return st.at
    if st.do.get("verb") in ("go", "flee"):
        v = st.do.get("exit") or st.do.get("to")
        if isinstance(v, str) and v in state.locations and v != o.loc:
            return v
    return None


def _tie(cid: str, lid: str) -> int:
    """同样近的几处里各人各有偏好（确定性的：只看人和地点的 id，不掷骰）。"""
    return int.from_bytes(hashlib.blake2b(f"{cid}|{lid}".encode(), digest_size=4).digest(), "big")


def _safe_place(
    state: WorldState, defs: Defs, c: Character, avoid: str | None = None, *, _yield: bool = True
) -> str | None:
    """最近的、眼下没有人对他怀敌意的别处；同属一方的地方（Location.owner 是他的身份）优先。

    躲的是人，所以也避开对头（对他怀敌意的人、``avoid`` 指的那人）正要去的地方。两个互相敌视的人同在
    一处、各自要躲时，会挑中同一处最近的地方，到了又互相躲回来，来回几日（P6）：让 id 小的先挑，另一个
    避开他会挑的那一处（实在没有别处可去才同路）；同样近的几处里各人的取舍也不同。
    """
    foes = [
        o
        for o in state.chars.values()
        if o.id != c.id and perception.on_map(o) and is_alive(o) and (o.id == avoid or danger.is_hostile(state, o, c.id))
    ]
    taken = {h for o in foes if (h := _heading(state, o)) is not None}
    presumed: set[str] = set()
    if _yield:
        for o in foes:
            if o.loc == c.loc and o.id < c.id and danger.is_hostile(state, c, o.id):
                alt = _safe_place(state, defs, o, _yield=False)
                if alt is not None:
                    presumed.add(alt)
    best: tuple[int, int, int, int, str] | None = None
    for lid, L in state.locations.items():
        if lid == c.loc or lid in taken:
            continue
        people = perception.present(state, lid, exclude=c.id)
        if any(danger.is_hostile(state, o, c.id) for o in people):
            continue
        if avoid is not None and any(o.id == avoid for o in people):
            continue
        d = _dist(state, defs, c, lid)
        if d >= 1 << 20:
            continue
        key = (1 if lid in presumed else 0, 0 if L.owner and L.owner in c.roles else 1, d, _tie(c.id, lid), lid)
        if best is None or key < best:
            best = key
    return best[4] if best else None


def _pick(state: WorldState, defs: Defs, c: Character, where: Any, roles: dict[str, str]) -> str | None:
    """图内的活人里挑第一个使 where 成立的（按路程由近到远，再按 id）；不挑自己。"""
    cands = [o for o in state.chars.values() if o.id != c.id and perception.on_map(o)]
    cands.sort(key=lambda o: (_dist(state, defs, c, o.loc), o.id))
    for o in cands:
        ctx = Ctx(self_id=c.id, roles={**roles, "it": o.id})
        if predicates.holds(state, defs, where, ctx):
            return o.id
    return None


def _pick_item(state: WorldState, defs: Defs, c: Character, where: Any, roles: dict[str, str]) -> str | None:
    for iid in sorted(state.items):
        it = state.items[iid]
        if not it.in_world:
            continue
        ctx = Ctx(self_id=c.id, roles={**roles, "it": iid})
        if predicates.holds(state, defs, where, ctx):
            return iid
    return None


def _cure_for(state: WorldState, c: Character, who: str | None) -> str | None:
    """治得了 who 身上的毒（或别的可解的状态）的东西，而且此人知道它在哪。"""
    p = state.chars.get(who or "")
    if p is None:
        return None
    kinds = {s.kind for s in p.statuses}
    for iid in sorted(state.items):
        it = state.items[iid]
        cures = set(it.props.get("cures") or [])
        if it.in_world and cures & kinds and _knows_where(state, c, iid):
            return iid
    return None


def _rumored_item(state: WorldState, c: Character) -> str | None:
    """听说过的宝物：他相信的事实里提到的要紧东西（秘籍、宝物、信物、活宝），不在他手里。"""
    keep = {"manual", "treasure", "creature"}
    for fid in sorted(c.beliefs):
        b = c.beliefs[fid]
        f = state.facts.get(fid)
        if f is None or b.confidence < predicates.BELIEVE:
            continue
        for iid in refs.refs_in(f.text):
            it = state.items.get(iid)
            if it is None or not it.in_world or it.holder == c.id:
                continue
            if set(it.tags) & keep or it.props.get("important"):
                return iid
    return None


def _teaches_of(state: WorldState, defs: Defs, c: Character, item_id: str | None) -> str | None:
    it = state.items.get(item_id or "")
    if it is None:
        return None
    for art in it.teaches:
        if art in defs.arts and growth.art_level(c, art) < 1:
            return art
    return None


def _art_of(state: WorldState, defs: Defs, c: Character, teacher: str | None) -> str | None:
    t = state.chars.get(teacher or "")
    if t is None:
        return None
    for art, lvl in sorted(t.arts.items()):
        if lvl >= 1 and art in defs.arts and growth.art_level(c, art) < 1 and growth.can_teach(state, defs, t, c, art):
            return art
    return None


def _practice_art(defs: Defs, c: Character) -> str | None:
    for art, lvl in sorted(c.arts.items()):
        if lvl >= 1 and growth.stage_need(defs, art, lvl) is not None:
            return art
    return None


def _foe_at(state: WorldState, who: str | None) -> str | None:
    """仇人眼下要去的地方（他当前这一步的 at），不知道就取他此刻所在之处。"""
    f = state.chars.get(who or "")
    if f is None or not perception.on_map(f):
        return None
    st = danger.current_step(f)
    if st is not None and st.at and st.at in state.locations:
        return st.at
    return f.loc


def bind_one(state: WorldState, defs: Defs, c: Character, spec: Any, roles: dict[str, str]) -> str | None:
    """一个绑定方法 → 一个 id，绑不上为 None。参数里的 self / $变量 先换掉。"""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"bad binder {spec!r}")
    how, arg = next(iter(spec.items()))
    ctx = Ctx(self_id=c.id, roles=roles)
    a = predicates.resolve_id(arg, state, ctx) if isinstance(arg, str) else arg
    if how == "captor_of":
        return _status_source(state, a, ("captive", "bound"))
    if how == "poisoner_of":
        return _status_source(state, a, (), prefix="poisoned")
    if how == "cure_for":
        return _cure_for(state, c, a)
    if how == "holder_of":
        it = state.items.get(a or "")
        return it.holder if it is not None and it.in_world and it.holder else None
    if how == "place_of":
        return loc_of(state, a) if isinstance(a, str) and a in state.items else None
    if how == "loc_of":
        return loc_of(state, a)
    if how == "superior_of":
        o = state.chars.get(a or "")
        sup = state.chars.get(o.superior or "") if o is not None else None
        return sup.id if sup is not None and perception.on_map(sup) else None
    if how == "safe_place":
        return _safe_place(state, defs, c)
    if how == "away_from":
        return _safe_place(state, defs, c, avoid=a)
    if how == "hostile_here":
        hs = danger.hostiles_toward(state, c.id)
        return sorted(hs, key=lambda o: o.id)[0].id if hs else None
    if how == "rumored_item":
        return _rumored_item(state, c)
    if how == "teaches_of":
        return _teaches_of(state, defs, c, a)
    if how == "art_of":
        return _art_of(state, defs, c, a)
    if how == "practice_art":
        return _practice_art(defs, c)
    if how == "foe_at":
        return _foe_at(state, a)
    if how == "pick":
        return _pick(state, defs, c, (arg or {}).get("where"), roles)
    if how == "pick_item":
        return _pick_item(state, defs, c, (arg or {}).get("where"), roles)
    raise ValueError(f"unknown binder {how!r}")


# ---------------------------------------------------------------- filling templates


class Unbound(Exception):
    pass


def fill(value: Any, b: dict[str, str]) -> Any:
    """把模板里的 $变量 换成 id。整串就是一个变量的直接换；夹在文字里的（如 ⟦$target⟧）就地替换。"""
    if isinstance(value, str):
        m = VAR_RE.fullmatch(value)
        if m:
            if m.group(1) not in b:
                raise Unbound(m.group(1))
            return b[m.group(1)]

        def sub(mm: re.Match[str]) -> str:
            if mm.group(1) not in b:
                raise Unbound(mm.group(1))
            return b[mm.group(1)]

        return VAR_RE.sub(sub, value)
    if isinstance(value, list):
        return [fill(v, b) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, b) for k, v in value.items()}
    return value


STEP_FIELDS = ("text", "at", "do", "pre", "takes", "offscreen")


def make_steps(state: WorldState, template: dict, b: dict[str, str]) -> list[dict]:
    """填好参数的步骤。at 写人的，取他此刻所在之处。"""
    out = []
    for raw in template.get("steps") or []:
        sd = {k: fill(raw[k], b) for k in STEP_FIELDS if k in raw}
        at = sd.get("at")
        if at is not None:
            loc = loc_of(state, at)
            if loc is None:
                raise Unbound(f"at {at}")
            do = sd.get("do") or {}
            if at in state.chars and at in (do.get("target"), do.get("to")):
                # 冲着一个人去做的事：不钉死地点，他走到哪里就跟到哪里（离场结算按 do 里的人找过去）
                sd.pop("at")
                sd["near"] = at
            else:
                sd["at"] = loc
        sd.setdefault("text", str(sd.get("do", {}).get("verb", "")))
        sd.setdefault("takes", 4)
        sd["tmpl"] = raw.get("id") or ""
        out.append(sd)
    return out


REQUEST_ACTS = ("request", "plead", "order")
REFUSED_MEMORY = 2 * 96  # 被回绝过的同一请求，两日之内不再当作候选（免得每个时辰去磨同一个人）


def refused_before(state: WorldState, c: Character, do: dict) -> bool:
    """同一个人、同一件事，此人近来求过而被回绝了。"""
    if do.get("verb") != "say" or do.get("act") not in REQUEST_ACTS:
        return False
    ctx = Ctx(self_id=c.id)
    to = predicates.resolve_id(do.get("to"), state, ctx)
    ask = do.get("ask") or {}
    params = {k: predicates.resolve_id(v, state, ctx) for k, v in (ask.get("params") or {}).items()}
    for r in state.requests.values():
        if r.frm != c.id or r.to != to or r.status not in ("refused", "countered", "expired"):
            continue
        if r.action.get("verb") == ask.get("verb", "wait") and (r.action.get("params") or {}) == params:
            if state.tick - r.created <= REFUSED_MEMORY:
                return True
    return False


def feasible(state: WorldState, defs: Defs, c: Character, steps: list[dict]) -> bool:
    if not steps:
        return False
    first = steps[0]
    verb = first.get("do", {}).get("verb")
    if verb not in actions.VERBS and verb != "say":
        return False
    if refused_before(state, c, first.get("do", {})):
        return False
    if not predicates.holds(state, defs, first.get("pre"), Ctx(self_id=c.id)):
        return False
    at = first.get("at") or loc_of(state, first.get("near"))
    if first.get("near") and at is None:
        return False
    if at and at != c.loc and not actions.path(state, defs, c.id, c.loc, at):
        return False
    return True


# ---------------------------------------------------------------- candidates


def step_started(state: WorldState, c: Character, st: Any) -> bool:
    """这一步是不是已经在做了：人已到了做这件事的地方，且在上面花了时间。"""
    ag = c.agenda
    return ag is not None and ag.progress > 0 and effective_at(state, c, st) == c.loc


def keep_allowed(state: WorldState, defs: Defs, c: Character) -> bool:
    """「照旧做原来的打算」能不能进候选：当前这一步还在、没过期，而且前提此刻仍成立，
    或者这一步还没开始做（前提写的是动手那一刻的条件，例如「被扣在营里，听候发落」）。"""
    st = danger.current_step(c)
    if st is None:
        return False
    if st.deadline is not None and state.tick > st.deadline:
        return False
    try:
        if predicates.holds(state, defs, st.pre, Ctx(self_id=c.id)):
            return True
    except predicates.KernelError:
        return False
    return not step_started(state, c, st)


def risk_of(state: WorldState, defs: Defs, c: Character, who: str | None, per_level: float) -> float:
    o = state.chars.get(who or "")
    if o is None:
        return 0.0
    gap = growth.power(state, defs, o) - growth.power(state, defs, c)
    return per_level * max(0, gap)


def candidates(state: WorldState, defs: Defs, cid: str, *, allow_keep: bool = False) -> list[Candidate]:
    """此人此刻的候选（至多 12 个，按规则分从高到低；含照旧过日子与等一等再说）。"""
    c = state.chars[cid]
    sc = scoring(defs)
    w = weights(defs)
    out: list[Candidate] = []
    if allow_keep and keep_allowed(state, defs, c):
        st = danger.current_step(c)
        assert st is not None and c.agenda is not None
        out.append(
            Candidate(
                id=KEEP_ID,
                rule=KEEP_ID,
                drive="any",
                goal=c.agenda.goal,
                template=KEEP_ID,
                steps=[{"text": st.text}],
                score=float(sc["keep"]),
                group=KEEP_ID,
                keep=True,
            )
        )
    drives = list(dict.fromkeys(list(c.drives) + ["any"]))
    for rule in rules(defs):
        rd = rule.get("drive", "any")
        kinds = rd if isinstance(rd, list) else [rd]
        for drive in drives:
            kind, _ = drive_kind(drive)
            if kind not in kinds:
                continue
            for target in drive_targets(state, c, drive):
                out += _expand(state, defs, c, rule, drive, target, w, sc)
    # 同一规则、同一批绑定、同一模板只留一个（两个所求可能生出同一个候选）
    seen: set[tuple] = set()
    uniq: list[Candidate] = []
    for cd in sorted(out, key=lambda x: (-x.score, x.rule, x.template, sorted(x.bindings.items()))):
        key = (cd.rule, cd.template, tuple(sorted(cd.bindings.items())))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(cd)
    generic = [cd for cd in uniq if cd.rule in GENERIC_GOALS]
    others = [cd for cd in uniq if cd.rule not in GENERIC_GOALS]
    chosen = others[: MAX_CANDIDATES - len(generic)] + generic
    chosen.sort(key=lambda x: -x.score)
    n = 0
    groups: dict[str, str] = {}
    for cd in chosen:
        if cd.keep:
            continue
        n += 1
        cd.id = f"g{n}"
        groups.setdefault(cd.group, f"G{len(groups) + 1}")
        cd.group = groups[cd.group]
    return chosen


def _expand(
    state: WorldState,
    defs: Defs,
    c: Character,
    rule: dict,
    drive: str,
    target: str | None,
    w: dict[str, float],
    sc: dict[str, Any],
) -> list[Candidate]:
    b: dict[str, str] = {}
    if target is not None:
        b["target"] = target
    rid = str(rule["id"])
    try:
        if not predicates.holds(state, defs, rule.get("when"), Ctx(self_id=c.id, roles=dict(b))):
            return []
        for name, spec in (rule.get("bind") or {}).items():
            v = bind_one(state, defs, c, spec, dict(b))
            if v is None:
                return []
            b[str(name)] = v
    except (predicates.KernelError, ValueError) as e:
        LOG.warning("goals.yaml %s: %s", rid, e)
        return []
    kind, _ = drive_kind(drive)
    weight = w.get(kind, w.get("any", 5.0))
    urgency = float(rule.get("urgency", 1))
    risk_var = rule.get("risk")
    risk = 0.0
    if isinstance(risk_var, str):
        try:
            risk = risk_of(state, defs, c, fill(risk_var, b), float(sc["risk_per_level"]))
        except Unbound:
            risk = 0.0
    group = f"{rid}|" + ",".join(f"{k}={v}" for k, v in sorted(b.items()))
    out: list[Candidate] = []
    for i, tmpl in enumerate(rule.get("templates") or []):
        tid = str(tmpl.get("id") or f"t{i + 1}")
        try:
            if not predicates.holds(state, defs, fill(tmpl.get("pre"), b), Ctx(self_id=c.id, roles=dict(b))):
                continue
            steps = make_steps(state, tmpl, b)
            goal = fill(str(rule.get("text") or rid), b)
        except Unbound:
            continue
        except predicates.KernelError as e:
            LOG.warning("goals.yaml %s/%s: %s", rid, tid, e)
            continue
        try:
            if not feasible(state, defs, c, steps):
                continue
        except predicates.KernelError as e:
            LOG.warning("goals.yaml %s/%s: %s", rid, tid, e)
            continue
        first_at = steps[0].get("at") or loc_of(state, steps[0].get("near"))
        hours = math.ceil(_dist(state, defs, c, first_at) / TICKS_PER_HOUR) if first_at else 0
        score = weight * urgency - risk - float(sc["distance_per_hour"]) * hours
        out.append(
            Candidate(
                id="",
                rule=rid,
                drive=drive,
                goal=goal,
                template=tid,
                steps=steps,
                score=score,
                group=group,
                bindings=dict(b),
            )
        )
    return out


# ---------------------------------------------------------------- instantiation


def instantiate(state: WorldState, defs: Defs, cid: str, cand: Candidate, steps: list[dict] | None = None) -> dict:
    """把选中的候选落成一份新打算（dict，交给效果 agenda）。步骤的 origin 记 replan。"""
    within = int(scoring(defs).get("within", 192))
    chosen = steps if steps is not None else cand.steps
    out_steps = []
    for k, sd in enumerate(chosen, 1):
        st = {key: sd[key] for key in STEP_FIELDS if key in sd and sd[key] is not None}
        st["id"] = f"{cid}@{state.tick}.{k}"
        st["origin"] = "replan"
        st["deadline"] = state.tick + within
        out_steps.append(st)
    return {"goal": cand.goal, "steps": out_steps, "cursor": 0, "progress": 0, "status": "active"}


def waiting_agenda(goal: str, waiting_for: str) -> dict:
    return {"goal": goal, "steps": [], "cursor": 0, "progress": 0, "status": "waiting", "waiting_for": waiting_for}


def groups(cands: list[Candidate]) -> dict[str, list[Candidate]]:
    """C5 看到的「目标」：同一 group 的候选是同一个目标的几条步骤模板。"""
    out: dict[str, list[Candidate]] = {}
    for cd in cands:
        if cd.keep:
            continue
        out.setdefault(cd.group, []).append(cd)
    return out


def step_ids(group_id: str, cands: list[Candidate]) -> dict[str, dict]:
    """步骤模板 id（'G1.t1.1'……）→ 步骤。"""
    out: dict[str, dict] = {}
    for cd in cands:
        for k, sd in enumerate(cd.steps, 1):
            out[f"{group_id}.{cd.template}.{k}"] = sd
    return out

