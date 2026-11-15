"""言语行为与请求裁决（方案 6.6）；``speech_options``（ARCHITECTURE 2.3）。

请求一建立就算分：

```text
never         撞上对方的底线
score = W[类别][关系档] + 20 × 强凭据 + 10 × 弱凭据 + 筹码 - 10 × (attempts - 1)
may >= 60；conditional 40 到 60；no < 40
```
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tianlong.kernel import beliefs, bottom, danger, effects, events, mood, perception, predicates, refs
from tianlong.kernel.defs import BlockDef, Defs
from tianlong.kernel.state import (
    TICKS_PER_DAY,
    Character,
    Ctx,
    Event,
    FactId,
    Promise,
    Request,
    Speech,
    Status,
    Step,
    WorldState,
    has_status,
    log_violation,
    next_id,
)

ACTS = (
    "greet",
    "tell",
    "ask",
    "request",
    "agree",
    "refuse",
    "bargain",
    "promise",
    "threaten",
    "plead",
    "order",
    "warn",
    "comfort",
    "insult",
    "taunt",
    "lie",
)
BASE_ACTS = (
    "greet",
    "tell",
    "ask",
    "request",
    "promise",
    "threaten",
    "warn",
    "comfort",
    "insult",
    "taunt",
    "lie",
)
REQUEST_ACTS = ("request", "plead", "order")
REPLY_ACTS = ("agree", "refuse", "bargain")
ACT_PHRASE: dict[str, str] = {
    "greet": "打了个招呼",
    "tell": "说",
    "ask": "问",
    "request": "请求",
    "agree": "答应了",
    "refuse": "拒绝了",
    "bargain": "开了个条件",
    "promise": "许诺",
    "threaten": "威胁",
    "plead": "求情",
    "order": "吩咐",
    "warn": "示警",
    "comfort": "安慰",
    "insult": "骂了几句",
    "taunt": "出言相激",
    "lie": "说",
}
VERDICT_TEXT = {
    "never": "决不答应",
    "no": "不会答应",
    "conditional": "可以谈条件",
    "may": "可以答应，也可以不答应",
}
MAX_FACTS = 8
ORDER_BONUS = 60
PROMISE_DAYS = 1
EVIDENCE_WINDOW = 16  # 出示信物在多少刻之内算数
DETAIL = "weak:detail:"  # 说得出当事人才知道的细节：一桩请求只算一次
ALERT_TICKS = 2


class RequestView(BaseModel):
    request: Request
    verdict_text: str
    allowed_replies: list[str]
    counter_options: list[dict] = []


class SpeechOptions(BaseModel):
    acts: list[str] = []
    facts: list[FactId] = []
    requests: list[RequestView] = []
    star: dict | None = None


# ---------------------------------------------------------------- who hears


def audience(state: WorldState, speaker: Character) -> list[Character]:
    """说话时听得到的人：同处一地、清醒、看得见的人。"""
    return [o for o in perception.visible_people(state, speaker) if perception.conscious(o)]


def _card_blocks(defs: Defs, c: Character) -> list[BlockDef]:
    return bottom.card_blocks(defs, c)


def say_blocked(
    state: WorldState,
    defs: Defs,
    c: Character,
    act: str,
    *,
    fact: str | None = None,
    to: list[str] | None = None,
) -> bool:
    """人物底线是否禁止这句话（verb 为 say 的 blocks；匹配规则见 ``kernel/bottom.py``）。"""
    return bottom.blocked(state, defs, c, "say", {"act": act, "fact": fact}, to=to)


def holds_captive(state: WorldState, holder: Character) -> bool:
    """holder（或听命于他的人）在这里扣着人：同处一地、被捆或被看押、来源是他或他的手下。"""
    for o in perception.present(state, holder.loc, exclude=holder.id):
        for st in o.statuses:
            if st.kind not in ("captive", "bound") or not st.source:
                continue
            src = state.chars.get(st.source)
            if st.source == holder.id or (src is not None and src.superior == holder.id):
                return True
    return False


def can_plead_to(state: WorldState, speaker: Character, target: Character) -> bool:
    """求饶、求情（6.6）：对方对自己有敌意；或对在场的另一个人有敌意；或手里扣着人（K17：替别人求情）。"""
    if danger.is_hostile(state, target, speaker.id):
        return True
    for o in perception.present(state, speaker.loc, exclude=speaker.id):
        if o.id != target.id and danger.is_hostile(state, target, o.id):
            return True
    return holds_captive(state, target)


def open_requests_to(state: WorldState, cid: str) -> list[Request]:
    return [r for r in state.requests.values() if r.to == cid and r.status in ("open", "countered")]


def countered_from(state: WorldState, cid: str) -> list[Request]:
    return [r for r in state.requests.values() if r.frm == cid and r.status == "countered"]


def available_acts(state: WorldState, defs: Defs, c: Character) -> list[str]:
    """此刻可用的言语行为。"""
    if not perception.conscious(c) or not perception.on_map(c):
        return []
    others = audience(state, c)
    if not others:
        return []
    acts = list(BASE_ACTS)
    to_me = open_requests_to(state, c.id)
    if any(
        r.kind != "question" and r.status == "open" and r.verdict == "may" for r in to_me
    ) or countered_from(state, c.id):
        acts.append("agree")
    if to_me or countered_from(state, c.id):
        acts.append("refuse")
    if any(r.kind != "question" and r.status == "open" and r.verdict == "conditional" for r in to_me):
        acts.append("bargain")
    if any(can_plead_to(state, c, o) for o in others):
        acts.append("plead")
    if any(o.superior == c.id for o in others):
        acts.append("order")
    out = []
    for a in ACTS:
        if a in acts and a not in out and not say_blocked(state, defs, c, a):
            out.append(a)
    return out


def speakable_facts(
    state: WorldState, defs: Defs, c: Character, topic: list[str] | None = None
) -> list[FactId]:
    """可以说的事实：自己相信、不触底线；按与眼前话题的相关度取前 8 条。"""
    here = {o.id for o in audience(state, c)} | {c.loc}
    topic_set = set(topic or []) | here
    scored: list[tuple[int, int, str]] = []
    for fid, b in c.beliefs.items():
        if b.confidence < predicates.BELIEVE:
            continue
        f = state.facts.get(fid)
        if f is None or say_blocked(state, defs, c, "tell", fact=fid):
            continue
        rs = set(refs.refs_in(f.text))
        score = 2 * len(rs & topic_set) + (1 if f.gossip else 0)
        scored.append((-score, -b.tick, fid))
    scored.sort()
    return [fid for _, _, fid in scored[:MAX_FACTS]]


def allowed_replies(r: Request) -> list[str]:
    if r.kind == "question":
        return ["tell", "refuse", "lie"]
    if r.verdict in ("never", "no"):
        return ["refuse"]
    if r.verdict == "conditional":
        return ["bargain", "refuse"]  # 4.5：可以谈条件时，答复里没有「答应」
    return ["agree", "refuse"]


def counter_options(state: WorldState, defs: Defs, r: Request) -> list[dict]:
    """还价的条件候选：要对方交出一件值钱的东西；或要对方服下自己手里的毒。"""
    frm, to = state.chars.get(r.frm), state.chars.get(r.to)
    if frm is None or to is None:
        return []
    out: list[dict] = []

    def worth(iid: str) -> int:
        it = state.items[iid]
        return int(it.props.get("value", 0)) + (10 if set(it.tags) & set(events.IMPORTANT_ITEM_TAGS) else 0)

    for iid in sorted(frm.inventory, key=lambda i: -worth(i))[:3]:
        out.append({"verb": "give", "params": {"item": iid, "target": to.id}})
    for iid in to.inventory:
        it = state.items[iid]
        if "poison" in it.tags:
            out.append({"verb": "use", "params": {"item": iid}})
    return out[:5]


def speech_options(state: WorldState, defs: Defs, actor: str) -> SpeechOptions:
    from tianlong.kernel import actions

    c = state.chars[actor]
    acts = available_acts(state, defs, c)
    if not acts:
        return SpeechOptions()
    views = []
    for r in open_requests_to(state, actor):
        views.append(
            RequestView(
                request=r,
                verdict_text="有人问你话" if r.kind == "question" else VERDICT_TEXT[r.verdict],
                allowed_replies=allowed_replies(r) if r.status == "open" else ["refuse"],
                counter_options=counter_options(state, defs, r)
                if r.kind != "question" and r.verdict == "conditional"
                else [],
            )
        )
    for r in countered_from(state, actor):
        views.append(
            RequestView(
                request=r,
                verdict_text="对方开了条件",
                allowed_replies=["agree", "refuse"],
                counter_options=[r.condition] if r.condition else [],
            )
        )
    star = None
    st = danger.current_step(c)
    if st is not None and st.do.get("verb") == "say" and actions.step_ready(state, defs, c, st):
        if st.at is None or st.at == c.loc:
            ctx = Ctx(self_id=actor)
            star = {k: predicates.resolve_id(v, state, ctx) for k, v in st.do.items() if k != "verb"}
            star.setdefault("act", "tell")
    topic = [star["to"]] if star and star.get("to") else None
    return SpeechOptions(acts=acts, facts=speakable_facts(state, defs, c, topic), requests=views, star=star)


# ---------------------------------------------------------------- requests


def classify_request(state: WorldState, defs: Defs, frm: str, to: str, action: dict) -> str:
    """所求之事的默认类别（解析器给了 cls 时以它为准）。"""
    verb = action.get("verb")
    params = action.get("params") or {}
    if verb == "teach":
        art = defs.arts.get(params.get("art", ""))
        if art is not None and art.secrecy == "sect":
            same = predicates.holds(state, defs, art.requires, Ctx(self_id=frm))
            return "effort" if same else "betray"
        return "effort"
    if verb in ("say", "show", "look", "wait", "greet", "listen"):
        return "trivial"
    if verb == "give":
        it = state.items.get(params.get("item", ""))
        if it is not None and (it.props.get("value") or set(it.tags) & set(events.IMPORTANT_ITEM_TAGS)):
            return "costly"
        return "effort"
    if verb == "release":
        return "costly"
    if verb in ("attack", "restrain", "steal", "block", "push", "flee"):
        return "risky"
    return "effort"


def request_blocked(state: WorldState, defs: Defs, r: Request) -> bool:
    """所求之事撞上对方的底线：never。与 ``legal_actions`` 用同一套匹配（``kernel/bottom.py``）。

    所求之事没写对象时，对象按提出请求的人算（「教我」「帮我」「给我」）；``$requester`` 指提出请求的人。
    """
    to = state.chars.get(r.to)
    if to is None:
        return True
    verb = str(r.action.get("verb") or "")
    params = dict(r.action.get("params") or {})
    if verb == "say":
        heard = params.get("to") or params.get("target") or r.frm
        listeners = [heard] if isinstance(heard, str) else [str(x) for x in heard]
        return bottom.blocked(state, defs, to, verb, params, to=listeners, requester=r.frm)
    if not params.get("target"):
        params["target"] = r.frm
    return bottom.blocked(state, defs, to, verb, params, requester=r.frm)


def request_entities(state: WorldState, frm: Character, to: Character, action: dict | None) -> set[str]:
    """一桩请求牵涉到的实体：双方、双方所在的地点、所求之事的各个参数。"""
    out = {frm.id, to.id, frm.loc, to.loc}
    for v in ((action or {}).get("params") or {}).values():
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, list | tuple):
            out.update(str(x) for x in v if isinstance(x, str))
    return out


def relevant_detail(state: WorldState, frm: Character, to: Character, sp: Speech, fid: str) -> bool:
    """「说得出当事人才知道的细节」（6.6 的弱凭据）：说者自己相信、又与所求之事相干的一条事实。

    相干＝这条事实提到了这桩请求牵涉的人、物、地点、本事（``request_entities``）。
    """
    b = frm.beliefs.get(fid)
    f = state.facts.get(fid)
    if b is None or f is None or b.confidence < predicates.BELIEVE:
        return False
    return bool(set(refs.refs_in(f.text)) & request_entities(state, frm, to, sp.ask_action))


def gather_evidence(state: WorldState, defs: Defs, frm: Character, to: Character, sp: Speech) -> list[str]:
    """强凭据：出示信物；对方亲眼见过。弱凭据：对方已知的事；有第三者作证；说得出当事人才知道的细节。

    最后一种一桩请求只算一次（``weak:detail:<事实>``），免得靠罗列琐事堆分。
    """
    out: list[str] = []
    for iid in sp.evidence:
        it = state.items.get(iid)
        if it is None or it.holder != frm.id:
            continue
        out.append(f"strong:item:{iid}" if it.props.get("evidence") == "strong" else f"weak:item:{iid}")
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - EVIDENCE_WINDOW:
            break
        if ev.verb == "show" and ev.actor == frm.id and ev.params.get("target") == to.id:
            iid = ev.params.get("item")
            it = state.items.get(iid or "")
            if it is not None and it.props.get("evidence") == "strong" and f"strong:item:{iid}" not in out:
                out.append(f"strong:item:{iid}")
    others = [o for o in audience(state, frm) if o.id != to.id]
    for fid in sp.fact_refs:
        b = to.beliefs.get(fid)
        if b is not None and b.source == "saw" and b.confidence >= predicates.BELIEVE:
            out.append(f"strong:fact:{fid}")
        elif b is not None and b.confidence >= predicates.BELIEVE:
            out.append(f"weak:fact:{fid}")
        elif any(
            (ob := o.beliefs.get(fid)) is not None and ob.confidence >= predicates.BELIEVE for o in others
        ):
            out.append(f"weak:witness:{fid}")
        elif not any(e.startswith(DETAIL) for e in out) and relevant_detail(state, frm, to, sp, fid):
            out.append(f"{DETAIL}{fid}")
    return out


def leverage_score(state: WorldState, frm: Character, to: Character, lev: dict[str, Any]) -> int:
    """筹码：欠着人情 +20；报酬 +10 到 30；正好帮到对方的急需 +30；可信的威胁加对方 fear 的三分之一。"""
    n = 0
    if to.owes.get(frm.id, 0) > 0:
        n += 20
    reward = lev.get("reward")
    if reward:
        n += max(10, min(30, int(reward)))
    if lev.get("urgent"):
        n += 30
    if lev.get("threat"):
        r = to.rel.get(frm.id)
        n += (r.fear if r else 0) // 3
    return n


def score_request(state: WorldState, defs: Defs, r: Request) -> None:
    """按 6.6 算分并给出裁决。"""
    w = defs.world.request
    b = mood.band(defs, state, r.to, r.frm)
    base = w.weights.get(r.action_class, w.weights.get("effort", {})).get(b, 0)
    strong = sum(1 for e in r.evidence if e.startswith("strong:"))
    weak = sum(1 for e in r.evidence if e.startswith("weak:"))
    score = base + 20 * strong + 10 * weak + r.leverage - 10 * (r.attempts - 1)
    if r.kind == "order":
        score += ORDER_BONUS
    r.score = score
    if request_blocked(state, defs, r):
        r.verdict = "never"
    elif score >= w.may:
        r.verdict = "may"
    elif score >= w.conditional:
        r.verdict = "conditional"
    else:
        r.verdict = "no"


def _same_request(state: WorldState, frm: str, to: str, kind: str, action: dict) -> Request | None:
    for r in state.requests.values():
        if r.frm == frm and r.to == to and r.kind == kind and r.action == action and r.status != "expired":
            return r
    return None


def make_request(
    state: WorldState,
    defs: Defs,
    frm: Character,
    to: Character,
    sp: Speech,
    kind: str,
) -> tuple[Request, bool]:
    """建立或重提请求。返回 (请求, 是否纠缠到第三次)。"""
    action = dict(sp.ask_action or {})
    action.setdefault("params", {})
    cls = sp.action_class or classify_request(state, defs, frm.id, to.id, action)
    if cls not in defs.world.request.weights:
        cls = "effort"
    evidence = gather_evidence(state, defs, frm, to, sp)
    lev = leverage_score(state, frm, to, sp.leverage)
    old = _same_request(state, frm.id, to.id, kind, action)
    nag = False
    if old is not None and old.status != "agreed":
        had_detail = any(e.startswith(DETAIL) for e in old.evidence)
        new_ev = [
            e for e in evidence if e not in old.evidence and not (had_detail and e.startswith(DETAIL))
        ]
        if new_ev or lev > old.leverage:
            old.evidence.extend(new_ev)
            old.leverage = max(old.leverage, lev)
        else:
            old.attempts += 1
            nag = old.attempts >= 3
        old.status = "open"
        old.action_class = cls
        score_request(state, defs, old)
        return old, nag
    r = Request(
        id=next_id(state, "R"),
        frm=frm.id,
        to=to.id,
        action=action,
        action_class=cls,
        evidence=evidence,
        kind=kind,  # type: ignore[arg-type]
        created=state.tick,
        leverage=lev,
    )
    score_request(state, defs, r)
    state.requests[r.id] = r
    return r, False


def _do_pattern(r: Request) -> dict:
    params = dict(r.action.get("params") or {})
    do = {"verb": r.action.get("verb", "wait")}
    do.update(params)
    return do


def accept(state: WorldState, defs: Defs, r: Request) -> None:
    """答应之后：那件事作为一步插到对方打算的最前面；同时算作一桩许诺。"""
    r.status = "agreed"
    to = state.chars[r.to]
    do = _do_pattern(r)
    if r.kind == "question":
        return
    st = Step(
        id=f"req_{r.id}",
        text=f"答应⟦{r.frm}⟧的请求",
        do=do,
        origin="rule",
        deadline=state.tick + PROMISE_DAYS * TICKS_PER_DAY,
    )
    effects.insert_step(state, to, st)
    pid = next_id(state, "P")
    state.promises[pid] = Promise(
        id=pid,
        frm=r.to,
        to=r.frm,
        text=st.text,
        do=do,
        deadline=state.tick + PROMISE_DAYS * TICKS_PER_DAY,
        request=r.id,
    )


# ---------------------------------------------------------------- processing


def gist_for(sp: Speech, speaker: str, state: WorldState) -> str:
    if sp.gist:
        return sp.gist
    act = sp.act
    if act in ("tell", "warn", "lie") and sp.fact_refs:
        return "；".join(state.facts[f].text for f in sp.fact_refs if f in state.facts)
    if act == "greet":
        return f"自称⟦{speaker}⟧"
    if act in REQUEST_ACTS and sp.ask_action:
        return "想让对方" + ask_phrase(sp.ask_action, speaker)
    return ""


# 所求之事的说法（K19）：给人看的要旨里不出现动词的英文名。{target}、{item} 等缺了就退回动词的中文标签。
_ASK_PHRASES: dict[str, str] = {
    "go": "往{exit}去",
    "flee": "逃往{exit}",
    "follow": "跟着{target}走",
    "examine": "细看{obj}",
    "take": "拿起{item}",
    "drop": "放下{item}",
    "give": "把{item}交给{target}",
    "show": "把{item}拿出来给{target}看",
    "use": "用{item}",
    "steal": "去偷{item}",
    "help": "照料{target}",
    "restrain": "拿住{target}",
    "release": "放了{target}",
    "attack": "对{target}动手",
    "block": "拦住{target}",
    "push": "推开{target}",
    "study": "学{art}",
    "practice": "练{art}",
    "teach": "传授{art}",
}
_ASK_PLAIN: dict[str, str] = {
    "go": "离开这里",
    "flee": "快逃",
    "follow": "跟着走",
    "hide": "躲起来",
    "look": "四下看看",
    "listen": "仔细听听",
    "search": "找一找",
    "say": "开口说话",
    "give": "把东西交出来",
    "show": "把东西拿出来看看",
    "help": "帮一把",
    "release": "放人",
    "attack": "动手",
    "teach": "传授本事",
    "study": "学本事",
    "wait": "等一等",
    "rest": "歇一歇",
    "struggle": "挣脱",
    "setpiece": "做一件事",
}


def ask_phrase(ask: dict, speaker: str | None = None) -> str:
    """所求之事的一句中文说法，实体写成 ``⟦id⟧``；对象是说话的人自己时写「自己」。"""
    from tianlong.kernel import actions, growth

    verb = str(ask.get("verb") or "")
    params = ask.get("params") or {}

    def ent(key: str) -> str | None:
        v = params.get(key)
        if not isinstance(v, str) or not v:
            return None
        if speaker is not None and v == speaker:
            return "自己"
        if key == "art" and v == growth.LITERACY:
            return "识字"
        return refs.ref(v)

    fields = {k: ent(k) for k in ("target", "item", "art", "exit")}
    fields["obj"] = fields["target"] or fields["item"]
    tpl = _ASK_PHRASES.get(verb)
    if tpl is not None:
        need = [k for k in fields if "{" + k + "}" in tpl]
        if all(fields[k] for k in need):
            return tpl.format(**{k: fields[k] for k in need})
    if verb == "say" and isinstance(params.get("about"), str) and params["about"]:
        return f"说一说{refs.ref(params['about'])}的事"
    if verb in _ASK_PLAIN:
        return _ASK_PLAIN[verb]
    spec = actions.VERBS.get(verb)
    return spec.label if spec is not None else "做一件事"


def _fact_text(sp: Speech, speaker: str, gist: str) -> str:
    to = "、".join(refs.ref(t) for t in sp.to)
    phrase = ACT_PHRASE.get(sp.act, "说")
    if sp.act == "greet":
        return f"⟦{speaker}⟧向{to}{phrase}" + (f"：{gist}" if gist else "")
    return f"⟦{speaker}⟧对{to}{phrase}" + (f"：{gist}" if gist else "")


def process(
    state: WorldState,
    defs: Defs,
    speaker_id: str,
    speech: Speech,
    *,
    source: str,
    step: str | None = None,
) -> list[Event]:
    """校验并结算一句话：先言语行为，再信念更新（6.6、6.7）。不合规的部分去掉并记一笔。"""
    sp = speech.model_copy(deep=True)
    c = state.chars[speaker_id]
    acts = available_acts(state, defs, c)
    if sp.act in ("agree", "bargain") and sp.act not in acts and "refuse" in acts:
        # 裁决不允许的答复：改判为拒绝，并记一次违规（6.6）
        log_violation(state, f"{speaker_id} chose {sp.act}, not allowed by the verdict → refuse")
        sp.act = "refuse"
    if sp.act not in acts:
        log_violation(state, f"speech dropped: {speaker_id} cannot {sp.act}")
        return []
    hearers = {o.id for o in audience(state, c)}
    if sp.to:
        to = [t for t in sp.to if t in hearers]
        if not to:
            log_violation(state, f"speech dropped: {speaker_id} -> {sp.to} not here")
            return []
        sp.to = to
    else:
        sp.to = sorted(hearers)
    # 事实：说者自己相信、不触底线；lie 不引事实
    if sp.act == "lie":
        sp.fact_refs = []
    else:
        keep = []
        for f in sp.fact_refs:
            b = c.beliefs.get(f)
            if (
                b is None
                or b.confidence < predicates.BELIEVE
                or say_blocked(state, defs, c, sp.act, fact=f, to=sp.to)
            ):
                log_violation(state, f"fact {f} stripped from {speaker_id}")
                continue
            keep.append(f)
        sp.fact_refs = keep
    if say_blocked(state, defs, c, sp.act, to=sp.to):
        log_violation(state, f"speech dropped: bottom line of {speaker_id}")
        return []
    if has_status(c, "hidden") and not sp.whisper:
        c.statuses = [s for s in c.statuses if s.kind != "hidden"]
    target = state.chars[sp.to[0]]
    kinds: list[dict] = [events.kind("talk")]
    nag = False
    req: Request | None = None
    reply_events: list[Event] = []
    # 请求类：先建请求，事件里带上它的 id
    if sp.act in REQUEST_ACTS:
        if sp.act == "plead" and not can_plead_to(state, c, target):
            log_violation(state, f"plead dropped: {target.id} is hostile to nobody here and holds no one")
            return []
        if sp.act == "order" and target.superior != c.id:
            log_violation(state, f"order dropped: {target.id} does not obey {speaker_id}")
            return []
        if not sp.ask_action:
            log_violation(state, f"{sp.act} without ask_action from {speaker_id}")
        else:
            req, nag = make_request(state, defs, c, target, sp, sp.act)
            sp.request = req.id
            if nag:
                kinds.append(events.kind("nag", who=target.id, by=c.id))
    elif sp.act == "ask":
        req = Request(
            id=next_id(state, "R"),
            frm=c.id,
            to=target.id,
            action={"verb": "say", "params": {"act": "tell", "about": sp.about}},
            action_class="trivial",
            verdict="may",
            kind="question",
            created=state.tick,
        )
        state.requests[req.id] = req
        sp.request = req.id
    elif sp.act in ("agree", "refuse", "bargain") or (sp.act in ("tell", "lie") and sp.request):
        ok = _reply(state, defs, c, sp, reply_events)
        if not ok and sp.act in ("agree", "refuse", "bargain"):
            return []
    if sp.act in ("insult", "taunt"):
        kinds += [events.kind("insult", who=t, by=c.id) for t in sp.to]
    elif sp.act == "threaten":
        kinds += [events.kind("threaten", who=t, by=c.id) for t in sp.to]
    elif sp.act == "comfort":
        kinds += [events.kind("comfort", who=t, by=c.id) for t in sp.to]
    elif sp.act == "promise":
        kinds += [events.kind("promise", who=t, by=c.id) for t in sp.to]
    # 专名：自报家门；叫得出对方名字的直呼其名
    names = list(sp.names)
    if sp.act == "greet" and c.id not in names:
        names.append(c.id)
    for t in sp.to:
        if t in c.rel and t not in names and speaker_id != state.player:
            names.append(t)
    sp.names = names
    gist = gist_for(sp, speaker_id, state)
    sp.gist = gist
    params: dict[str, Any] = {"act": sp.act, "to": list(sp.to)}
    if sp.request:
        params["request"] = sp.request
    if sp.act == "lie":
        params["lie"] = True
    overheard = None
    if sp.whisper:
        overheard = f"⟦{speaker_id}⟧凑近" + "、".join(refs.ref(t) for t in sp.to) + "低声说了几句"
    ev = events.emit(
        state,
        defs,
        actor=speaker_id,
        verb="say",
        text=_fact_text(sp, speaker_id, gist),
        params=params,
        speech=sp,
        source=source,
        step=step,
        kinds=kinds,
        overheard=overheard,
    )
    out = [ev] + reply_events
    # 之后：信念、示警、问候、许诺
    listeners = [state.chars[w] for w, how in ev.witnesses.items() if how == "saw" and w != speaker_id]
    if sp.whisper:
        listeners = [x for x in listeners if x.id in sp.to]
    extra: list[dict] = []
    judgements: dict[str, list[str]] = {}
    # 断言：不论哪种言语行为，话里夹带的断言都按 6.7 判（K16）。告知、示警没写 claims 时，
    # 所引的事实各算一条为真的断言；其余行为只判明写出来的 claims（请求里夹带的话）。
    claims = [] if sp.act == "lie" else list(sp.claims)
    if not claims and sp.act in ("tell", "warn"):
        claims = [_claim(state, f) for f in sp.fact_refs]
    if claims:
        beneficial = sp.act in REQUEST_ACTS or any(
            r.frm == c.id and r.to in sp.to and r.status == "open" for r in state.requests.values()
        )
        shown = bool(sp.evidence)
        for li in listeners:
            for cl in claims:
                before = li.beliefs.get(cl.ref or "")
                j = beliefs.hear_claim(state, defs, li, speaker_id, cl, shown=shown, beneficial=beneficial)
                judgements.setdefault(li.id, []).append(j.text)
                after = li.beliefs.get(cl.ref or "")
                if (
                    after is not None
                    and after.confidence >= predicates.BELIEVE
                    and (before is None or before.confidence < predicates.BELIEVE)
                ):
                    extra.append(events.kind("learn_key", who=li.id))
                if j.verdict == "false_self" and j.label == "disbelieve":
                    extra.append(events.kind("exposed", who=speaker_id, by=li.id))
    elif sp.act == "lie":
        for li in listeners:
            judgements.setdefault(li.id, []).append(beliefs.judge_text("doubt", unsourced=True))
    if sp.act == "warn":
        for t in sp.to:
            tc = state.chars[t]
            tc.statuses = [s for s in tc.statuses if s.kind != "alerted"]
            tc.statuses.append(Status(kind="alerted", until=state.tick + ALERT_TICKS, source=speaker_id))
    elif sp.act == "greet":
        for t in sp.to:
            if speaker_id not in state.chars[t].rel:
                mood.adj_rel(state, t, speaker_id, "affinity", 1)
    elif sp.act == "promise":
        for t in sp.to:
            pid = next_id(state, "P")
            state.promises[pid] = Promise(
                id=pid,
                frm=speaker_id,
                to=t,
                text=gist,
                do=sp.promise_do,
                deadline=sp.deadline
                if sp.deadline is not None
                else state.tick + PROMISE_DAYS * TICKS_PER_DAY,
            )
    if judgements:
        ev.params["judgements"] = judgements
    if extra:
        events.add_kinds(state, defs, ev, extra)
    return out


def _claim(state: WorldState, fid: str) -> Any:
    from tianlong.kernel.state import Claim

    return Claim(text=state.facts[fid].text if fid in state.facts else fid, ref=fid, verdict="true")


def _reply(state: WorldState, defs: Defs, c: Character, sp: Speech, out: list[Event]) -> bool:
    """答复一个请求：答应、拒绝、还价；或者接受、拒绝对方开的条件；或者回答一个问题。"""
    r = state.requests.get(sp.request or "")
    if r is None:
        # 只说「好」「不」而没指明是哪个请求：取冲自己来的第一个
        mine = open_requests_to(state, c.id) or countered_from(state, c.id)
        r = mine[0] if mine else None
        if r is None:
            log_violation(state, f"reply from {c.id} to no request")
            return False
        sp.request = r.id
    if r.to == c.id and r.status in ("open", "countered"):
        if r.kind == "question":
            if sp.act in ("tell", "lie"):
                r.status = "agreed"
            elif sp.act == "refuse":
                r.status = "refused"
            return True
        if sp.act not in allowed_replies(r) or (r.status == "countered" and sp.act != "refuse"):
            log_violation(state, f"{c.id} answered {r.id} with {sp.act}; verdict {r.verdict} → refuse")
            sp.act = "refuse"
        if sp.act == "agree":
            accept(state, defs, r)
        elif sp.act == "refuse":
            r.status = "refused"
        elif sp.act == "bargain":
            cond = sp.condition
            if not cond or not isinstance(cond, dict) or "verb" not in cond:
                opts = counter_options(state, defs, r)
                cond = opts[0] if opts else None
            if cond is None:
                log_violation(state, f"bargain from {c.id} without a condition")
                return False
            r.status = "countered"
            r.condition = cond
            sp.condition = cond
        return True
    if r.frm == c.id and r.status == "countered":
        if sp.act == "agree":
            if not fulfil_condition(state, defs, r, out):
                log_violation(state, f"{c.id} could not meet the condition of {r.id}")
                return False
            accept(state, defs, r)
        elif sp.act == "refuse":
            r.status = "refused"
        else:
            return False
        return True
    log_violation(state, f"{c.id} cannot answer {r.id}")
    return False


def fulfil_condition(state: WorldState, defs: Defs, r: Request, out: list[Event]) -> bool:
    """请求者接受还价：照条件做那件事（东西在对方手里的，先由对方递过来）。"""
    from tianlong.kernel import actions, resolve

    cond = r.condition or {}
    verb = cond.get("verb")
    params = dict(cond.get("params") or {})
    item = params.get("item")
    if verb == "use" and item and state.items.get(item) is not None and state.items[item].holder == r.to:
        out.extend(
            effects.apply_effects(
                state, defs, [{"give": [r.to, r.frm, item]}], Ctx(self_id=r.to), source="rule"
            )
        )
    act = actions.build(state, defs, r.frm, str(verb), params)
    if act is None:
        return False
    out.extend(resolve.resolve_action(state, defs, act, source="rule"))
    return True


def promise_kept(
    state: WorldState, defs: Defs, actor: str, action: Any, speech: Speech | None, ev: Event | None
) -> None:
    """许诺的人做了许下的事：守诺（5.5：对方 trust +15）。"""
    from tianlong.kernel import actions

    for p in state.promises.values():
        if p.status != "open" or p.frm != actor or not p.do:
            continue
        if actions.matches_do(action, p.do, Ctx(self_id=actor), state=state, speech=speech):
            p.status = "kept"
            if ev is not None:
                events.add_kinds(state, defs, ev, [events.kind("promise_kept", who=p.to, by=actor)])
            else:
                mood.adj_rel(state, p.to, actor, "trust", 15)


def condition_met(state: WorldState, defs: Defs, actor: str, action: Any) -> None:
    """请求者直接做了对方开的条件：请求转为答应。"""
    from tianlong.kernel import actions

    for r in list(state.requests.values()):
        if r.frm != actor or r.status != "countered" or not r.condition:
            continue
        cond = {"verb": r.condition.get("verb")}
        cond.update(r.condition.get("params") or {})
        if actions.matches_do(action, cond, Ctx(self_id=actor), state=state):
            accept(state, defs, r)
