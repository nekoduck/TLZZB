"""三选项（方案 2.3、8.4）：规则分 + J4 分量，每个方向留分最高的一个，凑出三个方向不同的选项。

1. ``kernel.actions.legal_actions`` 列出玩家此刻的合法动作；去掉标为 lethal 的、与上回合选中的相同的、
   对着玩家看不见的东西的、描述会露出玩家不知道的专名的、通往图外的。
2. 另加「开口」的候选（``storyteller/speaking.py``）：招呼、打听、告知与示警、相求、求情、安慰、答复，以及
   劝眼前情境里的人换个做法。每个是一句内核的 ``Speech``，配 1 刻的 ``wait``（与解析器处理只说话的输入相同）。
3. 按计分表给每个候选一个方向与规则分；近三回合选过同一动词与对象的扣 15。
4. 规则分前 20（每个方向至少留一个）交 J4，一次请求每个候选一个 score 问题；分 = 规则分 + 10 × 分量。
   J4 失败、或路由为 rules_only / claude_only（J4 没有慢路），分量一律记 1。
5. 每个方向留分最高的一个；险境（danger ≥ 2）必有「自保」，眼前有插得上手的事必有「介入」；
   至少一个与牵挂挂钩；凑不出三个方向时放宽为「动词与对象不同」，用 look 与 wait 补位。

game 结算玩家选中的选项：``resolve.apply(state, defs, option.action, option.speech, source="player")``。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

from tianlong import prompting
from tianlong.kernel import actions, danger, perception
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Speech, WorldState, has_status, injury_level
from tianlong.llm import LLM, LLMError
from tianlong.storyteller import common, speaking, threads

TAGS = ("推进", "人物", "探索", "自保", "介入", "成长")
TAG_PRIORITY = ("介入", "自保", "推进", "人物", "成长", "探索")  # 同分时取前面的方向
TOP_N = 20
RECENT_TURNS = 3
WANT = 3

INTERVENE_VERBS = ("block", "push", "give", "show", "help", "release", "use", "steal")
PERSON_VERBS = ("examine", "give", "show", "help", "follow", "push", "block")
VIOLENT_VERBS = ("attack", "restrain")
SAFE_VERBS = ("flee", "hide", "struggle")
VERB_TAG = {
    "go": "探索",
    "look": "探索",
    "listen": "探索",
    "examine": "探索",
    "search": "探索",
    "take": "探索",
    "flee": "自保",
    "hide": "自保",
    "wait": "自保",
    "rest": "自保",
    "struggle": "自保",
    "drop": "自保",
    "use": "自保",
    "follow": "人物",
    "give": "人物",
    "show": "人物",
    "help": "人物",
    "push": "人物",
    "block": "人物",
    "release": "介入",
    "steal": "介入",
    "restrain": "介入",
    "attack": "介入",
    "study": "成长",
    "practice": "成长",
    "teach": "成长",
    "setpiece": "介入",
}


class Option(BaseModel):
    """一个选项：一个身体动作；或者一句话（``speech``），那时 ``action`` 是 1 刻的 ``wait``。

    ``id``、``signature``、``desc`` 不给时照身体动作的填（开口的选项由 ``candidates`` 填好）：
    ``id`` 在这一组候选里唯一（J4、C4 用它对号）；``signature`` 用来去重和记「上回合选过」；
    ``desc`` 是一句中性的意图，实体写成 ⟦id⟧。
    """

    action: ActionInst
    tag: str  # 方向：推进、人物、探索、自保、介入、成长
    score: float  # 规则分 + 10 × J4 分量
    rule: float  # 规则分
    thread_ids: list[str] = []  # 这个动作推进的牵挂
    speech: Speech | None = None  # 开口的选项：结算时与 action 一并交给 resolve.apply
    id: str = ""
    signature: str = ""
    desc: str = ""

    @model_validator(mode="after")
    def _fill(self) -> Option:
        if not self.id:
            self.id = self.action.id
        if not self.signature:
            self.signature = (
                speaking.speech_signature(self.speech) if self.speech is not None else self.action.signature
            )
        if not self.desc:
            self.desc = speaking.plain_desc(self.speech) if self.speech is not None else self.action.desc
        return self

    def to_pending(self) -> dict[str, Any]:
        """存进 state.pending_options 的样子（game 用）：带着 speech，``Option.model_validate`` 可以读回来。"""
        return self.model_dump()


def object_of(a: ActionInst) -> str:
    p = a.params
    for k in ("target", "item", "feature", "exit", "art", "situation"):
        if k in p:
            return f"{k}={p[k]}"
    return ""


def pair_of(o: Option) -> tuple[str, str]:
    """（动词，对象）：开口的选项按言语行为与说话的对象算。"""
    if o.speech is not None:
        return speaking.speech_pair(o.speech)
    return o.action.verb, object_of(o.action)


# ---------------------------------------------------------------- 计分


class _Scene:
    """一回合里计分要用的东西，先算好。"""

    def __init__(self, state: WorldState, defs: Defs) -> None:
        self.state = state
        self.defs = defs
        self.p = state.chars[state.player]
        self.danger = danger.danger(state, defs)
        self.matcher = threads.Matcher(state, defs)
        self.weights = {t.id: t.weight for t in self.matcher.threads}
        self.involved = {
            cid
            for s in state.situations.values()
            if s.loc == self.p.loc and state.player not in s.roles.values()
            for cid in s.roles.values()
        }
        self.my_situations = {
            s.id for s in state.situations.values() if s.loc == self.p.loc and state.player in s.roles.values()
        }
        lc = common.teller(state, "last_chosen")
        self.last_sig = lc.get("sig") if isinstance(lc, dict) and lc.get("turn") == state.turn else None
        self.recent = {
            (r[1], r[2]) for r in common.teller(state, "recent_chosen", []) if r[0] > state.turn - RECENT_TURNS
        }
        present_last = common.teller(state, "present_last")
        self.present_last = set(present_last) if isinstance(present_last, list) else None
        self.names = common.unknown_names(state, defs)
        self.hostile_to_me = {h.id for h in danger.hostiles_toward(state, state.player)}

    def talk_open(self, cid: str) -> bool:
        """与此人有未了的话头：没答复的请求、他还价的条件、刚到场。"""
        me = self.state.player
        for r in self.state.requests.values():
            if r.frm == cid and r.to == me and r.status == "open":
                return True
            if r.frm == me and r.to == cid and r.status == "countered":
                return True
        return self.present_last is not None and cid not in self.present_last


def _combine(contrib: dict[str, float]) -> tuple[str, float]:
    """命中计分表几行时：方向取分最高的一行（同分按 TAG_PRIORITY），规则分 = 最高一行 + 其余各行的四分之一。"""
    tag = max(contrib, key=lambda k: (contrib[k], -TAG_PRIORITY.index(k)))
    rest = sum(v for k, v in contrib.items() if k != tag)
    return tag, contrib[tag] + 0.25 * rest


def _direction(sc: _Scene, a: ActionInst) -> tuple[str, float, list[str]]:
    """查计分表：返回（方向，规则分，推进的牵挂）。"""
    state = sc.state
    p = a.params
    contrib: dict[str, float] = {}

    def add(tag: str, v: float) -> None:
        contrib[tag] = max(contrib.get(tag, 0.0), v)

    # 介入：对象是进行中情境里的人，这个动作会改变他们的处境
    if "intervene" in a.tags and a.verb in INTERVENE_VERBS:
        add("介入", 50)
    if a.verb == "setpiece" and p.get("situation") in sc.my_situations:
        add("介入", 50)
    # 推进一条牵挂
    tied = sc.matcher.tied(a)
    if tied:
        add("推进", 10 * max(t.weight for t in tied))
    # 自保
    if sc.danger >= 2 and (a.verb in SAFE_VERBS or "reply" in a.tags):
        add("自保", 35)
    me = sc.p
    if a.verb == "use" and "target" not in p and has_status(me, "hungry"):
        it = state.items.get(p.get("item", ""))
        if it is not None and "food" in it.tags:
            add("自保", 20)
    if a.verb == "rest" and (has_status(me, "exhausted") or injury_level(me) >= 1):
        add("自保", 20)
    if a.verb == "struggle":
        add("自保", 20)  # 被捆着、被押着：挣脱总比干等要紧
    # 人物
    target = p.get("target")
    t = state.chars.get(target) if isinstance(target, str) else None
    if t is not None and t.tier in ("soul", "named") and a.verb in PERSON_VERBS and perception.on_map(t):
        if a.verb == "examine":
            if f"char:{t.id}" not in state.examined:
                add("人物", 30)
        elif a.verb == "help" or sc.talk_open(t.id):
            add("人物", 30)
    # 成长
    if a.verb == "study":
        add("成长", 40)
    elif a.verb in ("practice", "teach"):
        add("成长", 25)
    # 探索
    if a.verb in ("examine", "search") and "feature" in p:
        if f"feature:{me.loc}#{p['feature']}" not in state.examined:
            add("探索", 20)
    elif a.verb == "examine" and "item" in p:
        if f"item:{p['item']}" not in state.examined:
            add("探索", 20)
    elif a.verb == "go" and p.get("exit") not in state.visited:
        add("探索", 20)
    elif a.verb == "take":
        add("探索", 20)
    if contrib:
        tag, rule = _combine(contrib)
    else:
        tag = VERB_TAG.get(a.verb, "探索")
        if a.verb in ("examine", "use") and target is not None:
            tag = "人物"
        rule = 0.0
    # 扣分
    if a.verb in VIOLENT_VERBS and target not in sc.hostile_to_me:
        rule -= 20
    if a.verb == "drop":
        rule -= 10
    if (a.verb, object_of(a)) in sc.recent:
        rule -= 15
    return tag, rule, [t.id for t in tied]


def _visible_enough(sc: _Scene, a: ActionInst) -> bool:
    """只对玩家看得见、知道在哪的东西出选项。"""
    state = sc.state
    if a.verb == "steal" and not common.item_visible(state, sc.defs, a.params.get("item", "")):
        return False
    if a.verb == "go":
        loc = state.locations.get(sc.p.loc)
        ex = next((e for e in loc.exits if e.to == a.params.get("exit")), None) if loc else None
        if ex is not None and ex.edge:
            return False  # 走出地图即收笔：只经自由输入（2.6 会先问是否收笔）
    if common.unresolved_refs(state, sc.defs, a.desc):
        return False
    return not common.reveals_unknown_name(state, sc.defs, a.desc, sc.names)


def _speech_options(sc: _Scene, wait: ActionInst) -> list[Option]:
    """开口的候选：一句 Speech 配 1 刻的 wait；方向与规则分照同一张计分表。"""
    out: list[Option] = []
    cands = speaking.speech_candidates(
        sc.state,
        sc.defs,
        threads=sc.matcher.threads,
        danger_level=sc.danger,
        present_last=sc.present_last,
        names=sc.names,
    )
    for i, c in enumerate(cands, 1):
        sig = speaking.speech_signature(c.speech)
        if sig == sc.last_sig:
            continue
        tag, rule = _combine(c.contrib) if c.contrib else ("人物", 0.0)
        if speaking.speech_pair(c.speech) in sc.recent:
            rule -= 15
        out.append(
            Option(
                action=wait,
                speech=c.speech,
                id=f"s{i:02d}",
                signature=sig,
                desc=c.desc,
                tag=tag,
                score=rule,
                rule=rule,
                thread_ids=list(c.thread_ids),
            )
        )
    return out


def candidates(state: WorldState, defs: Defs) -> tuple[_Scene, list[Option]]:
    """过滤后的候选，带方向与规则分（不问 J4）。开口的在前：同分时先取开口的（「人物」的头一样是交谈）。"""
    sc = _Scene(state, defs)
    out: list[Option] = []
    legal = actions.legal_actions(state, defs, state.player)
    for a in legal:
        if a.lethal or a.signature == sc.last_sig:
            continue
        if not _visible_enough(sc, a):
            continue
        tag, rule, tids = _direction(sc, a)
        out.append(Option(action=a, tag=tag, score=rule, rule=rule, thread_ids=tids))
    # 只说话、不动：动作取 1 刻的 wait（与解析器相同）
    wait = next((a for a in legal if a.verb == "wait" and a.params.get("ticks") == 1), None)
    if wait is None:
        wait = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    if wait is not None:
        out = _speech_options(sc, wait) + out
    return sc, out


def _top(cands: list[Option]) -> list[Option]:
    """规则分前 20；每个方向分最高的先留下，免得必选的方向进不了 J4。"""
    ranked = sorted(cands, key=lambda o: -o.rule)
    keep: list[Option] = []
    seen_tags: set[str] = set()
    for o in ranked:
        if o.tag not in seen_tags:
            seen_tags.add(o.tag)
            keep.append(o)
    for o in ranked:
        if len(keep) >= TOP_N:
            break
        if o not in keep:
            keep.append(o)
    return keep


# ---------------------------------------------------------------- J4


def _hint_level(sc: _Scene, o: Option) -> float:
    """给 Mock 的规则提示（真 Jev 不看）：与 J4 的四级同一口径。"""
    if (o.tag == "介入" and o.rule >= 50) or (o.tag == "自保" and sc.danger >= 2):
        return 3
    if o.thread_ids:
        return 2
    if o.rule >= 20:
        return 1
    return 0


def j4_state(state: WorldState, defs: Defs) -> str:
    p = state.chars[state.player]
    me = state.player
    recent = [
        common.pv(state, defs, ev.fact_text)
        for ev in state.events_tail
        if ev.witnesses.get(me) == "saw" and ev.outcome != "blocked"
    ][-6:]
    present = [common.pv(state, defs, f"⟦{cid}⟧") for cid in common.visible_people_ids(state)]
    body = perception.body_sensations(state, p)
    d = danger.danger(state, defs)
    return prompting.render(
        "jev/j4_state",
        clock=common.clock(state.tick),
        place=common.pv(state, defs, f"⟦{p.loc}⟧"),
        tense=state.scene_mode == "tense",
        danger=d,
        present=present,
        recent=recent,
        body=body,
        threads=[common.pv(state, defs, t.text) for t in threads.open_threads(state)],
    )


async def _j4_weights(llm: LLM, state: WorldState, defs: Defs, sc: _Scene, top: list[Option]) -> dict[str, float]:
    """J4：每个候选一个 score（0 到 3）。失败或不走 Jev 时返回空表（一律按 1 记）。"""
    if not top or llm.mode("options") in ("rules_only", "claude_only"):
        return {}
    levels = [ln.strip() for ln in prompting.load("jev/j4_levels").splitlines() if ln.strip()]
    questions: dict[str, dict] = {}
    hints: dict[str, float] = {}
    for o in top:
        qid = f"w_{o.id}"
        questions[qid] = {
            "type": "score",
            "instructions": prompting.render("jev/j4_question", desc=common.pv(state, defs, o.desc)),
            "criteria": levels,
        }
        hints[qid] = _hint_level(sc, o)
    try:
        answers = await llm.jev.ask("J4", j4_state(state, defs), questions, hints=hints)
    except LLMError:
        return {}
    out: dict[str, float] = {}
    top_level = float(len(levels) - 1)
    for o in top:
        a = answers.get(f"w_{o.id}")
        if a is None:
            continue
        try:
            out[o.id] = max(0.0, min(top_level, float(a.get("score", 1.0))))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- 挑选


def _key(o: Option) -> tuple[float, float]:
    return (-o.score, -o.rule)


def affectable_event(sc: _Scene, cands: list[Option]) -> bool:
    """眼前有玩家插得上手的事：同一地点有进行中的情境，且候选里有能改变它的动作。"""
    return any(o.tag == "介入" and o.rule >= 50 for o in cands)


def _choose(sc: _Scene, scored: list[Option], pool: list[Option]) -> list[Option]:
    best: dict[str, Option] = {}
    for o in sorted(scored, key=_key):
        best.setdefault(o.tag, o)
    must: list[str] = []
    if sc.danger >= 2:
        must.append("自保")
    if affectable_event(sc, scored):
        must.append("介入")
    chosen = [best[t] for t in must if t in best]
    must_ids = {id(o) for o in chosen}
    for o in sorted(best.values(), key=_key):
        if len(chosen) >= WANT:
            break
        if o not in chosen:
            chosen.append(o)
    # 至少一个与牵挂挂钩：换掉分最低的（不动必选的，方向仍两两不同）
    if not any(o.thread_ids for o in chosen):
        tied = sorted((o for o in pool if o.thread_ids and o not in chosen), key=_key)
        if tied and len(chosen) < WANT:
            tags = {o.tag for o in chosen}
            pick = next((o for o in tied if o.tag not in tags), None)
            if pick is not None:
                chosen.append(pick)
        elif tied:
            for victim in sorted((o for o in chosen if id(o) not in must_ids), key=_key, reverse=True):
                others = {o.tag for o in chosen if o is not victim}
                pick = next((o for o in tied if o.tag not in others), None)
                if pick is not None:
                    chosen[chosen.index(victim)] = pick
                    break
    # 凑不出三个方向：放宽为「动词与对象不同」，用 look 与 wait 补位
    if len(chosen) < WANT:
        pairs = {pair_of(o) for o in chosen}
        plain = [o for o in pool if o.speech is None and o.action.verb in ("look", "wait")]
        fill = plain + sorted((o for o in pool if o not in plain), key=_key)
        for o in fill:
            if len(chosen) >= WANT:
                break
            pair = pair_of(o)
            if o not in chosen and pair not in pairs:
                chosen.append(o)
                pairs.add(pair)
        for o in sorted(pool, key=_key):
            if len(chosen) >= WANT:
                break
            if o not in chosen:
                chosen.append(o)
    return sorted(chosen, key=_key)


async def pick_options(llm: LLM, state: WorldState, defs: Defs) -> list[Option]:
    """三个选项（合法动作不足三个时有几个给几个）。只经 teller 效果记下给出了哪几个。"""
    p = state.chars.get(state.player)
    if p is None or not perception.on_map(p) or state.ended:
        return []
    sc, cands = candidates(state, defs)
    if not cands:
        return []
    top = _top(cands)
    weights = await _j4_weights(llm, state, defs, sc, top)
    for o in cands:
        o.score = o.rule + 10 * weights.get(o.id, 1.0)
    chosen = _choose(sc, top, cands)
    common.book(
        state,
        defs,
        common.teller_effects(
            {
                "offered": [{"sig": o.signature, "tag": o.tag, "score": round(o.score, 2)} for o in chosen],
                "present_last": common.visible_people_ids(state),
            }
        ),
    )
    return chosen


def _note(state: WorldState, defs: Defs, verb: str, obj: str, sig: str) -> None:
    recent = [r for r in common.teller(state, "recent_chosen", []) if isinstance(r, list) and len(r) == 4]
    recent.append([state.turn, verb, obj, sig])
    common.book(
        state,
        defs,
        common.teller_effects(
            {
                "last_chosen": {"sig": sig, "turn": state.turn},
                "recent_chosen": recent[-RECENT_TURNS:],
            }
        ),
    )


def note_action(state: WorldState, defs: Defs, action: ActionInst, speech: Speech | None = None) -> None:
    """记下玩家本回合做了哪个动作：下回合不再给同一个，近三回合同一动词与对象扣分。

    自由输入只说了一句话（动作是 1 刻的 wait）时把 ``speech`` 一并传来，记的就是那句话。
    """
    if speech is not None and action.verb == "wait":
        verb, obj = speaking.speech_pair(speech)
        _note(state, defs, verb, obj, speaking.speech_signature(speech))
    else:
        _note(state, defs, action.verb, object_of(action), action.signature)


def note_choice(state: WorldState, defs: Defs, option: Option) -> None:
    """玩家选了某个选项（game 在结算玩家动作后、出下一组选项前调用）。开口的选项记的是那句话。"""
    verb, obj = pair_of(option)
    _note(state, defs, verb, obj, option.signature)
