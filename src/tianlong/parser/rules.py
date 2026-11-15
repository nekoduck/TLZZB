"""规则猜测（D：给 MockJev 的提示、C1 的 Mock 默认值、``rules_only`` 路由）。

只认通用的汉语字词与场景里实体的名字、叫法；真模型在时它不做决定。猜错了也无妨：
下游还有 bind 与内核的合法性检查，猜错最多让归类出错，改不了世界。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tianlong.kernel import actions, perception, speech
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.parser import lexical as lx
from tianlong.parser.candidates import Menu, ask_score, has_need, rule_class

REQUESTISH = ("request", "plead", "order")
# 动词 → 它的对象优先取哪类东西
TARGET_KINDS: dict[str, tuple[str, ...]] = {
    "go": ("exit",),
    "flee": ("exit",),
    "follow": ("person",),
    "hide": ("feature",),
    "examine": ("person", "item", "feature"),
    "search": ("feature",),
    "take": ("item",),
    "drop": ("item",),
    "give": ("item", "person"),
    "show": ("item", "person"),
    "use": ("item", "person"),
    "steal": ("item", "person"),
    "help": ("person",),
    "restrain": ("person",),
    "release": ("person",),
    "attack": ("person",),
    "block": ("person",),
    "push": ("person", "feature"),
    "study": ("item", "person", "art"),
    "practice": ("art",),
    "teach": ("person", "art"),
}


@dataclass
class Guess:
    kind: str = "unclear"  # act / say / both / meta / unclear / impossible
    verb: str | None = None  # kernel 动词，或 'setpiece'
    target: str | None = None  # 实体 id；情境动作为 '<情境 id>:<动作名>'
    target2: str | None = None
    has_speech: bool = False
    meaningful: bool = True
    act: str | None = None
    to: list[str] = field(default_factory=list)
    ask: str | None = None  # AskCand.key
    claims: list[str] = field(default_factory=list)  # ClaimCand.key
    selfs: list[str] = field(default_factory=list)  # SelfCand.key 或 'self_unlisted'
    cls: str | None = None
    urgent: bool = False
    absurd: bool = False
    narr: str = ""
    spoken: str = ""
    stance: str = "plain"
    note: str = ""


def banned_hit(defs: Defs, text: str) -> str | None:
    """文字里出现这个世界没有的东西（lexicon 的 banned_modern）。"""
    for w in defs.lexicon.banned_modern:
        if w and w in (text or ""):
            return w
    return None


def meaningful(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if lx.has_cjk(t):
        return True
    return bool(lx.META.search(t))


# ---------------------------------------------------------------- people


def last_partner(state: WorldState) -> str | None:
    """玩家最近打过交道的人：他最近一个动作的对象，或最近冲他说话、在他眼前动作的人。"""
    me = state.chars[state.player]
    here = {o.id for o in perception.present(state, me.loc, exclude=me.id) if perception.visible_to(me, o)}
    for ev in reversed(state.events_tail[-40:]):
        if ev.actor == me.id:
            t = ev.params.get("target")
            if isinstance(t, str) and t in here:
                return t
            for t in ev.params.get("to", []) or []:
                if t in here:
                    return t
        elif ev.actor in here and ev.witnesses.get(me.id) == "saw":
            return ev.actor
    return None


def resolve_pronoun(state: WorldState, menu: Menu) -> str | None:
    people = [o.ref for o in menu.objects if o.kind == "person"]
    if len(people) == 1:
        return people[0]
    lp = last_partner(state)
    return lp if lp in people else None


def _best_named(state: WorldState, defs: Defs, text: str, hearers: list[str]) -> str | None:
    if not text:
        return None
    scored = [(lx.match_score(state, defs, h, text), -i, h) for i, h in enumerate(hearers)]
    s, _, h = max(scored)
    return h if s >= 2 else None


def _by_title(state: WorldState, defs: Defs, head: str, hearers: list[str]) -> str | None:
    me = state.chars[state.player]
    if lx.MASTER_TITLES.search(head) and me.superior in hearers:
        return me.superior
    if lx.AUTHORITY_TITLES.match(head):
        subs = {h: sum(1 for o in state.chars.values() if o.superior == h and o.loc == state.chars[h].loc)
                for h in hearers}
        best = max(hearers, key=lambda h: subs[h])
        if subs[best] > 0:
            return best
    if lx.FEMALE_WORDS.search(head):
        from tianlong.kernel import refs as krefs

        fem = [h for h in hearers
               if lx.FEMALE_MARK.search(krefs.display_name(state, defs, h, state.player) + state.chars[h].name)]
        if len(fem) == 1:
            return fem[0]
    return None


def addressee(state: WorldState, defs: Defs, clean: str, narr: str, spoken: str, menu: Menu) -> list[str]:
    """说话的对象（规则猜测）：先叫的人、「对某人说」、唯一在场的人、冲着自己来的人、刚打过交道的人。"""
    hearers = [o.ref for o in menu.people]
    if not hearers:
        return []
    me = state.chars[state.player]
    # 开口先叫人：「X，……」
    m = lx.VOCATIVE_RE.match(spoken or "")
    if m:
        head = m.group(1)
        who = _best_named(state, defs, head, hearers) or _by_title(state, defs, head, hearers)
        if who is not None:
            return [who]
    # 「对 X 说」「问 X」「告诉 X」
    for text in (narr, clean):
        m2 = lx.SPEECH_OBJ_RE.search(text or "")
        if m2:
            obj = m2.group(1)
            who = _best_named(state, defs, obj, hearers) or _by_title(state, defs, obj, hearers)
            if who is None and lx.PRONOUNS.search(obj):
                p = resolve_pronoun(state, menu)
                who = p if p in hearers else None
            if who is not None:
                return [who]
    if len(hearers) == 1:
        return [hearers[0]]
    # 不带称呼的开头：「公子当心……」
    who = _best_named(state, defs, (spoken or "")[:4], hearers)
    if who is not None:
        return [who]
    who = _by_title(state, defs, (spoken or "")[:4], hearers)
    if who is not None:
        return [who]
    # 冲着「你」说而没叫人：冲着自己来的人，或刚打过交道的人
    if lx.SECOND_PERSON.search(spoken or ""):
        from tianlong.kernel import danger

        agg = [h for h in hearers if danger.is_hostile(state, state.chars[h], me.id)]
        if len(agg) == 1:
            return agg
        lp = last_partner(state)
        if lp in hearers:
            return [lp]
    return []


# ---------------------------------------------------------------- speech acts


def speech_act(state: WorldState, spoken: str, acts: list[str]) -> str | None:
    if not spoken or not acts:
        return None
    me = state.chars[state.player]
    has_reqs = bool(speech.open_requests_to(state, me.id) or speech.countered_from(state, me.id))
    for name, pat in lx.ACT_PATTERNS:
        if not pat.search(spoken):
            continue
        if name in ("agree", "refuse", "bargain") and not has_reqs:
            continue
        if name == "plead" and "plead" not in acts:
            return "request" if "request" in acts else None
        if name == "greet" and (lx.REQUEST_MARK.search(spoken) or lx.QUESTION.search(spoken)):
            continue
        if name in acts:
            return name
    if lx.REQUEST_MARK.search(spoken) and "request" in acts:
        return "request"
    if lx.QUESTION.search(spoken) and "ask" in acts:
        return "ask"
    return "tell" if "tell" in acts else acts[0]


# ---------------------------------------------------------------- the guess


def _pick_targets(state: WorldState, defs: Defs, verb: str, narr: str, menu: Menu) -> tuple[str | None, str | None]:
    kinds = TARGET_KINDS.get(verb, ())
    if not kinds:
        return None, None
    hits = [(o, lx.match_score(state, defs, o.ref, narr), lx.first_mention(state, defs, narr, o.ref))
            for o in menu.objects if o.kind in kinds]
    hits = [h for h in hits if h[1] >= 2]
    # 称呼：东家、小姑娘
    if "person" in kinds and not any(h[0].kind == "person" for h in hits):
        hearers = [o.ref for o in menu.objects if o.kind == "person"]
        who = _by_title(state, defs, narr, hearers) if hearers else None
        if who is not None:
            o = menu.obj(who)
            if o is not None:
                hits.append((o, 5, 0))
    # 指代：他、她
    if lx.PRONOUNS.search(narr) and "person" in kinds and not any(h[0].kind == "person" for h in hits):
        p = resolve_pronoun(state, menu)
        if p is not None:
            o = menu.obj(p)
            if o is not None:
                hits.append((o, 5, narr.find(next(iter(lx.PRONOUNS.findall(narr)), "")) if narr else 0))
    if not hits:
        return None, None
    # 主对象按动词的首选类别，同类按出现先后
    def rank(h: tuple) -> tuple[int, int, int]:
        o, s, pos = h
        return (kinds.index(o.kind), pos, -s)

    hits.sort(key=rank)
    first = hits[0][0]
    second = next((h[0] for h in hits[1:] if h[0].ref != first.ref and h[0].kind != first.kind), None)
    return first.ref, second.ref if second else None


def _setpiece(state: WorldState, defs: Defs, narr: str, menu: Menu) -> str | None:
    best, best_s = None, 0
    nb = lx.bigrams(narr)
    vs = {v for _, v, _ in lx.verbs_in(narr)}
    for o in menu.verbs:
        if o.kind != "setpiece":
            continue
        shared = len(nb & lx.bigrams(o.desc))
        s = shared + (2 if vs & {v for _, v, _ in lx.verbs_in(o.desc)} else 0)
        if shared >= 2 and s > best_s:
            best, best_s = o.ref, s
    return best if best_s >= 3 else None


_STOP_BIGRAMS = {"一个", "不是", "知道", "我们", "你们", "他们", "这个", "那个", "什么", "没有", "自己", "一点",
                 "起来", "出来", "下来", "上来", "这里", "那里", "怎么", "可是", "就是", "还是", "已经"}


def _strip_names(state: WorldState, defs: Defs, text: str, ids: list[str]) -> str:
    out = text
    for eid in ids:
        for nm, _ in sorted(lx.names_of(state, defs, eid), key=lambda nw: -len(nw[0])):
            if nm and len(nm) >= 2:
                out = out.replace(nm, " ")
    return out


def _claims(state: WorldState, defs: Defs, spoken: str, menu: Menu) -> list[str]:
    """断言的规则猜测：说到了事实里的人或物，并且有实在的字词对得上。"""
    if not spoken:
        return []
    from tianlong.kernel import refs as krefs

    scored: list[tuple[int, str]] = []
    for c in menu.claims:
        rs = krefs.refs_in(c.text)
        named = [r for r in rs if r != state.player and lx.match_score(state, defs, r, spoken) >= 2]
        ent = len(named) + (1 if state.player in rs and "我" in spoken else 0)
        fact_core = krefs.REF_RE.sub(" ", c.text)
        spoken_core = _strip_names(state, defs, spoken, named)
        shared = len((lx.bigrams(spoken_core) & lx.bigrams(fact_core)) - _STOP_BIGRAMS)
        if (ent >= 1 and shared >= 1) or shared >= 3:
            scored.append((-(3 * ent + 2 * shared), c.key))
    scored.sort()
    return [k for _, k in scored[:3]]


def _selfs(state: WorldState, defs: Defs, spoken: str, menu: Menu) -> list[str]:
    if not spoken or not lx.SELF_CLAIM.search(spoken):
        return []
    vs = {v for _, v, _ in lx.verbs_in(spoken)}
    if not vs:
        return []
    out = []
    for s in menu.selfs:
        if s.verb in vs and (not s.refs or any(lx.match_score(state, defs, r, spoken) >= 2 for r in s.refs)):
            out.append(s.key)
    return out[:2] or ["self_unlisted"]


def guess(state: WorldState, defs: Defs, clean: str, menu: Menu) -> Guess:
    g = Guess()
    hit = banned_hit(defs, clean)
    if hit is not None:
        g.kind, g.absurd, g.meaningful = "impossible", True, True
        return g
    if not meaningful(clean):
        g.kind, g.meaningful = "unclear", False
        return g
    if lx.META.search(clean):
        g.kind = "meta"
        return g
    narr, spoken = lx.split_speech(clean)
    g.narr, g.spoken = narr, spoken
    # 身体动作
    legal_verbs = {o.ref for o in menu.verbs if o.kind == "verb"}
    sp = _setpiece(state, defs, narr, menu) if narr else None
    if sp is not None:
        g.verb, g.target = "setpiece", sp
    elif narr:
        found = lx.verbs_in(narr)
        if found:
            pick = next((v for _, v, _ in found if v in legal_verbs), found[0][1])
            g.verb = pick
            if pick in legal_verbs:
                g.target, g.target2 = _pick_targets(state, defs, pick, narr, menu)
                # 「看看某样东西」「读读石碑」：有具体对象就是细看
                if pick in ("look", "study") and "examine" in legal_verbs and lx.LOOK_AT.search(narr):
                    t1, _t2 = _pick_targets(state, defs, "examine", narr, menu)
                    if t1 is not None and (pick == "look" or "#" in t1):
                        g.verb, g.target, g.target2 = "examine", t1, None
                if g.verb == "examine" and g.target is None and "look" in legal_verbs:
                    g.verb = "look"
    # 说的话
    acts = [o.ref for o in menu.acts]
    if spoken and acts:
        g.has_speech = True
        g.act = speech_act(state, spoken, acts)
        g.to = addressee(state, defs, clean, narr, spoken, menu)
        g.stance = lx.stance_of(spoken)
        if g.act in REQUESTISH:
            cands = [a for a in menu.asks if not g.to or a.to in g.to]
            best, best_s = None, 0
            for a in cands:
                s = ask_score(state, defs, a, spoken, state.player)
                if s > best_s:
                    best, best_s = a, s
            if best is not None:
                g.ask = best.key
                if not g.to:
                    g.to = [best.to]
                g.cls = rule_class(state, defs, best, state.player)
                target = state.chars.get(best.to)
                g.urgent = bool(target is not None and has_need(state, target) and lx.URGENT_WORDS.search(spoken))
        g.claims = _claims(state, defs, spoken, menu)
        g.selfs = _selfs(state, defs, spoken, menu)
    elif spoken:
        g.has_speech = True  # 没人听得见；bind 会说明
    has_act = g.verb is not None
    if has_act and g.has_speech:
        g.kind = "both"
    elif has_act:
        g.kind = "act"
    elif g.has_speech:
        g.kind = "say"
    else:
        g.kind = "unclear"
    return g


def kill_intent(text: str) -> bool:
    return bool(lx.KILL_WORDS.search(text or ""))


def legal_verbs(legal: list[actions.ActionInst]) -> set[str]:
    return {a.verb for a in legal}
