"""J1 与 C1 的候选（方案 7.3）：动词、场景里的东西、言语行为、说话的对象、
对方做得到的事、可能相关的事实、玩家近来做过的事。

候选给模型看时用不透明的编号（p1、i2、k3……），回来后再对回实体 id。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from tianlong.kernel import actions, danger, growth, perception, refs, speech
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, WorldState, has_status

MAX_ASKS = 8
MAX_CLAIMS = 20
MAX_SELF = 8
CLASS_ORDER = ("trivial", "effort", "costly", "risky", "betray")
NEED_STATUSES = ("injured", "bound", "captive", "hungry", "exhausted")


class Opt(BaseModel):
    key: str
    ref: str  # 实体 id、动词名、言语行为名；情境动作为 '<情境 id>:<动作名>'
    desc: str  # 已渲染好的中文
    kind: str = ""  # person / item / exit / feature / art / verb / act / setpiece


class AskCand(BaseModel):
    """一件对方做得到的事：请求的动作写成 kernel Request 的格式 {verb, params, desc}。"""

    key: str = ""
    to: str
    action: dict
    desc: str  # 带 ⟦id⟧ 的中性描述
    cls: str = "effort"  # 规则给的类别（与 kernel 默认、J1 的 cls 取最重的一个）
    keys: list[str] = []  # 规则猜测用的关键字
    refs: list[str] = []
    about: str | None = None  # 「告诉我某事」一类：问的是什么
    # 请求的种类与要点（评测与上层用）：release / take_place_of / accompany / give_item / show_item / cure / help /
    # teach_art / come_along / stop_doing / let_pass / tell_about / take_to / protect
    type: str = "other"
    slots: dict[str, str] = {}


class ClaimCand(BaseModel):
    key: str = ""
    ref: str  # 事实 id，或 'event:<id>'
    text: str  # 带 ⟦id⟧ 的事实句
    known: bool = False  # 玩家自己信不信（只作参考，不进模型）


class SelfCand(BaseModel):
    key: str = ""
    ref: str  # 'event:<id>'
    text: str
    verb: str = ""
    refs: list[str] = []


class Menu(BaseModel):
    verbs: list[Opt] = []
    objects: list[Opt] = []
    acts: list[Opt] = []
    people: list[Opt] = []
    asks: list[AskCand] = []
    claims: list[ClaimCand] = []
    selfs: list[SelfCand] = []
    addressee: str | None = None  # 候选请求是按谁列的；None 表示在场的人都有

    def obj(self, key_or_id: str | None) -> Opt | None:
        if not key_or_id:
            return None
        for o in self.objects:
            if o.key == key_or_id or o.ref == key_or_id:
                return o
        return None

    def verb(self, key_or_id: str | None) -> Opt | None:
        if not key_or_id:
            return None
        for o in self.verbs:
            if o.key == key_or_id or o.ref == key_or_id:
                return o
        return None

    def person(self, key_or_id: str | None) -> Opt | None:
        if not key_or_id:
            return None
        for o in self.people:
            if o.key == key_or_id or o.ref == key_or_id:
                return o
        return None

    def ask(self, key: str | None) -> AskCand | None:
        if not key:
            return None
        for a in self.asks:
            if a.key == key:
                return a
        return None

    def claim(self, key_or_ref: str | None) -> ClaimCand | None:
        if not key_or_ref:
            return None
        for c in self.claims:
            if c.key == key_or_ref or c.ref == key_or_ref:
                return c
        return None

    def self_event(self, key_or_ref: str | None) -> SelfCand | None:
        if not key_or_ref:
            return None
        for s in self.selfs:
            if s.key == key_or_ref or s.ref == key_or_ref:
                return s
        return None


# ---------------------------------------------------------------- rendering


def pov(state: WorldState, defs: Defs, text: str) -> str:
    """玩家视角的渲染：自己写成「你」，不知其名的用叫法。"""
    return refs.render(text, state, defs, state.player)


def omni(state: WorldState, defs: Defs, text: str) -> str:
    """全知的渲染（给 Jev 对断言用）：玩家写成「你」，其余用真名。"""
    t = (text or "").replace(refs.ref(state.player), "你")
    return refs.render(t, state, defs, None)


def name_for_model(state: WorldState, defs: Defs, eid: str) -> str:
    """给模型看的叫法：玩家视角的叫法；不知其名的另附真名，方便对上玩家从书里知道的名字。"""
    shown = refs.display_name(state, defs, eid, state.player)
    found = refs.lookup(state, defs, eid)
    if found is not None and found[0] and found[0] != shown and eid != state.player:
        return f"{shown}（{found[0]}）"
    return shown


def model_text(state: WorldState, defs: Defs, text: str) -> str:
    """给模型看的事实句、候选句：每个 ⟦id⟧ 换成 name_for_model，玩家写成「你」。"""
    def sub(m: re.Match[str]) -> str:
        eid = m.group(1)
        if eid == state.player:
            return "你"
        return name_for_model(state, defs, eid)

    return refs.REF_RE.sub(sub, text or "")


def status_note(c: Character) -> str:
    notes = []
    if has_status(c, "bound"):
        notes.append("被捆着")
    elif has_status(c, "captive"):
        notes.append("被看押着")
    if not perception.conscious(c):
        notes.append("不省人事")
    lvl = next((s.level for s in c.statuses if s.kind == "injured"), 0)
    if lvl:
        notes.append("受了伤")
    if any(s.kind.startswith("poisoned") for s in c.statuses):
        notes.append("中了毒")
    return "，".join(notes)


# ---------------------------------------------------------------- objects


def scene_people(state: WorldState, me: Character) -> list[Character]:
    return [o for o in perception.present(state, me.loc, exclude=me.id) if perception.visible_to(me, o)]


def visible_items(state: WorldState, me: Character) -> list[str]:
    """看得见的物：地上的、自己身上的、在场的人手里的。"""
    people = {o.id for o in scene_people(state, me)}
    out: list[str] = []
    for it in state.items.values():
        if not it.in_world:
            continue
        if it.holder == me.id or (it.holder is None and it.loc == me.loc and it.feature is None):
            out.append(it.id)
        elif it.holder in people:
            out.append(it.id)
    return out


def scene_objects(state: WorldState, defs: Defs, legal: list[actions.ActionInst]) -> list[Opt]:
    me = state.chars[state.player]
    out: list[Opt] = []
    for i, o in enumerate(scene_people(state, me), 1):
        note = status_note(o)
        desc = name_for_model(state, defs, o.id) + (f"（{note}）" if note else "")
        out.append(Opt(key=f"p{i}", ref=o.id, desc=desc, kind="person"))
    for i, iid in enumerate(visible_items(state, me), 1):
        it = state.items[iid]
        where = "你身上" if it.holder == me.id else ("地上" if it.holder is None else "")
        if it.holder not in (None, me.id):
            where = refs.display_name(state, defs, it.holder or "", state.player) + "身上"
        out.append(Opt(key=f"i{i}", ref=iid, desc=f"{name_for_model(state, defs, iid)}（{where}）", kind="item"))
    exits = []
    for a in legal:
        if a.verb == "go" and a.params.get("exit") not in exits:
            exits.append(a.params["exit"])
    for i, to in enumerate(exits, 1):
        out.append(Opt(key=f"x{i}", ref=to, desc=f"往{name_for_model(state, defs, to)}去的路", kind="exit"))
    loc = state.locations[me.loc]
    for i, (fid, feat) in enumerate(loc.features.items(), 1):
        out.append(Opt(key=f"f{i}", ref=f"{me.loc}#{fid}", desc=feat.name, kind="feature"))
    arts: list[str] = []
    for a in legal:
        art = a.params.get("art")
        if art and art not in arts and a.verb in ("study", "practice", "teach"):
            arts.append(art)
    for i, art in enumerate(arts, 1):
        label = "识字" if art == growth.LITERACY else name_for_model(state, defs, art)
        out.append(Opt(key=f"a{i}", ref=art, desc=label, kind="art"))
    return out


def verb_options(
    state: WorldState, defs: Defs, legal: list[actions.ActionInst], words: dict[str, str]
) -> list[Opt]:
    out: list[Opt] = []
    seen: set[str] = set()
    n_sp = 0
    for a in legal:
        if a.verb == "setpiece":
            n_sp += 1
            ref = f"{a.params.get('situation')}:{a.params.get('name')}"
            tmpl = words.get("verb.setpiece", "{事}")
            out.append(Opt(key=f"sp{n_sp}", ref=ref, desc=tmpl.replace("{事}", pov(state, defs, a.desc)),
                           kind="setpiece"))
            continue
        if a.verb in seen:
            continue
        seen.add(a.verb)
        desc = words.get(f"verb.{a.verb}") or actions.VERBS[a.verb].label
        out.append(Opt(key=a.verb, ref=a.verb, desc=desc, kind="verb"))
    return out


def act_options(state: WorldState, defs: Defs, words: dict[str, str]) -> list[Opt]:
    me = state.chars[state.player]
    acts = speech.available_acts(state, defs, me)
    return [Opt(key=a, ref=a, desc=words.get(f"act.{a}") or speech.ACT_PHRASE.get(a, a), kind="act") for a in acts]


def people_options(state: WorldState, defs: Defs) -> list[Opt]:
    me = state.chars[state.player]
    hearers = {o.id for o in speech.audience(state, me)}
    return [
        Opt(key=f"p{i}", ref=o.id, desc=name_for_model(state, defs, o.id), kind="person")
        for i, o in enumerate(scene_people(state, me), 1)
        if o.id in hearers
    ]


# ---------------------------------------------------------------- requests


def _cls_max(*names: str | None) -> str:
    best = 0
    for n in names:
        if n in CLASS_ORDER:
            best = max(best, CLASS_ORDER.index(n))
    return CLASS_ORDER[best]


def has_need(state: WorldState, c: Character) -> bool:
    """对方眼下有没有急需：中毒、受伤、被缚被押、饥疲，或身边有人冲他来。"""
    if any(s.kind.startswith("poisoned") or s.kind in NEED_STATUSES for s in c.statuses):
        return True
    if c.loc and danger.hostiles_toward(state, c.id):
        return True
    return c.mood.get("fear", 0) >= 50


def _held(c: Character) -> bool:
    return has_status(c, "bound") or has_status(c, "captive")


def _holds(state: WorldState, t: Character, h: Character) -> str | None:
    """t 管不管得着 h 的去留。返回放人这件事对 t 算哪一类；管不着返回 None。

    下手的人（或他的上司）放人是「有损己利」；他的手下背着他放人是「违背所属」；没写是谁下的手，谁都可以谈。
    """
    if not _held(h):
        return None
    srcs = {s.source for s in h.statuses if s.kind in ("bound", "captive") and s.source}
    if not srcs:
        return "costly"
    for src in srcs:
        captor = state.chars.get(src or "")
        if t.id == src or (captor is not None and captor.superior == t.id):
            return "costly"
        if captor is not None and t.superior == captor.id:
            return "betray"
    return None


def _ask(kind: str, to: str, verb: str, params: dict, desc: str, cls: str, keys: list[str], rs: list[str],
         *, slots: dict[str, str] | None = None, about: str | None = None,
         extra: dict[str, Any] | None = None) -> AskCand:
    action: dict[str, Any] = {"verb": verb, "params": dict(params), "desc": desc}
    if extra:
        action.update(extra)
    return AskCand(to=to, action=action, desc=desc, cls=cls, keys=keys, refs=rs, about=about, type=kind,
                   slots=dict(slots or {}))


def _guards(state: WorldState, t: Character, loc: str) -> bool:
    """t 守着这块地方：地点属于 t 的某个身份。"""
    owner = state.locations[loc].owner if loc in state.locations else None
    return bool(owner) and owner in t.roles


def _all_request_candidates(state: WorldState, defs: Defs, frm: str, to: str) -> list[AskCand]:  # noqa: C901
    me = state.chars.get(frm)
    t = state.chars.get(to)
    if me is None or t is None or t.id == me.id:
        return []
    P = refs.ref(frm)
    out: list[AskCand] = []
    later: list[AskCand] = []
    here = [o for o in perception.present(state, t.loc, exclude=t.id) if o.id != frm]
    # 放人：放了我；放了某个被缚、被押的人；让我顶替他
    if _held(me):
        out.append(_ask("release", to, "release", {"target": frm}, f"放了{P}", "costly",
                        ["放了我", "放我", "松绑", "放开我", "饶", "走"], [frm], slots={"target": frm}))
    for h in here:
        cls = _holds(state, t, h)
        if cls is not None:
            H = refs.ref(h.id)
            out.append(_ask("release", to, "release", {"target": h.id}, f"放了{H}", cls,
                            ["放了", "放开", "松绑", "放人", "饶"], [h.id], slots={"target": h.id}))
            out.append(_ask("take_place_of", to, "release", {"target": h.id}, f"放了{H}，由{P}顶替{H}", cls,
                            ["换", "替", "顶替", "代替", "抵"], [h.id], slots={"target": h.id},
                            extra={"swap": frm}))
    # 让我跟某人同行：对方不拦着（「放行」写成对方等一等，带 permit 说明）。
    # 被他扣着的人、跟他同在一场情境里的人在前；他的手下排到最后
    together = {cid for s in state.situations.values() if to in s.roles.values() for cid in s.roles.values()}
    for h in here:
        if h.id == to:
            continue
        held_cls = _holds(state, t, h)
        if held_cls is not None or h.superior == to or h.id in together:
            H = refs.ref(h.id)
            cand = _ask("accompany", to, "wait", {"ticks": 1}, f"让{P}跟{H}一道走", held_cls or "effort",
                        ["跟", "一道", "一起", "同去", "同行", "陪", "随", "一块"], [h.id],
                        slots={"target": h.id}, extra={"permit": {"verb": "follow", "target": h.id}})
            (out if held_cls is not None or h.id in together else later).append(cand)
    # 给我某样东西；给我看看某样东西
    def worth(iid: str) -> int:
        it = state.items[iid]
        imp = 10 if set(it.tags) & {"token", "manual", "antidote", "poison", "treasure", "key"} else 0
        return int(it.props.get("value", 0) or 0) + imp

    for iid in sorted(t.inventory, key=lambda i: -worth(i))[:4]:
        it = state.items.get(iid)
        if it is None or not it.in_world:
            continue
        ref_i = refs.ref(iid)
        out.append(_ask("give_item", to, "give", {"item": iid, "target": frm}, f"把{ref_i}给{P}",
                        speech.classify_request(state, defs, to, frm, {"verb": "give", "params": {"item": iid}}),
                        ["给我", "借我", "交出", "拿来", "给", "借", "让给"], [iid], slots={"item": iid}))
        later.append(_ask("show_item", to, "show", {"item": iid, "target": frm}, f"把{ref_i}拿给{P}看看", "trivial",
                          ["看看", "瞧瞧", "瞧一瞧", "看一看", "开开眼", "见识"], [iid], slots={"item": iid}))
    # 给某个中毒、受伤的人用药、照料
    for h in [me, *here]:
        poisoned = [s.kind for s in h.statuses if s.kind.startswith("poisoned")]
        for iid in t.inventory:
            it = state.items.get(iid)
            if it is None:
                continue
            cures = it.props.get("cures") or []
            if poisoned and any(p in cures for p in poisoned):
                out.append(_ask("cure", to, "use", {"item": iid, "target": h.id},
                                f"给{refs.ref(h.id)}服{refs.ref(iid)}", "costly",
                                ["解药", "解毒", "救", "服", "给"], [h.id, iid] if h.id != frm else [iid],
                                slots={"target": h.id, "item": iid}))
        if h.id != to and (poisoned or any(s.kind == "injured" for s in h.statuses)):
            out.append(_ask("help", to, "help", {"target": h.id}, f"照料{refs.ref(h.id)}", "effort",
                            ["救", "照料", "包扎", "医", "治", "扶"], [h.id] if h.id != frm else [],
                            slots={"target": h.id}))
    # 教我本事
    arts = sorted(set(t.arts) | {growth.LITERACY})
    for art in arts:
        if growth.can_teach(state, defs, t, me, art):
            label = "识字" if art == growth.LITERACY else refs.ref(art)
            keys = ["教", "学", "拜师", "收我", "指点", "传"] + (["识字", "认字", "读书", "写字"]
                                                                if art == growth.LITERACY else [])
            out.append(_ask("teach_art", to, "teach", {"target": frm, "art": art}, f"教{P}{label}",
                            speech.classify_request(state, defs, frm, to,
                                                    {"verb": "teach", "params": {"target": frm, "art": art}}),
                            keys, [] if art == growth.LITERACY else [art], slots={"art": art}))
    # 跟我走
    out.append(_ask("come_along", to, "follow", {"target": frm}, f"跟{P}走", "effort",
                    ["跟我", "随我", "跟着我", "陪我", "一起走", "同我", "一道走", "一块走", "咱们", "我们"], []))
    # 住手：对方此刻正冲着某人来，或在一场情境里
    st = danger.current_step(t)
    hostile_to = [o.id for o in [me, *here] if danger.is_hostile(state, t, o.id)]
    if hostile_to or (st is not None and st.do.get("verb") in ("attack", "restrain", "use", "setpiece")):
        victim = hostile_to[0] if hostile_to else None
        desc = f"住手，别再为难{refs.ref(victim)}" if victim else "停下手上的事"
        out.append(_ask("stop_doing", to, "wait", {"ticks": 4}, desc, "costly" if victim else "effort",
                        ["住手", "停手", "别", "不要", "罢手", "放过", "饶"],
                        [victim] if victim and victim != frm else [],
                        slots={"target": victim} if victim else {}, extra={"stop": True}))
    # 放我过去：对方守着这块地方，或此刻对我有敌意
    if not _held(me):
        for e in actions.usable_exits(state, defs, me)[:4]:
            if e.edge:
                continue
            guarding = _guards(state, t, e.to) or _guards(state, t, me.loc)
            if guarding or danger.is_hostile(state, t, frm):
                out.append(_ask("let_pass", to, "wait", {"ticks": 1}, f"放{P}往{refs.ref(e.to)}去",
                                "betray" if guarding else "effort",
                                ["放我过去", "让我过去", "让我们", "放行", "过去", "行个方便", "下山", "进去"],
                                [], slots={"place": e.to}, extra={"permit": {"verb": "go", "exit": e.to}}))
    # 告诉我一些事
    out.append(_ask("tell_about", to, "say", {"act": "tell"}, f"回答{P}的问题，告诉{P}一些事", "trivial",
                    ["告诉", "说说", "讲讲", "是谁", "什么", "哪里", "怎么"], []))
    # 带我去某处
    for e in actions.usable_exits(state, defs, t)[:3]:
        if e.edge:
            continue
        out.append(_ask("take_to", to, "go", {"exit": e.to}, f"带{P}去{refs.ref(e.to)}", "effort",
                        ["带我", "领我", "带路"], [e.to], slots={"place": e.to}))
    # 替我挡住某个对我有敌意的人
    for h in here:
        if h.id != to and danger.is_hostile(state, h, frm):
            out.append(_ask("protect", to, "block", {"target": h.id}, f"替{P}挡住{refs.ref(h.id)}", "risky",
                            ["挡", "拦", "护", "救我"], [h.id], slots={"target": h.id}))
    return out + later


def ask_score(state: WorldState, defs: Defs, c: AskCand, text: str, frm: str) -> int:
    """规则猜测：话里对得上这件事的程度。牵涉到别人或别的东西的，要提到它，关键字才算数。"""
    from tianlong.parser import lexical

    others = [r for r in c.refs if r and r != frm]

    def mentioned(r: str) -> bool:
        if lexical.match_score(state, defs, r, text) >= 2:
            return True
        o = state.chars.get(r)
        if o is not None and lexical.FEMALE_WORDS.search(text) or (o is not None and "她" in text):
            alias = refs.display_name(state, defs, r, state.player) + (o.name if o else "")
            return bool(lexical.FEMALE_MARK.search(alias))
        return False

    named = sum(1 for r in others if mentioned(r))
    kw = sum(1 for k in c.keys if k and k in text)
    if others and not named:
        return kw if c.type == "teach_art" and kw else 0  # 「教我两手」：没说是哪门本事也算
    return 2 * kw + 3 * named


def _doable(state: WorldState, defs: Defs, c: AskCand) -> bool:
    """对方此刻做得到这件事（传授要等答应了才合法，不在这里查；说话、等候总做得到）。"""
    verb = c.action.get("verb")
    if verb in ("teach", "say", "wait"):
        t = state.chars.get(c.to)
        return t is not None and actions.can_act(t)
    return actions.build(state, defs, c.to, str(verb), dict(c.action.get("params") or {})) is not None


def request_candidates(
    state: WorldState, defs: Defs, frm: str, to: str, *, text: str = "", limit: int = MAX_ASKS
) -> list[AskCand]:
    """对方（to）做得到、可以求他做的事，至多 limit 件；给了 text 就按它提到的先后排。"""
    cands = [c for c in _all_request_candidates(state, defs, frm, to) if _doable(state, defs, c)]
    if text:
        cands = [c for _, c in sorted(enumerate(cands), key=lambda ic: (-ask_score(state, defs, ic[1], text, frm),
                                                                         ic[0]))]
    out = cands[:limit]
    for i, c in enumerate(out, 1):
        c.key = f"k{i}"
    return out


def rule_class(state: WorldState, defs: Defs, cand: AskCand, frm: str) -> str:
    """规则给的类别：候选自带的类别与 kernel 的默认类别取重的一个。"""
    base = speech.classify_request(state, defs, frm, cand.to, {k: v for k, v in cand.action.items()
                                                               if k in ("verb", "params")})
    return _cls_max(base, cand.cls)


# ---------------------------------------------------------------- claims


def claim_candidates(
    state: WorldState,
    defs: Defs,
    frm: str,
    *,
    people: list[str] | None = None,
    topic: list[str] | None = None,
    limit: int = MAX_CLAIMS,
) -> list[ClaimCand]:
    """与在场者和话题相关的事实，至多 limit 条；包括玩家角色不知道的（那些会判成 unsourced）。

    另加玩家近来亲见的几件事（'event:<id>'），说「我亲眼看见……」时对得上。
    """
    me = state.chars[frm]
    here = set(people if people is not None else [o.id for o in scene_people(state, me)])
    topic_set = set(topic or [])
    scored: list[tuple[int, int, str]] = []
    for i, (fid, f) in enumerate(state.facts.items()):
        rs = set(refs.refs_in(f.text))
        s = 2 * len(rs & here) + 4 * len(rs & topic_set) + (1 if me.loc in rs else 0) + (1 if frm in rs else 0)
        b = me.beliefs.get(fid)
        if b is not None and b.confidence >= 0.6:
            s += 1
        if any((ob := state.chars[o].beliefs.get(fid)) is not None and ob.confidence >= 0.6
               for o in here if o in state.chars):
            s += 1
        if s > 0:
            scored.append((-s, i, fid))
    scored.sort()
    out: list[ClaimCand] = []
    for _, _, fid in scored[: max(0, limit - 3)]:
        b = me.beliefs.get(fid)
        out.append(ClaimCand(ref=fid, text=state.facts[fid].text, known=b is not None and b.confidence >= 0.6))
    # 亲见的几件事（不是玩家做的；没有成为事实的）
    seen = 0
    for ev in reversed(state.events_tail):
        if seen >= 3 or len(out) >= limit:
            break
        if ev.actor in (None, frm) or ev.witnesses.get(frm) != "saw" or ev.outcome == "blocked":
            continue
        if ev.verb in ("say", "wait", "look", "listen", "warn_danger"):
            continue
        fid = f"E{ev.id}"
        if fid in state.facts and any(c.ref == fid for c in out):
            continue
        out.append(ClaimCand(ref=fid if fid in state.facts else f"event:{ev.id}", text=ev.fact_text, known=True))
        seen += 1
    for i, c in enumerate(out[:limit], 1):
        c.key = f"c{i}"
    return out[:limit]


def self_event_candidates(state: WorldState, defs: Defs, frm: str | None = None, *, limit: int = MAX_SELF
                          ) -> list[SelfCand]:
    """玩家近来做过的事（最近的在前）。'self_unlisted' 由调用方另加。"""
    frm = frm or state.player
    out: list[SelfCand] = []
    for ev in reversed(state.events_tail):
        if len(out) >= limit:
            break
        if ev.actor != frm or ev.outcome == "blocked" or ev.verb in ("warn_danger", "wait"):
            continue
        rs = [v for k, v in ev.params.items() if k in ("target", "target2", "item", "exit") and isinstance(v, str)]
        out.append(SelfCand(ref=f"event:{ev.id}", text=ev.fact_text, verb=ev.verb, refs=rs))
    for i, s in enumerate(out, 1):
        s.key = f"s{i}"
    return out
