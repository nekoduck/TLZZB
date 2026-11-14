"""P7 校验（方案 9.1，不含基线跑）：``validate(defs, state) -> list[str]``。

- 引用完整：地点、物件、事实、本事、人、情境都对得上。
- 谓词与效果的语法：5.2、5.3 的每个键都按参数形状检查（``PREDICATE_SHAPES`` / ``EFFECT_SHAPES``）。
- 地图连通：每个不是图外的地点都能从玩家的开局地点走到（隐藏出口、要判定的出口也算）。
- 开局时不变量成立。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from tianlong.kernel import invariants, mood, predicates, refs
from tianlong.kernel.actions import VERBS
from tianlong.kernel.defs import Defs
from tianlong.kernel.effects import CHECK_KINDS, EFFECT_SHAPES
from tianlong.kernel.predicates import (
    OPS,
    PREDICATE_SHAPES,
    REL_FIELDS,
    REQ_STATUSES,
    STAT_FIELDS,
    STEP_STATES,
)
from tianlong.kernel.state import WorldState

SPECIALS = ("self", "player")
STATUS_OPTS = {"until", "ticks", "level", "source"}
STEP_DO_KEYS = {
    "verb",
    "target",
    "target2",
    "to",
    "item",
    "exit",
    "feature",
    "art",
    "act",
    "fact",
    "intent",
    "name",
    "situation",
    "gist",
    "ticks",
    "hours",
}


class _Checker:
    def __init__(self, defs: Defs, state: WorldState) -> None:
        self.defs = defs
        self.state = state
        self.problems: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.problems.append(f"{where}: {msg}")

    # ---------------------------------------------------------------- references

    def _special(self, v: Any, roles: set[str] | None) -> bool:
        if v in SPECIALS:
            return True
        if isinstance(v, str) and v.startswith("$"):
            if roles is not None and v[1:] not in roles:
                return False
            return True
        return False

    def ref(self, kind: str, v: Any, where: str, roles: set[str] | None) -> None:
        s = self.state
        if kind in ("char", "char?"):
            if v is None and kind == "char?":
                return
            if not isinstance(v, str) or not (self._special(v, roles) or v in s.chars):
                self.err(where, f"unknown character {v!r}")
        elif kind in ("loc", "loc?"):
            if v is None and kind == "loc?":
                return
            if not isinstance(v, str) or not (self._special(v, roles) or v in s.locations or v in s.chars):
                self.err(where, f"unknown location {v!r}")
        elif kind == "charloc":
            if not isinstance(v, str) or not (self._special(v, roles) or v in s.locations or v in s.chars):
                self.err(where, f"unknown character or location {v!r}")
        elif kind == "item":
            if not isinstance(v, str) or not (self._special(v, roles) or v in s.items):
                self.err(where, f"unknown item {v!r}")
        elif kind == "fact":
            if not isinstance(v, str) or not (self._special(v, roles) or v in s.facts):
                self.err(where, f"unknown fact {v!r}")
        elif kind == "art":
            if not isinstance(v, str) or not (v in self.defs.arts or v == "literacy"):
                self.err(where, f"unknown art {v!r}")
        elif kind in ("int", "int?"):
            if v is None and kind == "int?":
                return
            if not isinstance(v, int) or isinstance(v, bool):
                self.err(where, f"expected an integer, got {v!r}")
        elif kind in ("num", "num?"):
            if v is None and kind == "num?":
                return
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                self.err(where, f"expected a number, got {v!r}")
        elif kind in ("str", "str?", "flagname"):
            if v is None and kind == "str?":
                return
            if not isinstance(v, str):
                self.err(where, f"expected a string, got {v!r}")
        elif kind == "relfield":
            if v not in REL_FIELDS:
                self.err(where, f"bad relation field {v!r}")
        elif kind == "statfield":
            if v not in STAT_FIELDS:
                self.err(where, f"bad stat field {v!r}")
        elif kind == "op":
            if v not in OPS:
                self.err(where, f"bad operator {v!r}")
        elif kind == "stepstate":
            if v not in STEP_STATES:
                self.err(where, f"bad step state {v!r}")
        elif kind == "reqstatus":
            if v not in REQ_STATUSES:
                self.err(where, f"bad request status {v!r}")
        elif kind == "addremove":
            if v not in ("add", "remove", True, False):
                self.err(where, f"role: expected add/remove, got {v!r}")
        elif kind == "moodkind":
            if v not in mood.MOODS:
                self.err(where, f"bad mood {v!r}")
        elif kind == "marklist":
            if v not in ("visited", "examined"):
                self.err(where, f"bad mark list {v!r}")
        elif kind == "any":
            return
        elif kind == "opts?":
            if v is None:
                return
            if not isinstance(v, dict) or set(v) - STATUS_OPTS:
                self.err(where, f"bad status options {v!r}")
        elif kind == "roles?":
            if v is None:
                return
            if not isinstance(v, dict):
                self.err(where, f"roles must be a mapping, got {v!r}")
                return
            for rv in v.values():
                self.ref("char", rv, where, roles)
        elif kind == "situation":
            if v not in self.defs.situations:
                self.err(where, f"unknown situation {v!r}")
        elif kind == "pred?":
            if v is not None:
                self.pred(v, where, roles)
        elif kind == "effects":
            self.effects(v, where, roles)
        elif kind == "stepdef":
            self.step(v, where, roles)
        else:
            self.err(where, f"(validator) unknown arg kind {kind}")

    # ---------------------------------------------------------------- predicates

    def pred(self, p: Any, where: str, roles: set[str] | None = None) -> None:
        if p is None or p is True or p is False:
            return
        if isinstance(p, str):
            if p.lower() not in ("true", "false"):
                self.err(where, f"bad predicate {p!r}")
            return
        if isinstance(p, list):
            for i, q in enumerate(p):
                self.pred(q, f"{where}[{i}]", roles)
            return
        if not isinstance(p, dict) or len(p) != 1:
            self.err(where, f"bad predicate {p!r}")
            return
        key, arg = next(iter(p.items()))
        shape = PREDICATE_SHAPES.get(key)
        w = f"{where}.{key}"
        if shape is None:
            self.err(where, f"unknown predicate {key!r}")
        elif shape == "preds":
            if not isinstance(arg, list):
                self.err(w, "expects a list")
            else:
                for i, q in enumerate(arg):
                    self.pred(q, f"{w}[{i}]", roles)
        elif shape == "pred":
            self.pred(arg, w, roles)
        elif shape == "flag":
            if isinstance(arg, list):
                if len(arg) != 2 or not isinstance(arg[0], str):
                    self.err(w, f"bad flag {arg!r}")
            elif not isinstance(arg, str):
                self.err(w, f"bad flag {arg!r}")
        elif isinstance(shape, list):
            if not isinstance(arg, list) or len(arg) != len(shape):
                self.err(w, f"expects a list of {len(shape)}, got {arg!r}")
            else:
                for i, (kind, v) in enumerate(zip(shape, arg, strict=True)):
                    self.ref(kind, v, f"{w}[{i}]", roles)
        else:
            self.ref(shape, arg, w, roles)

    # ---------------------------------------------------------------- effects

    def effects(self, effs: Any, where: str, roles: set[str] | None = None) -> None:
        if effs is None:
            return
        if not isinstance(effs, list):
            self.err(where, f"effects must be a list, got {effs!r}")
            return
        for i, e in enumerate(effs):
            self.effect(e, f"{where}[{i}]", roles)

    def effect(self, e: Any, where: str, roles: set[str] | None) -> None:
        if not isinstance(e, dict) or len(e) != 1:
            self.err(where, f"bad effect {e!r}")
            return
        key, arg = next(iter(e.items()))
        shape = EFFECT_SHAPES.get(key)
        w = f"{where}.{key}"
        if shape is None:
            self.err(where, f"unknown effect {key!r}")
            return
        if isinstance(shape, list):
            if key == "open" and isinstance(arg, str):
                arg = [arg]
            need = sum(1 for k in shape if not k.endswith("?"))
            if not isinstance(arg, list) or not need <= len(arg) <= len(shape):
                self.err(w, f"expects a list of {need} to {len(shape)}, got {arg!r}")
                return
            for i, v in enumerate(arg):
                self.ref(shape[i], v, f"{w}[{i}]", roles)
            return
        if shape == "emit":
            if not isinstance(arg, dict) or not isinstance(arg.get("text"), str):
                self.err(w, "emit needs {text}")
                return
            if arg.get("actor") is not None:
                self.ref("char", arg["actor"], f"{w}.actor", roles)
            for name in refs.ROLE_RE.findall(arg["text"]):
                if roles is not None and name not in roles and name != "self":
                    self.err(w, f"text uses unknown role {{{name}}}")
            for eid in refs.refs_in(arg["text"]):
                self._entity(eid, w)
        elif shape == "check":
            if not isinstance(arg, dict) or arg.get("kind") not in CHECK_KINDS:
                self.err(w, f"check needs a kind in {CHECK_KINDS}")
                return
            if "who" in arg:
                self.ref("char", arg["who"], f"{w}.who", roles)
            sub = set(roles or set()) | {"checked"} if roles is not None else None
            self.effects(arg.get("ok"), f"{w}.ok", sub)
            self.effects(arg.get("fail"), f"{w}.fail", sub)
        elif shape == "kill":
            if isinstance(arg, list):
                if not arg or len(arg) > 2:
                    self.err(w, f"bad kill {arg!r}")
                else:
                    self.ref("char", arg[0], w, roles)
            else:
                self.ref("char", arg, w, roles)
        elif shape == "thread":
            if not isinstance(arg, dict) or "kind" not in arg or "text" not in arg:
                self.err(w, "thread needs {kind, text}")
            elif arg["kind"] not in ("deadline", "promise", "lead", "event", "wish"):
                self.err(w, f"bad thread kind {arg['kind']!r}")
        elif shape == "scene":
            if arg not in ("calm", "tense"):
                self.err(w, f"bad scene {arg!r}")
        elif shape == "endreason":
            if arg not in ("closed", "dead", "left"):
                self.err(w, f"bad end reason {arg!r}")
        else:
            self.ref(shape, arg, w, roles)

    def step(self, sd: Any, where: str, roles: set[str] | None = None) -> None:
        if not isinstance(sd, dict):
            self.err(where, f"step must be a mapping, got {sd!r}")
            return
        do = sd.get("do")
        if not isinstance(do, dict) or do.get("verb") not in VERBS:
            self.err(where, f"step.do needs a verb from the verb table, got {do!r}")
        else:
            extra = set(do) - STEP_DO_KEYS
            if extra:
                self.err(where, f"step.do has unknown keys {sorted(extra)}")
            for k in ("target", "target2"):
                if k in do:
                    self.ref("char", do[k], f"{where}.do.{k}", roles)
            if "item" in do:
                self.ref("item", do["item"], f"{where}.do.item", roles)
            if "fact" in do:
                self.ref("fact", do["fact"], f"{where}.do.fact", roles)
            if "art" in do:
                self.ref("art", do["art"], f"{where}.do.art", roles)
            if "to" in do:
                self.ref("charloc", do["to"], f"{where}.do.to", roles)
        if sd.get("at") is not None and sd["at"] not in self.state.locations:
            self.err(where, f"unknown location {sd['at']!r}")
        self.pred(sd.get("pre"), f"{where}.pre", roles)
        self.effects(sd.get("offscreen"), f"{where}.offscreen", roles)

    def _entity(self, eid: str, where: str) -> None:
        if refs.lookup(self.state, self.defs, eid) is None and eid not in self.state.facts:
            self.err(where, f"text refers to unknown entity ⟦{eid}⟧")


def _connectivity(c: _Checker) -> None:
    s = c.state
    for lid, loc in s.locations.items():
        for e in loc.exits:
            if not e.edge and e.to not in s.locations:
                c.err(f"locations.{lid}", f"exit to unknown location {e.to!r}")
            if e.check is not None and e.check not in ("climb", "descend", "leap"):
                c.err(f"locations.{lid}", f"bad exit check {e.check!r}")
            if e.fall_to is not None and e.fall_to not in s.locations:
                c.err(f"locations.{lid}", f"fall_to unknown location {e.fall_to!r}")
            c.pred(e.pre, f"locations.{lid}.exits[{e.to}].pre")
        for t in loc.loud_to:
            if t not in s.locations:
                c.err(f"locations.{lid}.loud_to", f"unknown location {t!r}")
        for fid, f in loc.features.items():
            for x in f.reveals:
                c.ref("fact", x, f"locations.{lid}.features.{fid}.reveals", None)
            for x in f.contains:
                c.ref("item", x, f"locations.{lid}.features.{fid}.contains", None)
            if not 0 <= f.cover <= 30:
                c.err(f"locations.{lid}.features.{fid}", f"cover {f.cover} out of 0..30")
            c.pred(f.pre, f"locations.{lid}.features.{fid}.pre")
            c.effects(f.push, f"locations.{lid}.features.{fid}.push")
        if not -20 <= loc.terrain <= 20:
            c.err(f"locations.{lid}", f"terrain {loc.terrain} out of -20..20")
    p = s.chars.get(s.player)
    if p is None or p.loc not in s.locations:
        c.err("opening.player", "player has no valid start location")
        return
    seen = {p.loc}
    q = deque([p.loc])
    while q:
        here = q.popleft()
        for e in s.locations[here].exits:
            if e.to in s.locations and e.to not in seen:
                seen.add(e.to)
                q.append(e.to)
    for lid in s.locations:
        if lid not in seen:
            c.err(f"locations.{lid}", "not reachable from the start location")


def _raw_tables(c: _Checker) -> None:
    """goals / routines / opportunities 的格式由各自的模块定；这里只查认得出的谓词与效果。"""
    d = c.defs
    if isinstance(d.routines, dict):
        for name, rules in d.routines.items():
            for i, r in enumerate(rules or []):
                if isinstance(r, dict):
                    c.pred(r.get("when"), f"routines.{name}[{i}].when")
    if isinstance(d.opportunities, dict):
        for oid, o in d.opportunities.items():
            if isinstance(o, dict):
                c.pred(o.get("pre"), f"opportunities.{oid}.pre")
                c.effects(o.get("effects"), f"opportunities.{oid}.effects", None)


def validate(defs: Defs, state: WorldState) -> list[str]:
    c = _Checker(defs, state)
    s = state
    _connectivity(c)
    for iid, it in s.items.items():
        w = f"items.{iid}"
        if it.holder is not None and it.holder not in s.chars:
            c.err(w, f"holder {it.holder!r} unknown")
        if it.loc is not None and it.loc not in s.locations:
            c.err(w, f"location {it.loc!r} unknown")
        if (
            it.loc is not None
            and it.feature is not None
            and it.feature not in s.locations.get(it.loc, None).features
        ):  # type: ignore[union-attr]
            c.err(w, f"feature {it.feature!r} unknown")
        for a in it.teaches:
            c.ref("art", a, f"{w}.teaches", None)
        c.effects(it.props.get("on_use"), f"{w}.props.on_use", None)
        c.effects(it.props.get("on_obtain"), f"{w}.props.on_obtain", None)
        for x in it.props.get("reveals", []) or []:
            c.ref("fact", x, f"{w}.props.reveals", None)
        inf = it.props.get("inflicts")
        if inf is not None and (not isinstance(inf, dict) or "status" not in inf):
            c.err(w, "props.inflicts needs {status, ticks}")
    for fid, f in s.facts.items():
        for eid in refs.refs_in(f.text):
            c._entity(eid, f"facts.{fid}")
        for x in f.contradicts:
            c.ref("fact", x, f"facts.{fid}.contradicts", None)
    for aid, a in defs.arts.items():
        w = f"arts.{aid}"
        for src in a.sources:
            if src != "teacher" and src not in s.items:
                c.err(w, f"source {src!r} is neither an item nor teacher")
        c.pred(a.requires, f"{w}.requires")
        if not a.stages:
            c.err(w, "has no stages")
        for i, st in enumerate(a.stages):
            if st.hours <= 0:
                c.err(f"{w}.stages[{i}]", "hours must be positive")
    for cid, ch in s.chars.items():
        w = f"chars.{cid}"
        if ch.onstage and ch.loc not in s.locations:
            c.err(w, f"starts at unknown location {ch.loc!r}")
        if ch.superior is not None and ch.superior not in s.chars:
            c.err(w, f"superior {ch.superior!r} unknown")
        for oid in ch.rel:
            if oid not in s.chars:
                c.err(f"{w}.rel", f"unknown character {oid!r}")
        for a in ch.arts:
            c.ref("art", a, f"{w}.arts", None)
        for fid in ch.beliefs:
            c.ref("fact", fid, f"{w}.knows", None)
        if ch.agenda is not None:
            ids: set[str] = set()
            c.pred(ch.agenda.done_when, f"agendas.{cid}.done_when")
            for st in ch.agenda.steps:
                if st.id in ids:
                    c.err(f"agendas.{cid}", f"duplicate step id {st.id!r}")
                ids.add(st.id)
                c.step(st.model_dump(), f"agendas.{cid}.{st.id}")
        if (
            ch.tier == "extra"
            and ch.routine is not None
            and isinstance(defs.routines, dict)
            and ch.routine not in defs.routines
        ):
            c.err(w, f"routine {ch.routine!r} not in routines.yaml")
    for cid, card in defs.cards.items():
        if cid not in s.chars:
            c.err(f"characters/{cid}", "card not placed in the world")
        for i, bl in enumerate(card.bottom_lines):
            for j, b in enumerate(bl.blocks):
                if b.verb not in VERBS:
                    c.err(f"characters/{cid}.bottom_lines[{i}].blocks[{j}]", f"unknown verb {b.verb!r}")
                c.pred(
                    b.when, f"characters/{cid}.bottom_lines[{i}].blocks[{j}].when", {"target", "requester"}
                )
    for sid, d in defs.situations.items():
        w = f"situations/{sid}"
        roles = set(d.roles)
        c.pred(d.when, f"{w}.when", set())
        c.pred(d.exit_when, f"{w}.exit_when", roles)
        for name, spec in d.roles.items():
            if isinstance(spec, str):
                c.ref("char", spec, f"{w}.roles.{name}", set())
            elif isinstance(spec, dict) and isinstance(spec.get("pick"), dict):
                c.pred(spec["pick"].get("where"), f"{w}.roles.{name}.pick.where", roles | {"it"})
            else:
                c.err(f"{w}.roles.{name}", f"bad role spec {spec!r}")
        if d.at is not None:
            if d.at.startswith("$"):
                if d.at[1:] not in roles:
                    c.err(f"{w}.at", f"unknown role {d.at!r}")
            elif d.at not in s.locations:
                c.err(f"{w}.at", f"unknown location {d.at!r}")
        for name, a in d.actions.items():
            if a.role not in roles:
                c.err(f"{w}.actions.{name}", f"unknown role {a.role!r}")
            c.pred(a.pre, f"{w}.actions.{name}.pre", roles)
            c.effects(a.effects, f"{w}.actions.{name}.effects", roles)
        for i, r in enumerate(d.offscreen):
            c.pred(r.when, f"{w}.offscreen[{i}].when", roles)
            c.effects(r.effects, f"{w}.offscreen[{i}].effects", roles)
    for i, sch in enumerate(s.scheduled):
        c.effects(sch.get("effects"), f"opening.scheduled[{i}].effects", None)
        c.pred(sch.get("pre"), f"opening.scheduled[{i}].pre", None)
    for t in s.known_terms:
        if refs.lookup(s, defs, t) is None:
            c.err("opening.known_terms", f"unknown entity {t!r}")
    for t in s.term_alias:
        if refs.lookup(s, defs, t) is None:
            c.err("opening.term_alias", f"unknown entity {t!r}")
    for i, m in enumerate(defs.baseline):
        c.pred(m.holds, f"baseline[{i}].holds")
    _raw_tables(c)
    for p in invariants.check(s, defs):
        c.err("invariant", p)
    # 谓词在开局状态下能求值（不抛异常）
    for sid, d in defs.situations.items():
        try:
            predicates.holds(s, defs, d.when)
        except predicates.KernelError as e:
            c.err(f"situations/{sid}.when", str(e))
    return c.problems
