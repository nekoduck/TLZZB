"""人物开口：J2b 的问题、把答案拼成 ``Speech``、C2 的 speech 的校验（方案 6.6、7.3、7.6）。

角色的话只有要旨（``gist``），按模板拼出，台词由 C3 写。内核在 ``resolve.apply`` 里还会再校验一遍
（答复与裁决不符改判为拒绝、不可说的事实删去、撞底线的话去掉）；这里先按同样的规矩筛一遍，
免得把注定作废的话送进内核。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import yaml

from tianlong import prompting
from tianlong.kernel import danger, perception, predicates
from tianlong.kernel import speech as kspeech
from tianlong.kernel.actions import VERBS
from tianlong.kernel.defs import Defs
from tianlong.kernel.speech import RequestView, SpeechOptions
from tianlong.kernel.state import Character, Event, Speech, WorldState, has_status

STANCES = ("plain", "warm", "cold", "angry", "pleading", "mocking", "evasive")
STANCE_SUFFIX = {
    "warm": "语气热络",
    "cold": "语气冷淡",
    "angry": "语气恼怒",
    "pleading": "语气恳求",
    "mocking": "语带讥讽",
    "evasive": "说得支支吾吾",
}
REPLY_ACTS = ("agree", "refuse", "bargain")
ASK_ACTS = ("request", "order", "plead")  # 要带 ask_action 的言语行为
STAR_ONLY = ("request", "order", "promise")  # 没有 ★ 的那一步时不给模型选：所求、所许之事无从说起
MAX_TELL = 2  # 过了门槛的取概率最高的两条（7.3）
GIST_MAX = 80


@lru_cache(maxsize=4)
def questions(name: str) -> dict[str, Any]:
    """prompts/jev/<name>.md 里的固定问法（YAML）。"""
    data = yaml.safe_load(prompting.load(f"jev/{name}")) or {}
    return dict(data)


def labels(kind: str) -> dict[str, str]:
    return dict(questions("j2b_questions").get(kind) or {})


# ---------------------------------------------------------------- describing actions


def describe_do(do: dict) -> str:
    """把一个动作模式（{verb, target, item, …}）说成一句中文，实体用 ⟦id⟧。"""
    verb = do.get("verb")
    t, item = do.get("target"), do.get("item")
    tr = f"⟦{t}⟧" if isinstance(t, str) and t else "对方"
    ir = f"⟦{item}⟧" if isinstance(item, str) and item else "东西"
    if verb == "give":
        return f"把{ir}交给{tr}"
    if verb == "show":
        return f"把{ir}拿出来看看"
    if verb == "release":
        return f"放了{tr}"
    if verb == "restrain":
        return f"把{tr}拿下"
    if verb == "attack":
        return f"对{tr}下杀手" if do.get("intent") == "kill" else f"对{tr}动手"
    if verb == "follow":
        return f"跟着{tr}走"
    if verb == "help":
        return f"照料{tr}"
    if verb == "block":
        return f"挡住{tr}"
    if verb == "push":
        return f"推开{tr}"
    if verb == "teach":
        art = do.get("art")
        return f"传授⟦{art}⟧" if art and art != "literacy" else "教人识字"
    if verb == "use":
        if isinstance(t, str) and t:
            return f"给{tr}用{ir}"
        return f"服下{ir}"
    if verb in ("go", "flee"):
        ex = do.get("exit") or do.get("to")
        return f"往⟦{ex}⟧去" if ex else "离开这里"
    if verb == "say":
        about = do.get("about")
        return f"说说⟦{about}⟧的事" if about else "说几句话"
    if verb == "wait" or verb is None:
        return "罢手"
    spec = VERBS.get(str(verb))
    label = spec.label if spec is not None else str(verb)
    return f"{label}{tr}" if isinstance(t, str) and t else label


# ---------------------------------------------------------------- who hears, what can be said


def hearers(state: WorldState, c: Character) -> list[str]:
    return [o.id for o in kspeech.audience(state, c)]


def model_acts(state: WorldState, defs: Defs, c: Character, opts: SpeechOptions) -> list[str]:
    """交给模型选的言语行为：此刻可用、而且拼得出一句话的。"""
    star_act = (opts.star or {}).get("act")
    question = any(v.request.kind == "question" and v.request.to == c.id for v in opts.requests)
    out: list[str] = []
    for a in opts.acts:
        if a in STAR_ONLY and a != star_act:
            continue
        if a in REPLY_ACTS and not opts.requests:
            continue
        if a == "lie" and not question:
            continue
        if a == "tell" and not opts.facts and not question and star_act != "tell":
            continue
        out.append(a)
    if opts.requests:  # 有请求冲着来：答复排在前面
        replies = [a for a in out if a in REPLY_ACTS]
        out = replies + [a for a in out if a not in REPLY_ACTS]
    return out


def primary_view(opts: SpeechOptions, events: Sequence[Event] = ()) -> RequestView | None:
    """要答复的那个请求：本轮里刚提的优先，否则取最新的一个。"""
    if not opts.requests:
        return None
    fresh = {str(ev.params.get("request")) for ev in events if ev.params.get("request")}
    for v in reversed(opts.requests):
        if v.request.id in fresh:
            return v
    return max(opts.requests, key=lambda v: (v.request.created, v.request.id))


def relevant_fact(state: WorldState, c: Character, opts: SpeechOptions, about: Any) -> str | None:
    """回答问题时最贴题的一条可说的事实。"""
    if not isinstance(about, str) or not about:
        return None
    for fid in opts.facts:
        f = state.facts.get(fid)
        if f is not None and f"⟦{about}⟧" in f.text:
            return fid
    return None


def preferred_reply(state: WorldState, c: Character, v: RequestView, opts: SpeechOptions) -> str:
    """规则里的答复：按裁决取内核允许的答复里最顺的一个（Mock 的提示、规则兜底都用它）。"""
    allowed = v.allowed_replies
    r = v.request
    if r.frm == c.id:  # 对方开了条件
        return "agree" if "agree" in allowed else allowed[0]
    if r.kind == "question":
        about = (r.action.get("params") or {}).get("about")
        return "tell" if relevant_fact(state, c, opts, about) and "tell" in allowed else "refuse"
    for pick in ("agree", "bargain", "refuse"):
        if pick in allowed:
            return pick
    return allowed[0] if allowed else "refuse"


def derive_ask(state: WorldState, c: Character, act: str, to: list[str]) -> dict | None:
    """request / order / plead 所求之事（方案没给角色列候选，这里按规则取一件说得通的事）。"""
    held = has_status(c, "bound") or has_status(c, "captive")
    if act == "plead":
        if held:
            return {"verb": "release", "params": {"target": c.id}}
        return {"verb": "wait", "params": {}}
    if act == "request":
        if held:
            return {"verb": "release", "params": {"target": c.id}}
        st = danger.current_step(c)
        item = st.do.get("item") if st is not None and st.do.get("verb") == "say" else None
        if isinstance(item, str) and item in state.items:
            return {"verb": "give", "params": {"item": item, "target": c.id}}
        return {"verb": "wait", "params": {}}
    if act == "order":
        for o in perception.visible_people(state, c):
            if o.id not in to and danger.is_hostile(state, c, o.id):
                return {"verb": "restrain", "params": {"target": o.id}}
        return {"verb": "follow", "params": {"target": c.id}}
    return None


def star_text(state: WorldState, defs: Defs, c: Character) -> str | None:
    st = danger.current_step(c)
    if st is None or st.do.get("verb") != "say":
        return None
    return st.text.strip().rstrip("。") or None


# ---------------------------------------------------------------- gist


def make_gist(
    state: WorldState,
    speaker: Character,
    sp: Speech,
    *,
    view: RequestView | None = None,
    star: str | None = None,
) -> str:
    """要旨（第三人称的一句话）。内核把它接在「某人对某人说/拒绝了/……：」后面。"""
    act = sp.act
    r = view.request if view is not None else None
    if star and act in ("request", "order", "promise", "ask", "plead", "threaten"):
        base = star
    elif act in ("tell", "warn") and sp.fact_refs:
        base = "；".join(state.facts[f].text.rstrip("。") for f in sp.fact_refs if f in state.facts)
    elif act == "tell":
        base = "说自己知道的不多" if r is not None and r.kind == "question" else "说起眼前的事"
    elif act == "warn":
        base = "要对方当心"
    elif act == "greet":
        base = ""  # 内核写「自称某某」
    elif act == "ask":
        base = f"问⟦{sp.about}⟧是什么来历" if sp.about else "打听近来的事"
    elif act == "request":
        base = "请对方" + describe_do(flat(sp.ask_action))
    elif act == "order":
        base = "命对方" + describe_do(flat(sp.ask_action))
    elif act == "plead":
        ask = flat(sp.ask_action)
        base = "求对方放了自己" if ask.get("verb") == "release" else "求对方手下留情"
    elif act == "agree":
        base = ("答应" + describe_do(flat(r.condition if r.frm == speaker.id else r.action))) if r else "答应了"
    elif act == "refuse":
        if r is not None and r.kind == "question":
            base = "不肯回答"
        elif r is not None and r.frm == speaker.id:
            base = "不肯答应对方开的条件"
        else:
            base = ("不肯" + describe_do(flat(r.action))) if r else "一口回绝"
    elif act == "bargain":
        base = "要对方先" + describe_do(flat(sp.condition)) if sp.condition else "要对方先拿出点诚意"
    elif act == "promise":
        base = "许诺会" + describe_do(sp.promise_do) if sp.promise_do else "许下一个诺言"
    elif act == "threaten":
        base = "要对方识相些，否则不客气"
    elif act == "comfort":
        base = "宽慰对方几句"
    elif act == "insult":
        base = "骂对方不知好歹"
    elif act == "taunt":
        base = "出言讥讽，激对方动手"
    elif act == "lie":
        base = "编了一套说辞搪塞"
    else:
        base = ""
    suffix = STANCE_SUFFIX.get(sp.stance, "")
    if base and suffix:
        return f"{base}，{suffix}"
    return base or suffix


def flat(do: dict | None) -> dict:
    """{verb, params: {...}} → {verb, ...}（describe_do 吃的形状）。"""
    if not do:
        return {"verb": None}
    out = {"verb": do.get("verb")}
    out.update(do.get("params") or {})
    return out


# ---------------------------------------------------------------- building & validating


def compose(
    state: WorldState,
    defs: Defs,
    c: Character,
    opts: SpeechOptions,
    *,
    act: str,
    to: str | None = None,
    stance: str = "plain",
    facts: Sequence[str] = (),
    replies: dict[str, str] | None = None,
    conds: dict[str, dict] | None = None,
    whisper: bool = False,
    events: Sequence[Event] = (),
    gist: str | None = None,
    request_ref: str | None = None,
) -> Speech | None:
    """把一组选择拼成一句合规的话；拼不成返回 None（不开口）。"""
    allowed = model_acts(state, defs, c, opts)
    views = {v.request.id: v for v in opts.requests}
    replies = replies or {}
    conds = conds or {}
    heard = hearers(state, c)
    if not heard:
        return None
    view: RequestView | None = None
    bind = act in REPLY_ACTS or (act in ("tell", "lie", "refuse") and request_ref in views)
    if not bind and act in ("tell", "lie"):
        pv = primary_view(opts, events)
        bind = pv is not None and pv.request.kind == "question" and pv.request.to == c.id
    if bind:
        view = views.get(request_ref or "") or primary_view(opts, events)
        if view is None:
            return None
        ok = [a for a in view.allowed_replies if a in opts.acts]
        reply = replies.get(view.request.id)
        if reply not in ok:
            reply = act if act in ok else None
        if reply is None:
            # 答复与裁决不符：改判为拒绝（6.6、7.6）
            if "refuse" not in opts.acts:
                return None
            reply = "refuse"
        act = reply
        r = view.request
        target = [r.to if r.frm == c.id else r.frm]
    else:
        if act not in allowed:
            return None
        target = [to] if to and to in heard else []
    if act not in opts.acts:
        return None
    sp = Speech(act=act, to=[t for t in target if t in heard] or [], stance=stance if stance in STANCES else "plain")
    if view is not None:
        sp.request = view.request.id
        if act == "bargain":
            cond = conds.get(view.request.id)
            if cond is None and view.counter_options:
                cond = view.counter_options[0]
            if cond is None:
                return None
            sp.condition = cond
        if act == "tell":
            about = (view.request.action.get("params") or {}).get("about")
            fs = [f for f in facts if f in opts.facts]
            best = relevant_fact(state, c, opts, about)
            sp.fact_refs = fs[:MAX_TELL] or ([best] if best else [])
    elif act in ("tell", "warn"):
        sp.fact_refs = [f for f in facts if f in opts.facts][:MAX_TELL]
    if act == "order" and not sp.to:  # 命令只对听命于自己的人有效
        sp.to = [t for t in heard if state.chars[t].superior == c.id][:1]
    if act in ASK_ACTS:
        sp.ask_action = derive_ask(state, c, act, sp.to)
        if sp.ask_action is None:
            return None
    if act == "ask" and sp.to and not _knows(c, sp.to[0]):
        sp.about = sp.to[0]
    if act == "promise":
        st = danger.current_step(c)
        sp.promise_do = None if st is None else dict(st.do)
    sp.whisper = bool(whisper and sp.to)
    star = star_text(state, defs, c) if (opts.star or {}).get("act") == act else None
    sp.gist = (gist or "").strip()[:GIST_MAX] or make_gist(state, c, sp, view=view, star=star)
    return validate(state, defs, c, sp, opts)


def _knows(c: Character, other: str) -> bool:
    return other in c.rel or c.superior == other


def validate(state: WorldState, defs: Defs, c: Character, sp: Speech, opts: SpeechOptions) -> Speech | None:
    """方案 7.6 的校验表（J2b 拼出的话、C2 的 speech 同样过一遍）。"""
    if sp.act not in opts.acts:
        return None
    heard = set(hearers(state, c))
    sp.to = [t for t in sp.to if t in heard]
    if sp.act in REPLY_ACTS and not sp.request:
        return None
    if sp.act == "lie":
        sp.fact_refs = []
    else:
        sp.fact_refs = [
            f
            for f in dict.fromkeys(sp.fact_refs)
            if f in opts.facts and not kspeech.say_blocked(state, defs, c, sp.act, fact=f, to=sp.to or None)
        ]
    if kspeech.say_blocked(state, defs, c, sp.act, to=sp.to or None):
        return None
    if sp.act == "plead" and not any(danger.is_hostile(state, state.chars[t], c.id) for t in (sp.to or heard)):
        return None
    if sp.act == "order" and sp.to and state.chars[sp.to[0]].superior != c.id:
        return None
    return sp


def star_speech(
    state: WorldState, defs: Defs, c: Character, opts: SpeechOptions, events: Sequence[Event] = ()
) -> Speech | None:
    """规则里的话：当前这一步打算是开口（★），就照它说。"""
    star = opts.star
    if not star:
        return None
    act = str(star.get("act", "tell"))
    to = star.get("to")
    fact = star.get("fact")
    believed = []
    if isinstance(fact, str):
        b = c.beliefs.get(fact)
        if b is not None and b.confidence >= predicates.BELIEVE:
            believed = [fact]
    opts2 = opts.model_copy(update={"facts": list(dict.fromkeys(list(opts.facts) + believed))})
    return compose(
        state,
        defs,
        c,
        opts2,
        act=act,
        to=to if isinstance(to, str) else None,
        facts=believed,
        events=events,
    )


def from_out(
    state: WorldState,
    defs: Defs,
    c: Character,
    out: Any,
    opts: SpeechOptions,
    events: Sequence[Event] = (),
) -> Speech | None:
    """C2 的 ``SpeechOut`` → ``Speech``，按 7.6 的表校验。"""
    if out is None:
        return None
    act = str(out.act or "")
    allowed = model_acts(state, defs, c, opts)
    if act not in allowed and not (act in ("agree", "bargain") and "refuse" in opts.acts):
        return None  # speech.act 不在可用的言语行为里：去掉这句话
    views = {v.request.id: v for v in opts.requests}
    ref = out.request_ref if out.request_ref in views else None
    to = next((t for t in out.to if t in hearers(state, c)), None)
    return compose(
        state,
        defs,
        c,
        opts,
        act=act,
        to=to,
        stance=str(out.stance or "plain"),
        facts=list(out.fact_refs or []),
        whisper=bool(out.whisper),
        events=events,
        gist=str(out.gist or ""),
        request_ref=ref,
    )


__all__ = [
    "compose",
    "validate",
    "star_speech",
    "from_out",
    "model_acts",
    "primary_view",
    "preferred_reply",
    "describe_do",
    "make_gist",
    "labels",
    "questions",
    "STANCES",
]
