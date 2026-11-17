"""自由输入的归类（方案 4.2 第 1 步、4.3、7.3 J1、7.5 C1）。

J1 一个请求问齐 7.3 的问题（D-014：「有没有说出口的话」「读不读得出意思」另问是非）；
kind、verb、target、act 里该看的任何一项没把握，或路由要求，整句改交 C1；
都失败就回一句戏内的话（kind = unclear），不耗时。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel

from tianlong import prompting
from tianlong.kernel import actions, perception, speech, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.llm import LLMError, route
from tianlong.llm.schemas import Intent
from tianlong.parser import lexical as lx
from tianlong.parser import replies, rules
from tianlong.parser.candidates import (
    CLASS_ORDER,
    MAX_ASKS,
    Menu,
    act_options,
    ask_score,
    claim_candidates,
    model_text,
    people_options,
    pov,
    request_candidates,
    scene_objects,
    scene_people,
    self_event_candidates,
    status_note,
    verb_options,
)
from tianlong.parser.stage import strip_stage

Via = Literal["jev", "claude", "rule"]
KINDS_J1 = ("act", "say", "both", "meta")
NOUL_FLOOR = 0.4  # 是非题落在 (0.4, 0.6) 之间算没把握
MAX_ASK_PEOPLE = 4
KIND_SURE = 0.9  # kind 一题的把握到这里，「有没有说出口的话」以它为准
SPEECH_CLEAR = 0.75  # kind 很有把握地说只是动作时，has_speech 要到这里才算另有一句话
SPEECH_NONE = 0.25  # kind 很有把握地说在说话时，has_speech 低到这里才算没有话
ONE_OBJECT = ("examine", "push")  # 只取一个对象的动词：物与人两题里取把握大的那个
FEW_HEARERS = 2  # 「对众人说」而听得见的不超过这么多人：算作对这几个人说
ASK_TIE = 0.15  # 两条候选请求的概率差不到这么多：算咬得很紧，用 cls 一题来分


class ParseResult(BaseModel):
    intent: Intent
    clean: str
    attempts: list[str] = []
    via: Via = "rule"
    absurd: bool = False
    menu: Menu = Menu()
    stance: str = "plain"
    whisper: bool = False
    escalated: bool = False
    errors: list[str] = []


# ---------------------------------------------------------------- wording


@lru_cache(maxsize=4)
def _words_cached(root: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in prompting.load("jev/j1_questions").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.]+)\s*[：:]\s*(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def words() -> dict[str, str]:
    """J1 的固定问法（prompts/jev/j1_questions.md）。"""
    return _words_cached(str(prompting.root()))


# ---------------------------------------------------------------- menu


def _ask_people(state: WorldState, defs: Defs, clean: str, menu: Menu, attempts: list[str] | None = None
                ) -> list[str]:
    """候选请求按谁列：从称呼认得出说话对象就只列他；否则在场听得见的人都算上，按分量排先后。"""
    narr, spoken = lx.split_speech(clean)
    to, strength = rules.addressee_guess(state, defs, clean, narr, spoken or clean, menu, attempts)
    menu.guess_to, menu.guess_strength = (to[0], strength) if to else (None, 0)
    if to and strength >= rules.LIKELY:
        return to
    hearers = [o.ref for o in menu.people]

    def weight(cid: str) -> tuple[int, int]:
        c = state.chars[cid]
        subs = sum(1 for o in state.chars.values() if o.superior == cid and o.loc == c.loc)
        tier = {"soul": 2, "named": 1}.get(c.tier, 0)
        mentioned = 0 if lx.match_score(state, defs, cid, clean) >= 2 else 1
        return (-(3 * subs + tier), mentioned)

    ranked = sorted(hearers, key=weight)
    if to:  # 只是猜的（冲着「你」说、刚打过交道）：把他排在最前，别人也列上
        ranked = [to[0]] + [h for h in ranked if h != to[0]]
    return ranked


def build_menu(state: WorldState, defs: Defs, clean: str, attempts: list[str] | None = None) -> Menu:
    """J1 与 C1 共用的候选。"""
    legal = actions.legal_actions(state, defs, state.player)
    w = words()
    menu = Menu(
        verbs=verb_options(state, defs, legal, w),
        objects=scene_objects(state, defs, legal),
        acts=act_options(state, defs, w),
        people=people_options(state, defs),
    )
    if menu.acts and menu.people:
        who = _ask_people(state, defs, clean, menu, attempts)
        menu.addressee = who[0] if len(who) == 1 else None
        pool: list[tuple[int, int, int, Any]] = []
        for pi, cid in enumerate(who):
            for ci, a in enumerate(request_candidates(state, defs, state.player, cid, text=clean)):
                score = ask_score(state, defs, a, clean, state.player)
                if pi >= MAX_ASK_PEOPLE and score <= 0:
                    continue  # 排在后面的人：只列话里对得上的事
                pool.append((-score, ci, pi, a))
        pool.sort(key=lambda t: t[:3])
        asks = [t[3] for t in pool[:MAX_ASKS]]
        for i, a in enumerate(asks, 1):
            a.key = f"k{i}"
        menu.asks = asks
        topic = [eid for eid, _ in lx.match_entities(state, defs, clean, defs.lexicon.names.keys(), floor=3)]
        topic += [eid for eid, _ in lx.match_entities(state, defs, clean, defs.lexicon.terms.keys(), floor=3)]
        menu.claims = claim_candidates(state, defs, state.player, topic=topic)
        menu.selfs = self_event_candidates(state, defs, state.player)
    return menu


# ---------------------------------------------------------------- state text


def recent_text(state: WorldState, defs: Defs, recent: list[str] | None) -> str:
    if recent is not None:
        return "；".join(r for r in recent[-2:] if r)
    me = state.player
    out: list[str] = []
    for ev in reversed(state.events_tail):
        if len(out) >= 2:
            break
        if ev.actor == me and ev.source == "player" and ev.verb != "warn_danger":
            out.append(pov(state, defs, ev.fact_text))
    return "；".join(reversed(out))


SEEN_SKIP = ("wait", "look", "listen", "warn_danger", "rest")


def seen_text(state: WorldState, defs: Defs) -> str:
    """玩家眼前刚发生的两件事（别人做的、他看见的）：话里的「你」「他」指谁，常要靠它才对得上。"""
    me = state.player
    out: list[str] = []
    for ev in reversed(state.events_tail[-40:]):
        if len(out) >= 2:
            break
        if ev.actor in (None, me) or ev.witnesses.get(me) != "saw" or ev.verb in SEEN_SKIP:
            continue
        text = pov(state, defs, ev.fact_text)
        if text and text not in out:
            out.append(text)
    return "；".join(reversed(out))


def pending_text(state: WorldState, defs: Defs) -> str:
    """别人向玩家提的、还没了结的要求，和对方开了条件的事：答应、拒绝、还价这几种话要靠它才认得出。"""
    me = state.player
    out: list[str] = []
    for r in speech.open_requests_to(state, me)[-3:]:
        who = pov(state, defs, f"⟦{r.frm}⟧")
        if r.kind == "question":
            out.append(f"{who}问了你一句话，等你回答")
        else:
            out.append(f"{who}要你{pov(state, defs, speech.asked_text(r.action, r.frm))}")
    for r in speech.countered_from(state, me)[-2:]:
        who = pov(state, defs, f"⟦{r.to}⟧")
        cond = pov(state, defs, speech.asked_text(r.condition, r.to)) if r.condition else ""
        out.append(f"你求{who}的事，{who}开了条件" + (f"：要你{cond}" if cond else ""))
    return "；".join(out)


def relation_note(state: WorldState, me: Any, o: Any) -> str:
    """在场的人与玩家、与别的在场者的上下关系（「东家」「帮主」这类称呼靠它对上人）。"""
    notes = []
    if me.superior == o.id:
        notes.append("你听他的吩咐")
    elif o.superior == me.id:
        notes.append("听你的吩咐")
    subs = sum(1 for c in state.chars.values() if c.superior == o.id and c.loc == o.loc and c.id != me.id)
    if subs:
        notes.append(f"在场有{subs}个人听他的")
    return "，".join(notes)


def scene_text(state: WorldState, defs: Defs, menu: Menu, *, words_text: str | None, recent: list[str] | None
               ) -> str:
    me = state.chars[state.player]
    people = []
    for o in scene_people(state, me):
        note = "，".join(n for n in (status_note(o), relation_note(state, me, o)) if n)
        people.append(pov(state, defs, f"⟦{o.id}⟧") + (f"（{note}）" if note else ""))
    things = [o.desc for o in menu.objects if o.kind in ("item", "feature") and "你身上" not in o.desc]
    exits = [o.desc for o in menu.objects if o.kind == "exit"]
    inv = [pov(state, defs, f"⟦{i}⟧") for i in me.inventory if i in state.items]
    return prompting.render(
        "jev/j1_state",
        clock=timeflow.clock_text(state.tick),
        place=pov(state, defs, f"⟦{me.loc}⟧"),
        mood="场面紧张。" if state.scene_mode == "tense" else "",
        people="｜".join(people),
        things="、".join(things),
        exits="、".join(exits),
        body="；".join(perception.body_sensations(state, me)),
        inventory="、".join(inv),
        recent=recent_text(state, defs, recent),
        seen=seen_text(state, defs),
        pending=pending_text(state, defs),
        words=words_text,
    )


def _ask_desc(state: WorldState, defs: Defs, menu: Menu, a: Any) -> str:
    text = model_text(state, defs, a.desc)
    if menu.addressee is None:
        text += f"（求的是{model_text(state, defs, f'⟦{a.to}⟧')}）"
    return text


def candidates_text(state: WorldState, defs: Defs, menu: Menu) -> str:
    """C1 的场景块里的候选。"""
    def opts(xs: list[Any]) -> list[dict[str, str]]:
        return [{"key": o.key, "desc": o.desc} for o in xs]

    return prompting.render(
        "jev/j1_candidates",
        verbs=opts(menu.verbs),
        objects=opts(menu.objects),
        acts=opts(menu.acts),
        people=opts(menu.people),
        asks=[{"key": a.key, "desc": _ask_desc(state, defs, menu, a)} for a in menu.asks],
        claims=[{"key": c.key, "desc": model_text(state, defs, c.text)} for c in menu.claims],
        selfs=[{"key": s.key, "desc": model_text(state, defs, s.text)} for s in menu.selfs],
    )


# ---------------------------------------------------------------- J1 questions


def j1_questions(state: WorldState, defs: Defs, menu: Menu) -> dict[str, dict[str, Any]]:
    w = words()
    q: dict[str, dict[str, Any]] = {}
    q["kind"] = {"type": "choice", "instructions": w["kind"], "criteria": {k: w[f"kind.{k}"] for k in KINDS_J1}}
    q["has_speech"] = {"type": "noul", "instructions": w["has_speech"]}
    q["meaningful"] = {"type": "noul", "instructions": w["meaningful"]}
    verb_crit = {o.key: o.desc for o in menu.verbs}
    verb_crit["none"] = w["verb.none"]
    q["verb"] = {"type": "choice", "instructions": w["verb"], "criteria": verb_crit}
    # 对象分两题问：target 只列物、出口、景物、本事；target2 只列人（两题合起来就是 7.3 的对象清单）
    things = {o.key: o.desc for o in menu.objects if o.kind != "person"}
    persons = {o.key: o.desc for o in menu.objects if o.kind == "person"}
    q["target"] = {"type": "choice", "instructions": w["target"], "criteria": {"none": w["target.none"], **things}}
    q["target2"] = {"type": "choice", "instructions": w["target2"], "criteria": {"none": w["target2.none"], **persons}}
    if menu.acts and menu.people:
        q["act"] = {"type": "choice", "instructions": w["act"], "criteria": {o.key: o.desc for o in menu.acts}}
        to = {o.key: o.desc for o in menu.people}
        to["all"] = w["to.all"]
        q["to"] = {"type": "choice", "instructions": w["to"], "criteria": to}
        for a in menu.asks:
            q[f"ask_{a.key}"] = {"type": "noul", "instructions": w["ask"].replace("{事}", _ask_desc(state, defs, menu, a))}
        for c in menu.claims:
            q[f"claim_{c.key}"] = {
                "type": "noul", "instructions": w["claim"].replace("{事}", model_text(state, defs, c.text))}
        for s in menu.selfs:
            q[f"self_{s.key}"] = {
                "type": "noul", "instructions": w["self"].replace("{事}", model_text(state, defs, s.text))}
        q["self_unlisted"] = {"type": "noul", "instructions": w["self_unlisted"]}
        # 所求之事不在候选里时也要问类别与急需（binding.other_request 用）
        q["cls"] = {"type": "choice", "instructions": w["cls"],
                    "criteria": {k: w.get(f"cls.{k}", k) for k in CLASS_ORDER}}
        q["urgent"] = {"type": "noul", "instructions": w["urgent"]}
    return q


def _key_of_verb(menu: Menu, g: rules.Guess) -> str:
    if g.verb is None:
        return "none"
    if g.verb == "setpiece":
        o = menu.verb(g.target)
        return o.key if o else "none"
    o = menu.verb(g.verb)
    return o.key if o else "none"


def _key_of_obj(menu: Menu, ref: str | None) -> str:
    o = menu.obj(ref)
    return o.key if o else "none"


def hints_for(menu: Menu, g: rules.Guess) -> dict[str, Any]:
    """规则猜测 → MockJev 的提示（真 Jev 不看）。"""
    h: dict[str, Any] = {
        "kind": g.kind if g.kind in KINDS_J1 else "say",
        "has_speech": 1.0 if g.has_speech else 0.0,
        "meaningful": 1.0 if g.meaningful and g.kind != "unclear" else 0.0,
        "verb": _key_of_verb(menu, g),
        "target": "none",
        "target2": "none",
    }
    if g.verb != "setpiece":
        for ref in (g.target2, g.target):  # 物答在 target，人答在 target2；主对象后写，同类时以它为准
            o = menu.obj(ref)
            if o is not None:
                h["target2" if o.kind == "person" else "target"] = o.key
    if menu.acts:
        if g.act:
            h["act"] = g.act
        p = menu.person(g.to[0]) if g.to else None
        h["to"] = p.key if p else "all"
        if g.ask:
            h[f"ask_{g.ask}"] = 1.0
        for k in g.claims:
            h[f"claim_{k}"] = 1.0
        for k in g.selfs:
            h["self_unlisted" if k == "self_unlisted" else f"self_{k}"] = 1.0
        if g.cls:
            h["cls"] = g.cls
        h["urgent"] = 1.0 if g.urgent else 0.0
    return h


def guess_intent(menu: Menu, g: rules.Guess, attempts: list[str]) -> Intent:
    """规则猜测 → C1 的 Mock 默认值（编号形式，和真 C1 的输出同构）。"""
    verb = None if g.verb is None else _key_of_verb(menu, g)
    if verb == "none":
        verb = g.verb if g.verb != "setpiece" else None
    target = None if g.verb == "setpiece" else (menu.obj(g.target).key if menu.obj(g.target) else g.target)
    claim_refs = list(g.claims) + list(g.selfs)
    to = [p.key for t in g.to if (p := menu.person(t)) is not None]
    kind = g.kind
    return Intent(
        kind=kind,  # type: ignore[arg-type]
        verb=verb,
        target=target,
        target2=menu.obj(g.target2).key if menu.obj(g.target2) else g.target2,
        act=g.act,
        to=to,
        asks=g.ask,
        cls=g.cls,
        urgent=g.urgent,
        claim_refs=claim_refs,
        attempts=list(attempts),
        dropped="",
        note=replies.UNCLEAR if kind == "unclear" else "",
    )


# ---------------------------------------------------------------- answers → intent


def _yes(a: Any, th: float) -> bool:
    try:
        return float(a.get("noul", 0.0)) > th
    except (AttributeError, TypeError, ValueError):
        return False


def _choice(a: Any) -> str | None:
    if a is None:
        return None
    try:
        return a.top if hasattr(a, "top") else str(a.get("choice"))
    except (AttributeError, TypeError):
        return None


def _conf(a: Any) -> float:
    try:
        return float(a.get("confidence", 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _noul(a: Any) -> float:
    try:
        return float(a.get("noul", 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _targets(answers: dict[str, Any], menu: Menu, verb: str, th: Any) -> tuple[str | None, str | None]:
    """物（target 一题）与人（target2 一题）按动词落成主对象、第二对象。

    动词不带的那类对象一律不取（四下看看、歇息、等候都不带对象）；只取一个对象的动词取把握大的那个；
    其余按动词的首选类别排先后，第二个对象要过 choice_confidence 才取。
    """
    kinds = rules.TARGET_KINDS.get(verb, ())
    if not kinds:
        return None, None
    picks: list[tuple[Any, float]] = []
    for q in ("target", "target2"):
        o = menu.obj(_choice(answers.get(q)))
        if o is not None and o.kind in kinds and all(o.ref != x.ref for x, _ in picks):
            picks.append((o, _conf(answers.get(q))))
    if not picks:
        return None, None
    if verb in ONE_OBJECT:
        picks = [max(picks, key=lambda oc: oc[1])]
    else:
        picks.sort(key=lambda oc: kinds.index(oc[0].kind))
        if len(picks) > 1 and picks[1][1] < float(th.choice_confidence):
            picks = picks[:1]
    return picks[0][0].ref, (picks[1][0].ref if len(picks) > 1 else None)


def _spoke(answers: dict[str, Any], kind_ans: str, th: Any) -> bool:
    """有没有说出口的话：kind 一题很有把握时以它为准，是非题要答得明确才改得动它（D-014 的落地）。"""
    p = _noul(answers.get("has_speech"))
    if _conf(answers.get("kind")) >= KIND_SURE:
        if kind_ans == "act":
            return p >= SPEECH_CLEAR
        if kind_ans in ("say", "both"):
            return p > SPEECH_NONE
    return p > float(th.noul_yes)


def _addressee(answers: dict[str, Any], menu: Menu, th: Any) -> list[str]:
    """那句话对谁说：J1 的答案与规则的猜测合起来定。

    - 话里叫了名字称呼（CALLED）：J1 要很有把握（不低于 KIND_SURE）才改得动它。
    - J1 没把握：听规则的（哪怕只是按「冲着谁来」「刚跟谁打过交道」猜的）。
    - 「对众人」而听得见的只有一两个人：就是对这一两个人说。
    """
    a = answers.get("to")
    key = _choice(a)
    p = menu.person(key) if key and key != "all" else None
    sure = a is not None and not route.choice_unsure(a, th)
    guess = menu.guess_to if menu.guess_to is not None and menu.person(menu.guess_to) is not None else None
    if guess is not None:
        if menu.guess_strength >= rules.CALLED and not (sure and _conf(a) >= KIND_SURE):
            return [guess]
        if not sure:
            return [guess]
    if p is None and key == "all" and len(menu.people) <= FEW_HEARERS:
        return [o.ref for o in menu.people]
    return [p.ref] if p else []


def _pick_ask(ask_ps: list[tuple[str, float]], answers: dict[str, Any], menu: Menu, th: Any) -> str:
    """过线的候选里取概率最高的；有几条咬得很紧（差不到 ASK_TIE）时，取类别与 J1 的 cls 一题相符的那条。

    「把金盒给我看看」对「给你」「拿给你看看」两条都会答是：J1 自己说这是举手之劳，就取「看看」。
    """
    top = max(p for _, p in ask_ps)
    close = [(k, p) for k, p in ask_ps if p >= top - ASK_TIE]
    cls_ans = answers.get("cls")
    if len(close) > 1 and cls_ans is not None and not route.choice_unsure(cls_ans, th):
        want = _choice(cls_ans)
        same = [(k, p) for k, p in close if (c := menu.ask(k)) is not None and c.cls == want]
        if same:
            close = same
    return max(close, key=lambda kp: kp[1])[0]


def intent_from_jev(answers: dict[str, Any], menu: Menu, th: Any, attempts: list[str],
                    guess: rules.Guess | None = None) -> Intent:
    """J1 的答案 → Intent（实体 id 形式）。J1 取概率最高的一项，不取样（D）。

    ``guess``：规则猜测。只用来断一种两可的情形——整句就是一个动作词（「等一等」），J1 的 verb 一题也认定是它，
    kind 一题却没多大把握地答成「说话」：按动作算。
    """
    yes = float(th.noul_yes)
    kind_ans = _choice(answers.get("kind")) or "say"
    has_sp = _spoke(answers, kind_ans, th)
    readable = _yes(answers.get("meaningful"), yes)
    verb_key = _choice(answers.get("verb"))
    vopt = menu.verb(verb_key) if verb_key and verb_key != "none" else None
    verb = target = target2 = None
    if vopt is not None:
        if vopt.kind == "setpiece":
            verb, target = "setpiece", vopt.ref
        else:
            verb = vopt.ref
            target, target2 = _targets(answers, menu, verb, th)
    kind_conf = _conf(answers.get("kind"))
    bare_verb = (guess is not None and guess.kind == "act" and verb is not None and verb == guess.verb
                 and kind_conf < KIND_SURE and _conf(answers.get("verb")) >= KIND_SURE)
    if bare_verb and kind_ans != "meta":
        kind_ans, has_sp = "act", False
    # kind 没把握地答成「说话」、是非题却说没有话，而动词认出来了：当作动作
    has_act = verb is not None and (
        kind_ans in ("act", "both") or (kind_ans == "say" and not has_sp and kind_conf < KIND_SURE))
    if not readable:
        kind = "unclear"
    elif kind_ans == "meta":
        kind = "meta"
    elif has_act and has_sp:
        kind = "both"
    elif has_act:
        kind = "act"
    elif has_sp:
        kind = "say"
    elif kind_ans in ("act", "both"):
        kind = "impossible"  # 读得懂、是个动作，可动词表里没有这种做法：此刻做不到（不是「看不懂」）
    else:
        kind = "unclear"
    act = None
    to: list[str] = []
    asks = cls = None
    urgent = False
    claim_refs: list[str] = []
    if kind in ("say", "both"):
        act = _choice(answers.get("act"))
        to = _addressee(answers, menu, th)
        ask_ps = [(a.key, _noul(answers[f"ask_{a.key}"])) for a in menu.asks if f"ask_{a.key}" in answers]
        ask_ps = [kp for kp in ask_ps if kp[1] > yes]
        if ask_ps and act in rules.REQUESTISH:
            # 只有请求、求情、命令才带所求之事；询问（ask）由内核另挂一个待答的问题（6.6）
            asks = _pick_ask(ask_ps, answers, menu, th)
        if act in rules.REQUESTISH:
            # 所求之事不在候选里也留着类别与急需：模型只能把事判得更难（D-087），binding.other_request 要用
            cls = _choice(answers.get("cls"))
            urgent = _yes(answers.get("urgent"), yes)
        cl = [(c.ref, _noul(answers[f"claim_{c.key}"])) for c in menu.claims if f"claim_{c.key}" in answers]
        claim_refs += [r for r, p in sorted(cl, key=lambda rp: -rp[1]) if p > yes][:3]
        sl = [(s.ref, _noul(answers[f"self_{s.key}"])) for s in menu.selfs if f"self_{s.key}" in answers]
        selfs = [r for r, p in sorted(sl, key=lambda rp: -rp[1]) if p > yes][:2]
        if not selfs and _yes(answers.get("self_unlisted"), yes):
            selfs = ["self_unlisted"]
        claim_refs += selfs
    return Intent(
        kind=kind,  # type: ignore[arg-type]
        verb=verb if kind in ("act", "both") else None,
        target=target if kind in ("act", "both") else None,
        target2=target2 if kind in ("act", "both") else None,
        act=act,
        to=to,
        asks=asks,
        cls=cls if cls in CLASS_ORDER else None,
        urgent=urgent,
        claim_refs=claim_refs,
        attempts=list(attempts),
        dropped="",
        note=replies.UNCLEAR if kind == "unclear" else (replies.CANNOT if kind == "impossible" else ""),
    )


def target_question(answers: dict[str, Any], verb: str | None) -> str | None:
    """这个动词的对象看哪一题的把握：首选类别是人的看 target2，否则看 target；只取一个对象的看被取中的那题。"""
    kinds = rules.TARGET_KINDS.get(verb or "", ())
    if not kinds:
        return None
    if verb in ONE_OBJECT:
        got = [(q, _conf(answers.get(q))) for q in ("target", "target2")
               if _choice(answers.get(q)) not in (None, "none")]
        return max(got, key=lambda qc: qc[1])[0] if got else "target"
    return "target2" if kinds[0] == "person" else "target"


def gate_keys(answers: dict[str, Any], intent: Intent) -> list[str]:
    """4.3 升级时该看的 choice 题（D-085：只看与本句相关的）。"""
    keys = ["kind"]
    if intent.kind in ("act", "both"):
        keys.append("verb")
        tq = target_question(answers, intent.verb)
        if tq is not None:
            keys.append(tq)
    if intent.kind in ("say", "both") and "act" in answers:
        keys.append("act")
    return keys


def jev_unsure(answers: dict[str, Any], intent: Intent, th: Any) -> bool:
    """4.3：kind、verb、对象、act 里该看的任何一项没把握；或两道是非题落在中间。

    kind 一题很有把握时（不低于 KIND_SURE），has_speech 落在中间不算没把握：那时以 kind 为准。
    """
    keys = gate_keys(answers, intent)
    present = {k: answers[k] for k in keys if k in answers}
    if route.should_escalate(present, th, keys=keys):
        return True
    for k in ("has_speech", "meaningful"):
        a = answers.get(k)
        if a is None:
            continue
        if k == "has_speech" and _conf(answers.get("kind")) >= KIND_SURE:
            continue
        if NOUL_FLOOR < _noul(a) < float(th.noul_yes):
            return True
    return False


def normalize(intent: Intent, menu: Menu, state: WorldState, attempts: list[str]) -> Intent:
    """C1 的输出（编号或 id）→ 实体 id 形式；清单外的东西去掉。attempts 以正则剥下的为准。"""
    verb = target = target2 = None
    if intent.verb:
        vopt = menu.verb(intent.verb)
        if vopt is not None and vopt.kind == "setpiece":
            verb, target = "setpiece", vopt.ref
        elif vopt is not None:
            verb = vopt.ref
        elif intent.verb in actions.VERBS and intent.verb != "setpiece":
            verb = intent.verb

    def ent(x: str | None) -> str | None:
        if not x:
            return None
        o = menu.obj(x)
        if o is not None:
            return o.ref
        if x in state.chars or x in state.items or x in state.locations:
            return x
        return None

    if verb is not None and verb != "setpiece":
        target = ent(intent.target)
        target2 = ent(intent.target2)
        if target2 == target:
            target2 = None
    to = []
    for t in intent.to or []:
        p = menu.person(t)
        if p is not None and p.ref not in to:
            to.append(p.ref)
    claim_refs: list[str] = []
    for r in intent.claim_refs or []:
        c = menu.claim(r)
        s = menu.self_event(r)
        if r == "self_unlisted":
            claim_refs.append(r)
        elif c is not None:
            claim_refs.append(c.ref)
        elif s is not None:
            claim_refs.append(s.ref)
    asks = intent.asks if menu.ask(intent.asks) is not None and intent.act in rules.REQUESTISH else None
    kind = intent.kind
    if kind in ("act", "both") and verb is None:
        kind = "say" if kind == "both" else "unclear"
    return Intent(
        kind=kind,
        verb=verb if kind in ("act", "both") else None,
        target=target if kind in ("act", "both") else None,
        target2=target2 if kind in ("act", "both") else None,
        act=intent.act if kind in ("say", "both") else None,
        to=to,
        asks=asks if kind in ("say", "both") else None,
        cls=intent.cls if intent.cls in CLASS_ORDER else None,
        urgent=bool(intent.urgent),
        claim_refs=claim_refs if kind in ("say", "both") else [],
        attempts=list(attempts),
        dropped=intent.dropped or "",
        note=intent.note or "",
    )


def unclear_intent(attempts: list[str], note: str = "") -> Intent:
    return Intent(kind="unclear", verb=None, target=None, target2=None, act=None, to=[], asks=None, cls=None,
                  urgent=False, claim_refs=[], attempts=list(attempts), dropped="", note=note or replies.UNCLEAR)


# ---------------------------------------------------------------- entry point


async def classify(llm: Any, state: WorldState, defs: Defs, raw: str, *, recent: list[str] | None = None
                   ) -> ParseResult:
    """自由输入 → ParseResult。永不抛异常：模型失败按 4.3 退让，最后回一句戏内的话。"""
    clean, attempts = strip_stage(raw)
    try:
        return await _classify(llm, state, defs, clean, attempts, recent)
    except Exception as e:  # noqa: BLE001 — 游戏不因解析失败而中断（4.3）
        return ParseResult(intent=unclear_intent(attempts), clean=clean, attempts=attempts, via="rule",
                           errors=[f"{type(e).__name__}: {e}"])


async def _classify(llm: Any, state: WorldState, defs: Defs, clean: str, attempts: list[str],
                    recent: list[str] | None) -> ParseResult:
    if not clean:
        return ParseResult(intent=unclear_intent(attempts, replies.ONLY_STAGE), clean=clean, attempts=attempts)
    menu = build_menu(state, defs, clean, attempts)
    g = rules.guess(state, defs, clean, menu)
    extra = {"stance": g.stance, "whisper": bool(lx.WHISPER.search(clean))}
    if g.absurd:
        intent = unclear_intent(attempts, replies.ABSURD).model_copy(update={"kind": "impossible"})
        return ParseResult(intent=intent, clean=clean, attempts=attempts, via="rule", absurd=True, menu=menu, **extra)
    mode = llm.mode("classify")
    if mode == "rules_only":
        intent = normalize(guess_intent(menu, g, attempts), menu, state, attempts)
        return ParseResult(intent=intent, clean=clean, attempts=attempts, via="rule", menu=menu, **extra)
    th = llm.settings.jev.thresholds
    errors: list[str] = []
    order = {"jev_first": ["jev", "claude"], "claude_first": ["claude", "jev"], "claude_only": ["claude"]}[mode]
    held: tuple[Intent, Via] | None = None  # J1 答了但没把握，C1 又失败时用它
    for step in order:
        if step == "jev":
            try:
                answers = await llm.jev.ask(
                    "J1", scene_text(state, defs, menu, words_text=clean, recent=recent),
                    j1_questions(state, defs, menu), hints=hints_for(menu, g),
                )
                intent = intent_from_jev(answers, menu, th, attempts, g)
            except LLMError as e:
                errors.append(f"J1 {type(e).__name__}: {e}")
                continue
            if mode == "jev_first" and jev_unsure(answers, intent, th):
                held = (intent, "jev")
                continue
            return ParseResult(intent=intent, clean=clean, attempts=attempts, via="jev", menu=menu,
                               errors=errors, **extra)
        else:
            try:
                system = [prompting.load("c1_system"),
                          scene_text(state, defs, menu, words_text=None, recent=None) + "\n\n"
                          + candidates_text(state, defs, menu)]
                user = prompting.render("c1_user", words=clean, recent=recent_text(state, defs, recent))
                obj, _meta = await llm.claude.call_json(
                    "C1", system, user, Intent, mock_default=lambda: guess_intent(menu, g, attempts),
                    escalated=held is not None,
                )
                intent = normalize(obj, menu, state, attempts)
            except LLMError as e:
                errors.append(f"C1 {type(e).__name__}: {e}")
                continue
            return ParseResult(intent=intent, clean=clean, attempts=attempts, via="claude", menu=menu,
                               escalated=held is not None, errors=errors, **extra)
    if held is not None:
        return ParseResult(intent=held[0], clean=clean, attempts=attempts, via=held[1], menu=menu,
                           errors=errors, **extra)
    return ParseResult(intent=unclear_intent(attempts), clean=clean, attempts=attempts, via="rule", menu=menu,
                       errors=errors, **extra)
