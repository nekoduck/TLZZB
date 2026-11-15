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
from tianlong.kernel import actions, perception, timeflow
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


def _ask_people(state: WorldState, defs: Defs, clean: str, menu: Menu) -> list[str]:
    """候选请求按谁列：猜得出说话对象就只列他；否则按在场者的分量列几个人。"""
    narr, spoken = lx.split_speech(clean)
    to = rules.addressee(state, defs, clean, narr, spoken or clean, menu)
    if to:
        return to
    hearers = [o.ref for o in menu.people]

    def weight(cid: str) -> tuple[int, int]:
        c = state.chars[cid]
        subs = sum(1 for o in state.chars.values() if o.superior == cid and o.loc == c.loc)
        tier = {"soul": 2, "named": 1}.get(c.tier, 0)
        mentioned = 0 if lx.match_score(state, defs, cid, clean) >= 2 else 1
        return (-(3 * subs + tier), mentioned)

    return sorted(hearers, key=weight)[:MAX_ASK_PEOPLE]


def build_menu(state: WorldState, defs: Defs, clean: str) -> Menu:
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
        who = _ask_people(state, defs, clean, menu)
        menu.addressee = who[0] if len(who) == 1 else None
        pool: list[tuple[int, int, int, Any]] = []
        for pi, cid in enumerate(who):
            for ci, a in enumerate(request_candidates(state, defs, state.player, cid, text=clean)):
                pool.append((-ask_score(state, defs, a, clean, state.player), ci, pi, a))
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


def scene_text(state: WorldState, defs: Defs, menu: Menu, *, words_text: str | None, recent: list[str] | None
               ) -> str:
    me = state.chars[state.player]
    people = []
    for o in scene_people(state, me):
        note = status_note(o)
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
    obj = {o.key: o.desc for o in menu.objects}
    q["target"] = {"type": "choice", "instructions": w["target"], "criteria": {**obj, "none": w["target.none"]}}
    q["target2"] = {"type": "choice", "instructions": w["target2"], "criteria": {**obj, "none": w["target2.none"]}}
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
        if menu.asks:
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
        "target": "none" if g.verb == "setpiece" else _key_of_obj(menu, g.target),
        "target2": _key_of_obj(menu, g.target2),
    }
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


def intent_from_jev(answers: dict[str, Any], menu: Menu, th: Any, attempts: list[str]) -> Intent:
    """J1 的答案 → Intent（实体 id 形式）。J1 取概率最高的一项，不取样（D）。"""
    yes = float(th.noul_yes)
    kind_ans = _choice(answers.get("kind")) or "say"
    has_sp = _yes(answers.get("has_speech"), yes)
    readable = _yes(answers.get("meaningful"), yes)
    verb_key = _choice(answers.get("verb"))
    vopt = menu.verb(verb_key) if verb_key and verb_key != "none" else None
    verb = target = target2 = None
    if vopt is not None:
        if vopt.kind == "setpiece":
            verb, target = "setpiece", vopt.ref
        else:
            verb = vopt.ref
            t1 = menu.obj(_choice(answers.get("target")))
            t2 = menu.obj(_choice(answers.get("target2")))
            target = t1.ref if t1 else None
            target2 = t2.ref if t2 and (t1 is None or t2.ref != t1.ref) else None
    has_act = kind_ans in ("act", "both") and verb is not None
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
    else:
        kind = "unclear"
    act = to_key = None
    to: list[str] = []
    asks = cls = None
    urgent = False
    claim_refs: list[str] = []
    if kind in ("say", "both"):
        act = _choice(answers.get("act"))
        to_key = _choice(answers.get("to"))
        p = menu.person(to_key) if to_key and to_key != "all" else None
        to = [p.ref] if p else []
        ask_ps = [(a.key, float(answers[f"ask_{a.key}"].get("noul", 0.0))) for a in menu.asks
                  if f"ask_{a.key}" in answers]
        ask_ps = [kp for kp in ask_ps if kp[1] > yes]
        if ask_ps:
            asks = max(ask_ps, key=lambda kp: kp[1])[0]
            cls = _choice(answers.get("cls"))
            urgent = _yes(answers.get("urgent"), yes)
        cl = [(c.ref, float(answers[f"claim_{c.key}"].get("noul", 0.0))) for c in menu.claims
              if f"claim_{c.key}" in answers]
        claim_refs += [r for r, p in sorted(cl, key=lambda rp: -rp[1]) if p > yes][:3]
        sl = [(s.ref, float(answers[f"self_{s.key}"].get("noul", 0.0))) for s in menu.selfs
              if f"self_{s.key}" in answers]
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
        cls=cls,
        urgent=urgent,
        claim_refs=claim_refs,
        attempts=list(attempts),
        dropped="",
        note=replies.UNCLEAR if kind == "unclear" else "",
    )


def jev_unsure(answers: dict[str, Any], intent: Intent, th: Any) -> bool:
    """4.3：kind、verb、target、act 里该看的任何一项没把握；或两道是非题落在中间。"""
    keys = ["kind"]
    if intent.kind in ("act", "both"):
        keys.append("verb")
        if intent.verb in rules.TARGET_KINDS:
            keys.append("target")
    if intent.kind in ("say", "both") and "act" in answers:
        keys.append("act")
    present = {k: answers[k] for k in keys if k in answers}
    if route.should_escalate(present, th, keys=keys):
        return True
    for k in ("has_speech", "meaningful"):
        a = answers.get(k)
        if a is not None:
            p = float(a.get("noul", 0.0))
            if NOUL_FLOOR < p < float(th.noul_yes):
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
    asks = intent.asks if menu.ask(intent.asks) is not None else None
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
    menu = build_menu(state, defs, clean)
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
                intent = intent_from_jev(answers, menu, th, attempts)
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
