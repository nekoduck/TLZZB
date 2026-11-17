"""绑定（方案 4.2 第 1 步、2.4、6.6、6.7）：把归类结果对齐到一个合法的 ActionInst 与一句 Speech。

- 每次输入至多一个身体动作加一句话（2.4）。只说话的，动作取 1 刻的 ``wait``。
- 对不上的对象、此刻做不到的事：kind = impossible，回一句戏内的话，不耗时。
- 提到这个世界没有的东西（lexicon 的 banned_modern）：absurd，叙述写一句恍惚，不耗时。
- 玩家的断言只是言语行为：结论由 ``kernel.beliefs.claim_verdict`` 下，输入里的任何东西都改不了世界。
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Literal

from pydantic import BaseModel

from tianlong.kernel import actions, beliefs, perception, refs, speech
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Claim, Speech, WorldState, has_status, injury_level
from tianlong.parser import lexical as lx
from tianlong.parser import replies, rules
from tianlong.parser.candidates import CLASS_ORDER, AskCand, has_need, request_candidates, visible_items
from tianlong.parser.intake import ParseResult

Kind = Literal["action", "unclear", "impossible", "meta"]
DURATION_VERBS = ("wait", "rest", "study", "practice", "teach")
OPINION_TEXT = "一句没凭没据的话"


class Bound(BaseModel):
    kind: Kind
    action: ActionInst | None = None
    speech: Speech | None = None
    lethal: bool = False
    signature: str = ""
    reply: str = ""
    absurd: bool = False
    attempts: list[str] = []
    repeat: bool = False  # 没有新凭据、重复提同一个请求（内核会把 attempts 加一）
    via: str = "rule"
    errors: list[str] = []


# ---------------------------------------------------------------- helpers


def safe_text(state: WorldState, defs: Defs, text: str) -> str:
    """要旨里只留玩家已知的专名：不知其名的实体换成玩家眼里的叫法（不再是 ⟦id⟧），免得说一句话就「学会」名字。"""
    def sub(m: re.Match[str]) -> str:
        eid = m.group(1)
        if eid == state.player or "#" in eid or eid in state.known_terms:
            return m.group(0)
        return refs.display_name(state, defs, eid, state.player)

    return refs.REF_RE.sub(sub, text or "")


def safe_note(state: WorldState, defs: Defs, note: str, default: str) -> str:
    """C1 的戏内话不得带出玩家不知道的名字；带了就用模板。"""
    n = (note or "").strip().replace("\n", "")
    if not n or len(n) > 80:
        return default
    for eid, term in defs.lexicon.names.items():
        if eid in state.known_terms or eid == state.player:
            continue
        if term.name and term.name != (term.alias or "") and term.name in n:
            return default
    for eid, term in defs.lexicon.terms.items():
        if eid not in state.known_terms and term.name and term.name in n:
            return default
    return n


def _cls_max(*names: str | None) -> str:
    best = 0
    for n in names:
        if n in CLASS_ORDER:
            best = max(best, CLASS_ORDER.index(n))
    return CLASS_ORDER[best]


def _values(state: WorldState, a: ActionInst) -> set[str]:
    p = a.params
    out = {str(p[k]) for k in ("target", "target2", "item", "exit", "art") if p.get(k)}
    if p.get("feature"):
        out.add(f"{state.chars[a.actor].loc}#{p['feature']}")
    return out


def _absent_reply(state: WorldState, defs: Defs, eid: str, legal: list[ActionInst]) -> str | None:
    """对象此刻不在眼前时的回话；在眼前返回 None。"""
    me = state.chars[state.player]
    if any(a.params.get("exit") == eid for a in legal if a.verb in ("go", "flee")):
        return None  # 合法清单里的一条出路（通到图外的路不在地点表里）
    if eid in state.chars:
        o = state.chars[eid]
        if o.loc == me.loc and o.onstage and perception.visible_to(me, o):
            return None
        return replies.NO_SUCH_PERSON
    if eid in state.items:
        return None if eid in visible_items(state, me) else replies.NO_SUCH_THING
    if eid in state.locations:
        if eid == me.loc or any(a.params.get("exit") == eid for a in legal if a.verb in ("go", "flee")):
            return None
        return replies.NO_SUCH_WAY
    if "#" in eid:
        return None if eid.split("#", 1)[0] == me.loc else replies.NO_SUCH_THING
    if eid in defs.arts or eid == "literacy":
        return None
    return replies.NO_SUCH_THING


def first_hop(state: WorldState, defs: Defs, clean: str, gos: list[ActionInst]) -> ActionInst | None:
    """「去某个不相邻的地方」：取最短路上的第一步（只走玩家知道的路）。"""
    me = state.chars[state.player]
    others = [loc for loc in state.locations if loc != me.loc]
    for dest, _score in lx.match_entities(state, defs, clean, others, floor=3):
        route = actions.path(state, defs, me.id, me.loc, dest)
        if route:
            nxt = route[0].to
            for a in gos:
                if a.params.get("exit") == nxt:
                    return a
    return None


def mentioned_absent(state: WorldState, defs: Defs, clean: str, legal: list[ActionInst], *, floor: int = 6
                     ) -> str | None:
    """话里点名提到、此刻却不在眼前的人或东西：说明一句，不让玩家猜。"""
    narr, _ = lx.split_speech(clean)
    text = narr or clean
    me = state.chars[state.player]
    ids = [c for c in state.chars if c != me.id] + [i for i, it in state.items.items() if it.in_world]
    for eid, _score in lx.match_entities(state, defs, text, ids, floor=floor):
        why = _absent_reply(state, defs, eid, legal)
        if why is not None:
            return why
    return None


def cannot_reason(state: WorldState, defs: Defs, verb: str) -> str:
    me = state.chars[state.player]
    if has_status(me, "bound") and verb not in ("wait", "struggle"):
        return replies.BOUND
    if verb in ("go", "flee", "follow") and injury_level(me) >= 3:
        # 侥幸不死之后还能逃一次（D-352：``reprieve``）：这时逃不成是因为没有路可逃，不是伤得动不了；
        # 想照常走开的，告诉他走不动、但还逃得了，免得他以为只能等死
        if not has_status(me, "reprieve"):
            return replies.TOO_HURT
        if verb == "follow":
            return replies.TOO_HURT
        if verb == "go":
            can_flee = any(a.verb == "flee" for a in actions.legal_actions(state, defs, me.id))
            return replies.HURT_CAN_FLEE if can_flee else replies.TOO_HURT
    if verb in ("go", "follow") and has_status(me, "captive"):
        return replies.CAPTIVE
    if verb in ("study", "practice", "teach", "rest") and actions.tense_for(state, me):
        return replies.TENSE
    if verb == "rest":
        from tianlong.kernel import danger

        if danger.hostiles_toward(state, me.id):
            return replies.HOSTILE_NEAR
    if verb == "release":
        return replies.NOT_HELD
    return replies.CANNOT


def explain_unclear(state: WorldState, defs: Defs, clean: str) -> str | None:
    """读不懂的一句里若有个此刻做不到的动作（例如拿已经在身上的东西），就说明为什么做不到。"""
    narr, _ = lx.split_speech(clean)
    found = lx.verbs_in(narr)
    if not found:
        return None
    legal = actions.legal_actions(state, defs, state.player)
    verbs = {a.verb for a in legal}
    verb = found[0][1]
    if verb in verbs:
        return None
    me = state.chars[state.player]
    if verb == "take" and any(lx.match_score(state, defs, i, narr) >= 2 for i in me.inventory):
        return replies.ALREADY_HAVE
    return mentioned_absent(state, defs, clean, legal, floor=2) or cannot_reason(state, defs, verb)


# ---------------------------------------------------------------- the physical action


def _with_duration(state: WorldState, defs: Defs, a: ActionInst, clean: str, legal: list[ActionInst]
                   ) -> ActionInst | None:
    ticks = lx.duration_ticks(clean)
    if ticks is None:
        return a
    params = dict(a.params)
    if a.verb == "wait":
        params["ticks"] = max(1, min(96, ticks))
    else:
        params["hours"] = max(1, min(actions.MAX_HOURS, math.ceil(ticks / 8)))
    sig = actions.signature(a.verb, params)
    for b in legal:
        if b.signature == sig:
            return b
    built = actions.build(state, defs, state.player, a.verb, params)
    return built if built is not None else a


def align(state: WorldState, defs: Defs, result: ParseResult) -> tuple[ActionInst | None, str]:
    """动词加对象 → 合法清单里的一个动作实例；对不上返回 (None, 戏内回话)。"""
    it = result.intent
    pid = state.player
    legal = actions.legal_actions(state, defs, pid)
    verb = it.verb or ""
    if not legal:
        return None, replies.CANNOT
    if verb == "setpiece":
        for a in legal:
            if a.verb == "setpiece" and f"{a.params.get('situation')}:{a.params.get('name')}" == it.target:
                return a, ""
        return None, replies.CANNOT
    targets = [t for t in (it.target, it.target2) if t]
    for t in targets:
        why = _absent_reply(state, defs, t, legal)
        if why is not None:
            return None, why
    cands = [a for a in legal if a.verb == verb]
    if not cands:
        return None, cannot_reason(state, defs, verb)
    if targets:
        matching = [a for a in cands if all(t in _values(state, a) for t in targets)]
        if not matching and it.target:
            matching = [a for a in cands if it.target in _values(state, a)]
        if not matching:
            return None, cannot_reason(state, defs, verb)
    else:
        matching = cands
    clean = result.clean
    if verb == "attack":
        want = "kill" if rules.kill_intent(clean) else "injure"
        pref = [a for a in matching if a.params.get("intent") == want]
        matching = pref or matching
    if verb in DURATION_VERBS:
        base = matching[0]
        if verb == "rest" and lx.duration_ticks(clean) is None:
            base = next((a for a in matching if a.params.get("hours") == 2), base)
        chosen = _with_duration(state, defs, base, clean, legal)
        return (chosen, "") if chosen is not None else (None, replies.CANNOT)
    if not targets:
        if verb == "go":
            hop = first_hop(state, defs, clean, matching)
            if hop is not None:
                return hop, ""
        if verb == "hide":
            loc = state.locations[state.chars[pid].loc]
            feats = [a for a in matching if a.params.get("feature")]
            if feats:
                return max(feats, key=lambda a: loc.features[a.params["feature"]].cover), ""
        if verb == "search":
            whole = [a for a in matching if not a.params.get("feature")]
            if whole:
                return whole[0], ""
        if verb == "examine":
            look = next((a for a in legal if a.verb == "look"), None)
            if look is not None:
                return look, ""
        objects = {frozenset(_values(state, a)) for a in matching}
        if len(objects) > 1:
            return None, mentioned_absent(state, defs, clean, legal) or replies.WHICH
    elif (exact := [a for a in matching if _values(state, a) - {"injure", "kill"} == set(targets)]):
        return exact[0], ""
    elif len(matching) > 1 and len({frozenset(_values(state, a)) for a in matching}) > 1:
        # 指定了一个对象、另一个没指定（例如给谁）：在场只有一种可能才算数
        return None, replies.WHICH
    return matching[0], ""


# ---------------------------------------------------------------- the utterance


def _pick_request(state: WorldState, to: list[str]) -> str | None:
    pid = state.player
    mine = [r for r in speech.open_requests_to(state, pid) if not to or r.frm in to]
    mine += [r for r in speech.countered_from(state, pid) if not to or r.to in to]
    mine.sort(key=lambda r: -r.created)
    return mine[0].id if mine else None


def _claims(state: WorldState, defs: Defs, result: ParseResult) -> tuple[list[Claim], list[str], list[str]]:
    """断言 → (claims, fact_refs, 要旨片段)。结论一律由 kernel.beliefs.claim_verdict 下。"""
    pid = state.player
    me = state.chars[pid]
    menu = result.menu
    claims: list[Claim] = []
    fact_refs: list[str] = []
    gist: list[str] = []
    for ref in result.intent.claim_refs:
        if ref == "self_unlisted":
            v = beliefs.claim_verdict(state, pid, None, self_claim=True)
            claims.append(Claim(text="自称做过一件事", ref=None, verdict=v))  # type: ignore[arg-type]
            gist.append("说起自己做过的一件事")
            continue
        s = menu.self_event(ref)
        if s is not None:
            v = beliefs.claim_verdict(state, pid, ref, self_claim=True)
            eid = ref.split(":", 1)[1]
            fid = f"E{eid}" if f"E{eid}" in state.facts else None
            claims.append(Claim(text=s.text, ref=fid or ref, verdict=v))  # type: ignore[arg-type]
            if v == "true" and fid and (b := me.beliefs.get(fid)) is not None and b.confidence >= beliefs.BELIEVE:
                fact_refs.append(fid)
            gist.append("自称：" + safe_text(state, defs, s.text))
            continue
        c = menu.claim(ref)
        text = c.text if c is not None else (state.facts[ref].text if ref in state.facts else "")
        if not text:
            continue
        v = beliefs.claim_verdict(state, pid, ref)
        claims.append(Claim(text=text, ref=ref, verdict=v))  # type: ignore[arg-type]
        if v == "true" and ref in state.facts:
            b = me.beliefs.get(ref)
            if b is not None and b.confidence >= beliefs.BELIEVE:
                fact_refs.append(ref)
        gist.append(safe_text(state, defs, text) if v == "true" else "说起一件事")
    return claims, fact_refs, gist


def _evidence(state: WorldState, defs: Defs, clean: str, action: ActionInst | None) -> list[str]:
    pid = state.player
    me = state.chars[pid]
    out: list[str] = []
    if action is not None and action.verb in ("show", "give") and action.params.get("item") in me.inventory:
        out.append(action.params["item"])
    for iid in me.inventory:
        itm = state.items.get(iid)
        if itm is None or iid in out:
            continue
        if (itm.props.get("evidence") or "token" in itm.tags) and lx.match_score(state, defs, iid, clean) >= 4:
            out.append(iid)
    return out


def _about(state: WorldState, defs: Defs, result: ParseResult, to: list[str], cand: AskCand | None) -> str | None:
    if cand is not None and cand.about:
        return cand.about
    t = result.intent.target
    if t and t not in to and t != state.player:
        return t
    skip = set(to) | {state.player}
    for eid, _ in lx.match_entities(state, defs, result.clean, defs.lexicon.names.keys(), floor=3):
        if eid not in skip:
            return eid
    return None


def _in_charge(state: WorldState, hearers: list[str]) -> str | None:
    """对众人开口相求：落到在场主事的人头上（在场听他吩咐的人最多的那一位）；没有这样的人返回 None。"""
    def subs(h: str) -> int:
        return sum(1 for o in state.chars.values() if o.superior == h and o.loc == state.chars[h].loc)

    ranked = sorted(hearers, key=lambda h: -subs(h))
    if not ranked or subs(ranked[0]) == 0:
        return None
    if len(ranked) > 1 and subs(ranked[1]) == subs(ranked[0]):
        return None
    return ranked[0]


def _retarget(state: WorldState, defs: Defs, cand: AskCand, who: str, text: str) -> AskCand | None:
    """候选是按别人列的、话却明明是对 who 说的：换成 who 做得到的同一件事；他做不到这件事返回 None。"""
    for c in request_candidates(state, defs, state.player, who, text=text, limit=64):
        if c.type == cand.type and c.slots == cand.slots:
            return c
    return None


def other_request(state: WorldState, result: ParseResult, to: list[str], hearers: list[str]) -> AskCand | None:
    """求的事不在候选里（「撤兵」「借个火」）：仍记成一个请求，让内核照常裁决、计重复（D）。

    动作写成对方等一等，带 other 指纹（同一句话重复说才算同一个请求）；类别至少算「费事」。
    """
    who = to[0] if to else (hearers[0] if len(hearers) == 1 else _in_charge(state, hearers))
    if who is None:
        return None
    fp = f"{zlib.crc32(result.clean.encode('utf-8')):08x}"
    desc = f"{refs.ref(state.player)}开口相求的一件事"
    return AskCand(to=who, action={"verb": "wait", "params": {"ticks": 1}, "desc": desc, "other": fp}, desc=desc,
                   cls=_cls_max("effort", result.intent.cls), type="other")


def build_speech(state: WorldState, defs: Defs, result: ParseResult, action: ActionInst | None
                 ) -> tuple[Speech | None, str]:
    """组一句 Speech；说不出口（没人听）返回 (None, 回话)。"""
    pid = state.player
    me = state.chars[pid]
    acts = speech.available_acts(state, defs, me)
    hearers = [o.id for o in speech.audience(state, me)]
    if not acts or not hearers:
        return None, replies.NOBODY
    it = result.intent
    menu = result.menu
    act = it.act or "tell"
    to = [t for t in it.to if t in hearers]
    cand = menu.ask(it.asks) if (act in speech.REQUEST_ACTS or act == "ask") else None
    if cand is not None and cand.to not in hearers:
        cand = None
    if cand is not None and to and cand.to not in to:
        # 说话的对象与候选所属的人对不上：以说话的对象为准，换成他做得到的同一件事（没有就照候选的）
        cand = _retarget(state, defs, cand, to[0], result.clean) or cand
    if act in speech.REQUEST_ACTS:
        if cand is None:
            cand = other_request(state, result, to, hearers)
        if cand is None:
            act = "tell"
        else:
            to = [cand.to]
            if act == "plead" and "plead" not in acts:
                act = "request"
            if act == "order" and ("order" not in acts or state.chars[cand.to].superior != pid):
                act = "request"
    if act not in acts:
        act = "tell" if "tell" in acts else acts[0]
    claims, fact_refs, gist_parts = _claims(state, defs, result)
    sp = Speech(
        act=act,
        to=to,
        stance=result.stance or "plain",
        spoken_text=result.clean,
        whisper=bool(result.whisper and to),
    )
    if act in speech.REQUEST_ACTS and cand is not None:
        sp.ask_action = {k: v for k, v in cand.action.items()}
        kernel_cls = speech.classify_request(state, defs, pid, cand.to, {"verb": cand.action.get("verb"),
                                                                         "params": cand.action.get("params", {})})
        sp.action_class = _cls_max(kernel_cls, cand.cls, it.cls)
        target = state.chars[cand.to]
        if it.urgent and has_need(state, target):
            sp.leverage = {"urgent": True}
        sp.gist = safe_text(state, defs, cand.desc)
    elif act == "ask":
        sp.about = _about(state, defs, result, to, cand)
        sp.gist = ("问起" + safe_text(state, defs, refs.ref(sp.about))) if sp.about else "问了一件事"
    elif act in ("agree", "refuse", "bargain"):
        sp.request = _pick_request(state, to)
        if act == "bargain" and sp.request:
            r = state.requests[sp.request]
            opts = speech.counter_options(state, defs, r)
            sp.condition = opts[0] if opts else None
    if act in ("tell", "warn"):
        if claims:
            sp.claims = claims
            sp.fact_refs = fact_refs
            sp.gist = "；".join(dict.fromkeys(gist_parts))
        else:
            sp.claims = [Claim(text=OPINION_TEXT, ref=None, verdict="opinion")]
    elif claims:
        # 请求、询问里夹带的断言：真的那几条算凭据（6.6）；其余只是话
        sp.claims = claims
        sp.fact_refs = fact_refs
    sp.evidence = _evidence(state, defs, result.clean, action)
    return sp, ""


def is_repeat(state: WorldState, sp: Speech) -> bool:
    if sp.act not in speech.REQUEST_ACTS or not sp.ask_action or not sp.to:
        return False
    action = dict(sp.ask_action)
    action.setdefault("params", {})
    for r in state.requests.values():
        if (r.frm == state.player and r.to == sp.to[0] and r.kind == sp.act and r.action == action
                and r.status not in ("expired", "agreed")):
            return True
    return False


# ---------------------------------------------------------------- entry point


def bind(state: WorldState, defs: Defs, result: ParseResult) -> Bound:
    """ParseResult → Bound。永远返回一个合法动作，或一个不耗时的非动作种类。"""
    try:
        return _bind(state, defs, result)
    except Exception as e:  # noqa: BLE001 — 解析出错也不让游戏中断
        return Bound(kind="unclear", reply=replies.unclear_for(result.clean), attempts=list(result.attempts),
                     via=result.via, errors=[f"{type(e).__name__}: {e}"])


def _bind(state: WorldState, defs: Defs, result: ParseResult) -> Bound:
    it = result.intent
    common = {"attempts": list(result.attempts), "via": result.via}
    if result.absurd or rules.banned_hit(defs, result.clean) is not None:
        return Bound(kind="impossible", absurd=True, reply=replies.ABSURD, **common)
    if it.kind == "meta":
        return Bound(kind="meta", reply=replies.META, **common)
    if it.kind == "unclear":
        why = explain_unclear(state, defs, result.clean)
        if why is not None:
            return Bound(kind="impossible", reply=why, **common)
        return Bound(kind="unclear", reply=safe_note(state, defs, it.note, replies.unclear_for(result.clean)),
                     **common)
    if it.kind == "impossible":
        return Bound(kind="impossible", reply=safe_note(state, defs, it.note, replies.CANNOT), **common)
    me = state.chars.get(state.player)
    if me is None or not actions.can_act(me):
        return Bound(kind="impossible", reply=replies.CANNOT, **common)
    action: ActionInst | None = None
    if it.kind in ("act", "both") and it.verb:
        action, why = align(state, defs, result)
        if action is None:
            kind: Kind = "unclear" if why == replies.WHICH else "impossible"
            return Bound(kind=kind, reply=why, **common)
    sp: Speech | None = None
    if it.kind in ("say", "both"):
        sp, why = build_speech(state, defs, result, action)
        if sp is None and action is None:
            return Bound(kind="impossible", reply=why, **common)
    if action is None and sp is None:
        return Bound(kind="unclear", reply=replies.unclear_for(result.clean), **common)
    if action is None:
        legal = actions.legal_actions(state, defs, state.player)
        action = next((a for a in legal if a.verb == "wait" and a.params.get("ticks") == 1), None)
        if action is None:
            action = actions.build(state, defs, state.player, "wait", {"ticks": 1})
        if action is None:
            return Bound(kind="impossible", reply=replies.CANNOT, **common)
    return Bound(
        kind="action",
        action=action,
        speech=sp,
        lethal=action.lethal,
        signature=action.signature,
        repeat=is_repeat(state, sp) if sp is not None else False,
        **common,
    )
