"""人物的抉择（方案 4.2 第 3 步、4.3、7.3 的 J2、7.6 的 C2、7.12 的路由）。

一个人一次抉择：一个身体动作加至多一句话。

- 路人（tier extra）不调模型，按 ``routines.yaml`` 行动（``minds.routines``）。
- 有魂、有名的人按路由（``decide_soul`` / ``decide_named``）：
  - ``jev_first``：J2a 选动作、问开不开口、问拿不拿得定主意；没把握或这一刻要紧 → C2；
    否则在概率不低于 ``sample_floor`` 的选项里取样（随机数取自 ``kernel.rng``，可重放）。
    要开口再发 J2b，拼成一句 ``Speech``。
  - ``claude_first``：先 C2，失败再走 J2。``claude_only``：只走 C2。``rules_only``：只用规则。
- 失败兜底（4.3）：J2 失败 → C2；C2 也失败 → 取 ★ 动作（没有就 ``wait``），不开口
  （当前这一步打算本身是开口的，照那一步说）。升级之后 C2 失败、而 J2 已经答了的，用 J2 的答案。
  C2 答了清单外的动作 → 同样退到 ★ / ``wait``（7.6 的校验表）。

并行与可重放：同一微轮里各人的模型请求用 ``asyncio.gather`` 并行发出；取样（掷 ``kernel.rng``）
放在请求都回来以后、按人的先后逐个进行，所以响应到达的先后不影响结果。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from tianlong import prompting
from tianlong.kernel import actions, danger, perception, resolve, rng
from tianlong.kernel import speech as kspeech
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.speech import SpeechOptions
from tianlong.kernel.state import Character, Ctx, Event, Speech, WorldState, log_violation
from tianlong.llm import STAR_PREFIX, route
from tianlong.llm.schemas import Decision, SpeechOut
from tianlong.minds import render, talk
from tianlong.minds.types import MindDecision, idle_action

MAX_REACTORS = 8  # 每个微轮至多 8 人（4.3）；game 传 settings.limits.max_reactors
SALIENT = 6  # 见证了分量不低于 6 的事件，算与本轮有关
RECENT_SETPIECE = 16  # 刚做过的情境动作，16 刻之内不再优先
INNER_MAX = 40
DISPLAY_MAX = 12
NONE = "none"


# ---------------------------------------------------------------- who reacts


def involved(state: WorldState, events: Sequence[Event], loc: str) -> set[str]:
    """本轮的当事人：事件的动作者、对象、说话的对象、事件性质里的当事人；此地情境的角色；有请求冲着来的人。"""
    out: set[str] = set()
    for ev in events:
        if ev.loc != loc:
            continue
        if ev.actor:
            out.add(ev.actor)
        for k in ("target", "target2"):
            v = ev.params.get(k)
            if isinstance(v, str):
                out.add(v)
        if ev.speech is not None:
            out.update(ev.speech.to)
        for kd in ev.kinds:
            for k in ("who", "by"):
                v = kd.get(k)
                if isinstance(v, str):
                    out.add(v)
    for s in state.situations.values():
        if s.loc == loc:
            out.update(s.roles.values())
    for r in state.requests.values():
        if r.status == "open":
            out.add(r.to)
        elif r.status == "countered":
            out.add(r.frm)
    return out


def _star_rank(state: WorldState, defs: Defs, c: Character) -> int | None:
    """有 ★ 的事在这里：要走开、要做的不是「等」排 2；只是「在这里等」排 3。"""
    st = danger.current_step(c)
    if st is None or not actions.step_ready(state, defs, c, st):
        return None
    if st.at and st.at != c.loc:
        return 2
    return 2 if st.do.get("verb") != "wait" else 3


def pick_reactors(
    state: WorldState, defs: Defs, events: Sequence[Event] = (), *, limit: int = MAX_REACTORS
) -> list[str]:
    """与玩家同处一地、活着、清醒、与本轮有关的人；当事人在前，同级按 initiative 从高到低，至多 ``limit`` 人。

    有关 = 本轮事件的动作者、对象、说话对象；见证了分量不低于 6 的事件；有请求或问题冲着他来；
    他在这里有 ★ 的事（当前这一步打算，或此地情境里的角色）。
    """
    p = state.chars.get(state.player)
    if p is None or not perception.on_map(p):
        return []
    inv = involved(state, events, p.loc)
    rows: list[tuple[int, int, int, str]] = []
    for i, c in enumerate(perception.present(state, p.loc, exclude=p.id)):
        if not perception.conscious(c):
            continue
        rank: int | None
        if c.id in inv:
            rank = 0
        elif any(c.id in ev.witnesses and ev.salience >= SALIENT for ev in events):
            rank = 1
        else:
            rank = _star_rank(state, defs, c)
        if rank is None:
            continue
        rows.append((rank, -c.initiative, i, c.id))
    rows.sort()
    return [r[3] for r in rows[: max(0, limit)]]


def addressed(state: WorldState, events: Sequence[Event], loc: str) -> set[str]:
    """本轮事件里，话是冲着谁说的（被人当面相求、相问、相劝的人）。"""
    out: set[str] = set()
    for ev in events:
        if ev.loc == loc and ev.speech is not None:
            out.update(ev.speech.to)
    return out


def order(
    state: WorldState, decisions: Sequence[MindDecision], events: Sequence[Event] = ()
) -> list[MindDecision]:
    """结算顺序（4.3）：话冲着他说的人最先（先答话，别人再出手），其余当事人次之，旁观者最后；
    同级按 initiative 从高到低；再同级保持原先后。

    会让情境散场的那一招（``closing``）整体排在不散场的招之后——否则它一落下，同场其他人这一拍
    想好的事全都落空（「…no longer legal at resolution」）。两招都会散场时仍照上面的先后。
    """
    cache: dict[str, tuple[set[str], set[str]]] = {}

    def key(item: tuple[int, MindDecision]) -> tuple[int, int, int, int]:
        i, d = item
        c = state.chars.get(d.actor)
        if c is None:
            return (1, 3, 0, i)
        if c.loc not in cache:
            cache[c.loc] = (addressed(state, events, c.loc), involved(state, events, c.loc))
        spoken_to, inv = cache[c.loc]
        rank = 0 if c.id in spoken_to else 1 if c.id in inv else 2
        return (1 if d.closing else 0, rank, -c.initiative, i)

    return [d for _, d in sorted(enumerate(decisions), key=key)]


def speech_stands(state: WorldState, defs: Defs, d: MindDecision) -> bool:
    """结算那一刻这句话还说得成：指名的听话人至少有一个还在跟前；答复的那桩请求还等着他答。"""
    sp = d.speech
    c = state.chars.get(d.actor)
    if sp is None or c is None:
        return False
    if sp.to:
        heard = {o.id for o in kspeech.audience(state, c)}
        if not heard.intersection(sp.to):
            return False
    if sp.act in kspeech.REPLY_ACTS:
        r = state.requests.get(sp.request or "")
        if r is not None:
            mine = (r.to == c.id and r.status in ("open", "countered")) or (r.frm == c.id and r.status == "countered")
            if not mine:
                return False
        elif not (kspeech.open_requests_to(state, c.id) or kspeech.countered_from(state, c.id)):
            return False
    return True


def _flags_set(effs: Any) -> set[str]:
    out: set[str] = set()
    for e in effs or []:
        if not isinstance(e, dict):
            continue
        v = e.get("set_flag")
        if isinstance(v, list) and v:
            out.add(str(v[0]))
        elif isinstance(v, str):
            out.add(v)
        chk = e.get("check")
        if isinstance(chk, dict):
            out |= _flags_set(list(chk.get("ok") or []) + list(chk.get("fail") or []))
    return out


def _flags_read(pred: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(pred, dict):
        for k, v in pred.items():
            if k == "flag":
                out.add(str(v[0] if isinstance(v, list) and v else v))
            else:
                out |= _flags_read(v)
    elif isinstance(pred, list):
        for v in pred:
            out |= _flags_read(v)
    return out


def closes_situation(state: WorldState, defs: Defs, a: ActionInst) -> bool:
    """这一招立的旗标正是它那场情境散场条件里看的：做了它，这场事就散了。"""
    if a.verb != "setpiece":
        return False
    s = state.situations.get(str(a.params.get("situation", "")))
    d = defs.situations.get(s.kind) if s is not None else None
    sa = d.actions.get(str(a.params.get("name"))) if d is not None else None
    if d is None or sa is None or d.exit_when is None:
        return False
    return bool(_flags_set(sa.effects) & _flags_read(d.exit_when))


def agreed_action(state: WorldState, defs: Defs, actor: str, speech: Speech | None) -> ActionInst | None:
    """一句「答应」连着做的那件事（5.7：有人劝过，他这一拍就照做，不必再等一轮）。

    所求之事此刻在他的合法清单里，就是它；所求是「住手」「等一等」的，就是等。裁决不许答应、
    所求是开口说话、或者那件事此刻做不出的，返回 None（动作照原先选的，答应的那一步留在打算里）。
    """
    if speech is None or speech.act != "agree":
        return None
    r = state.requests.get(speech.request or "")
    if r is None:  # 没指明是哪一桩：与内核同样取冲他来的第一个
        mine = kspeech.open_requests_to(state, actor)
        r = mine[0] if mine else None
    if r is None or r.to != actor or r.status != "open" or r.kind == "question":
        return None
    if "agree" not in kspeech.allowed_replies(r):
        return None
    verb = r.action.get("verb")
    legal = actions.legal_actions(state, defs, actor)
    if r.action.get("stop") or verb == "wait":
        return wait_action(legal)
    if not verb or verb == "say":
        return None
    do = {"verb": verb, **(r.action.get("params") or {})}
    ctx = Ctx(self_id=actor)
    for a in legal:
        if actions.matches_do(a, do, ctx, state=state):
            return a
    return None


def keep_word(state: WorldState, defs: Defs, d: MindDecision) -> MindDecision:
    """抉择里的话是「答应」时，把动作换成答应的那件事（同一个抉择里说到做到）。"""
    if d.actor == state.player:
        return d
    act = agreed_action(state, defs, d.actor, d.speech)
    if act is None or act.signature == d.action.signature:
        return d
    return d.model_copy(update={"action": act, "notes": [*d.notes, f"agreed → {act.signature}"]})


def _finalize(state: WorldState, defs: Defs, d: MindDecision, offscreen: bool = False) -> MindDecision:
    # 离场的答复由 world.offscreen 一次结算，答应的那一步随后照离场的规矩推进：不在这里换动作
    if not offscreen:
        d = keep_word(state, defs, d)
    if closes_situation(state, defs, d.action):
        d = d.model_copy(update={"closing": True})
    return d


def apply_decision(state: WorldState, defs: Defs, d: MindDecision) -> list[Event]:
    """逐个结算（给 game 用）：动作此刻已不合法的，降级为 ``wait`` 并记日志，不重新调用模型（4.3）。

    结算前再核一遍那句话（``speech_stands``）；话是「答应」而所求之事此刻做得出的，先话后事分两次结算。
    """
    act = d.action
    if d.speech is not None and not speech_stands(state, defs, d):
        # 抉择之后、轮到他之前，听话的人走开了，或要答复的那桩请求已经不在了：这句话不说了。
        # 这是同一微轮里别人先结算造成的，不是谁违了规，不进 violations。
        d = d.model_copy(update={"speech": None, "notes": [*d.notes, "speech moot at resolution"]})
    kept = agreed_action(state, defs, d.actor, d.speech)
    if kept is not None and kept.verb != "wait":
        # 答应了就做：先结算那句话（内核把答应的事插成他眼下这一步），紧接着结算那件事。
        # 分两次进内核：同一次结算里才插进来的一步，不变量会当成「已作废的步骤」而整体回滚。
        w = actions.build(state, defs, d.actor, "wait", {"ticks": 1})
        said = resolve.apply(state, defs, w, d.speech, source="mind") if w is not None else []
        r = state.requests.get(said[0].speech.request or "") if said and said[0].speech is not None else None
        if r is None or r.status != "agreed":
            return said
        now = actions.is_legal(state, defs, kept)
        if now is None:
            log_violation(state, f"{d.actor}: agreed {kept.signature} but cannot do it now")
            return said
        return said + resolve.apply(state, defs, now, None, source="mind")
    if kept is not None:
        act = kept
    elif actions.is_legal(state, defs, act) is None:
        log_violation(state, f"{d.actor}: {act.signature} no longer legal at resolution → wait")
        w = actions.build(state, defs, d.actor, "wait", {"ticks": 1})
        if w is None:
            return []
        act = w
    return resolve.apply(state, defs, act, d.speech, source="mind")


# ---------------------------------------------------------------- importance & stars


def is_important(state: WorldState, defs: Defs, actor: str, events: Sequence[Event] = ()) -> bool:
    """内核把这一刻标为要紧（4.3）：牵涉此人的性命攸关之事，或冲他来的「冒险」「违背所属」的请求。"""
    c = state.chars[actor]
    here = perception.present(state, c.loc)
    for a in here:
        for b in here:
            if a.id != b.id and actor in (a.id, b.id) and danger.intends_kill(state, a, b.id):
                return True
    for ev in events:
        if ev.loc != c.loc:
            continue
        touched = actor in (ev.actor, ev.params.get("target")) or any(
            actor in (k.get("who"), k.get("by")) for k in ev.kinds
        )
        if not touched:
            continue
        if ev.verb == "attack" and ev.params.get("intent") == "kill":
            return True
        if any(k.get("k") == "death" for k in ev.kinds):
            return True
    for r in state.requests.values():
        if r.to == actor and r.status == "open" and r.kind != "question" and r.action_class in ("risky", "betray"):
            return True
    return False


def _setpiece_info(state: WorldState, defs: Defs, a: ActionInst) -> tuple[bool, int]:
    s = state.situations.get(str(a.params.get("situation", "")))
    if s is None:
        return False, 0
    d = defs.situations.get(s.kind)
    if d is None:
        return False, 0
    names = list(d.actions)
    name = str(a.params.get("name"))
    sa = d.actions.get(name)
    return (sa is not None and sa.pre is not None), (names.index(name) if name in names else 0)


def _recently_did(state: WorldState, actor: str, verb: str) -> bool:
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - RECENT_SETPIECE:
            break
        if ev.actor == actor and ev.verb == verb:
            return True
    return False


def preferred_star(state: WorldState, defs: Defs, acts: Sequence[ActionInst]) -> ActionInst | None:
    """★ 不止一个时取哪个：刚做过的情境动作排最后；情境动作先于打算；有前提的（戏往下走的那一拍）先于
    没前提的；后声明的先于先声明的；不是「等」的先于「等」，「等」取最短的。"""
    stars = [a for a in acts if a.star]
    if not stars:
        return None

    def key(a: ActionInst) -> tuple:
        if a.verb == "setpiece":
            has_pre, idx = _setpiece_info(state, defs, a)
            done = _recently_did(state, a.actor, str(a.params.get("name")))
            return (1 if done else 0, 0, 0 if has_pre else 1, -idx, (0, 0))
        return (0, 1, 0, 0, (1 if a.verb == "wait" else 0, a.params.get("ticks", 0)))

    return min(stars, key=key)


def wait_action(acts: Sequence[ActionInst]) -> ActionInst | None:
    waits = [a for a in acts if a.verb == "wait"]
    return min(waits, key=lambda a: a.params.get("ticks", 1)) if waits else None


def rule_action(state: WorldState, defs: Defs, acts: Sequence[ActionInst]) -> ActionInst | None:
    """规则：★ 动作；没有就 wait。"""
    return preferred_star(state, defs, acts) or wait_action(acts)


# ---------------------------------------------------------------- one mind


@dataclass
class _Mind:
    actor: str
    events: list[Event]
    offscreen: bool
    c: Character
    category: str = "decide_named"
    mode: str = "jev_first"
    c2_kind: str = "C2_named"
    acts: list[ActionInst] = field(default_factory=list)
    by_id: dict[str, ActionInst] = field(default_factory=dict)
    opts: SpeechOptions = field(default_factory=SpeechOptions)
    text: str = ""
    important: bool = False
    rule: ActionInst | None = None
    stage: str = "done"  # j2a / c2 / j2b / done
    escalated: bool = False
    j2a: Any = None
    j2a_err: str | None = None
    c2: Decision | None = None
    c2_err: str | None = None
    action: ActionInst | None = None
    via: str = "jev"
    j2b_facts: dict[str, str] = field(default_factory=dict)
    j2b_conds: dict[str, dict[str, dict]] = field(default_factory=dict)
    j2b: Any = None
    j2b_err: str | None = None
    decision: MindDecision | None = None
    notes: list[str] = field(default_factory=list)


def _prepare(llm: Any, state: WorldState, defs: Defs, actor: str, events: Sequence[Event], offscreen: bool) -> _Mind:
    from tianlong.minds import routines

    c = state.chars[actor]
    m = _Mind(actor=actor, events=list(events), offscreen=offscreen, c=c)
    if actor == state.player:  # 玩家的动作只来自玩家
        m.decision = MindDecision(actor=actor, action=idle_action(actor), via="rule", notes=["player"])
        return m
    if c.tier == "extra":
        m.decision = routines.act(state, defs, actor)
        return m
    m.acts = actions.legal_actions(state, defs, actor, for_model=True)
    if not m.acts:
        m.decision = MindDecision(actor=actor, action=idle_action(actor), via="rule", notes=["cannot act"])
        return m
    m.by_id = {a.id: a for a in m.acts}
    m.opts = kspeech.speech_options(state, defs, actor)
    m.rule = rule_action(state, defs, m.acts)
    m.important = is_important(state, defs, actor, events) or any(a.star and a.lethal for a in m.acts)
    m.category = "decide_soul" if c.tier == "soul" else "decide_named"
    m.c2_kind = "C2_soul" if c.tier == "soul" else "C2_named"
    m.mode = llm.mode(m.category)
    if m.mode == "rules_only":
        m.decision = _rule_decision(state, defs, m, "route rules_only")
        return m
    m.text = render.state_text(state, defs, actor, events)
    m.stage = "j2a" if m.mode == "jev_first" else "c2"
    return m


def _rule_decision(state: WorldState, defs: Defs, m: _Mind, why: str) -> MindDecision:
    action = m.rule or idle_action(m.actor)
    sp = talk.star_speech(state, defs, m.c, m.opts, m.events)
    return MindDecision(
        actor=m.actor,
        action=action,
        speech=sp,
        via="rule",
        escalated=m.escalated,
        important=m.important,
        notes=[*m.notes, why],
    )


# ---------------------------------------------------------------- J2a


def _ordered_acts(state: WorldState, defs: Defs, m: _Mind) -> list[ActionInst]:
    best = preferred_star(state, defs, m.acts)
    return ([best] if best else []) + [a for a in m.acts if a is not best]


def j2a_questions(state: WorldState, defs: Defs, m: _Mind) -> dict[str, Any]:
    q = talk.questions("j2a_questions")
    criteria: dict[str, str] = {}
    for a in _ordered_acts(state, defs, m):
        text = render.render_for(a.desc, state, defs, m.actor)
        criteria[a.id] = (STAR_PREFIX + text) if a.star else text
    criteria[NONE] = str(q["none"])
    return {
        "action": {"type": "choice", "instructions": str(q["action"]), "criteria": criteria},
        "speak": {"type": "noul", "instructions": str(q["speak"])},
        "needs_thought": {"type": "noul", "instructions": str(q["needs_thought"])},
    }


def j2a_hints(state: WorldState, defs: Defs, m: _Mind) -> dict[str, Any]:
    """Mock 的提示（真 Jev 忽略）：取规则的动作；有 ★ 的话要说、有请求冲着来就开口；拿得定主意。"""
    wants = bool(m.opts.star) or bool(m.opts.requests)
    return {
        "action": m.rule.id if m.rule is not None else NONE,
        "speak": 1.0 if wants and m.opts.acts else 0.0,
        "needs_thought": 0.0,
    }


async def _ask_j2a(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    if not llm.jev_available:
        m.j2a_err = "jev unavailable"
        return
    try:
        m.j2a = await llm.jev.ask(
            "J2a", m.text, j2a_questions(state, defs, m), hints=j2a_hints(state, defs, m)
        )
    except Exception as e:  # noqa: BLE001 — 模型失败一律退让（4.3）
        m.j2a_err = f"{type(e).__name__}: {e}"


def _roll(state: WorldState) -> Callable[[], float]:
    return lambda: rng.uniform(state)


def _sample(state: WorldState, ans: Any, floor: float) -> str | None:
    probs = getattr(ans, "probabilities", None) or (ans.get("probabilities") if isinstance(ans, dict) else None)
    if not probs:
        choice = ans.get("choice") if isinstance(ans, dict) else None
        return str(choice) if choice else None
    return route.sample_choice(probs, _roll(state), floor)


def _take_j2a(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    """J2a 的答案落成动作：取样；要开口就排 J2b。"""
    th = llm.settings.jev.thresholds
    pick = _sample(state, m.j2a["action"], th.sample_floor)
    if pick == NONE:
        action = wait_action(m.acts)
    else:
        action = m.by_id.get(pick or "")
    if action is None:
        m.decision = _rule_decision(state, defs, m, f"J2a picked {pick!r}, not on the list")
        m.stage = "done"
        return
    m.action = action
    m.via = "jev"
    speak = float(m.j2a["speak"].get("noul", 0.0)) >= th.noul_yes
    if speak and talk.model_acts(state, defs, m.c, m.opts):
        m.stage = "j2b"
    else:
        _finish(m, None)


def _finish(m: _Mind, speech: Speech | None, *, inner: str = "", display: str = "") -> None:
    assert m.action is not None
    m.decision = MindDecision(
        actor=m.actor,
        action=m.action,
        speech=speech,
        inner=inner,
        display=display,
        via=m.via,  # type: ignore[arg-type]
        escalated=m.escalated,
        important=m.important,
        notes=list(m.notes),
    )
    m.stage = "done"


# ---------------------------------------------------------------- C2


def _speech_out(sp: Speech | None) -> SpeechOut | None:
    if sp is None:
        return None
    return SpeechOut(
        act=sp.act,
        to=list(sp.to),
        stance=sp.stance,
        gist=sp.gist,
        fact_refs=list(sp.fact_refs),
        request_ref=sp.request,
        whisper=sp.whisper,
    )


def mock_decision(state: WorldState, defs: Defs, m: _Mind) -> Decision:
    """MockClaude 的回答：动作同规则（★ 或 wait）；★ 的那一步是开口就照说，否则按裁决答复冲着来的请求。"""
    sp = talk.star_speech(state, defs, m.c, m.opts, m.events)
    pv = talk.primary_view(m.opts, m.events)
    if sp is None and pv is not None:
        rep = talk.preferred_reply(state, m.c, pv, m.opts)
        sp = talk.compose(state, defs, m.c, m.opts, act=rep, events=m.events, request_ref=pv.request.id)
    return Decision(
        inner="照原先的打算行事。",
        action=m.rule.id if m.rule is not None else NONE,
        speech=_speech_out(sp),
        display="",
    )


def c2_user(state: WorldState, defs: Defs, m: _Mind) -> str:
    lab = talk.labels
    acts = talk.model_acts(state, defs, m.c, m.opts)
    people = [
        {"id": pid, "name": render.name_for(state, defs, m.actor, pid)} for pid in talk.hearers(state, m.c)
    ]
    reqs = []
    for v in m.opts.requests:
        replies = [r for r in v.allowed_replies if r in m.opts.acts]
        reqs.append(
            {
                "id": v.request.id,
                "text": render.request_line(state, defs, m.c, v.request),
                "replies": "、".join(f"{r}（{lab('replies').get(r, r)}）" for r in replies) or "无",
            }
        )
    return prompting.render(
        "c2_user",
        state=m.text,
        actions=[
            {"id": a.id, "star": a.star, "text": render.render_for(a.desc, state, defs, m.actor)} for a in m.acts
        ],
        acts=[{"id": a, "label": lab("acts").get(a, a)} for a in acts],
        people=people,
        stances=[{"id": s, "label": lab("stances").get(s, s)} for s in talk.STANCES],
        facts=[
            {"id": f, "text": render.render_for(state.facts[f].text, state, defs, m.actor)}
            for f in m.opts.facts
            if f in state.facts
        ],
        requests=reqs,
    )


async def _ask_c2(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    if not llm.claude_available:
        m.c2_err = "claude unavailable"
        return
    try:
        system = [prompting.load("c2_system"), render.card_block(defs, m.actor, state=state)]
        obj, _meta = await llm.claude.call_json(
            m.c2_kind,
            system,
            c2_user(state, defs, m),
            Decision,
            mock_default=lambda: mock_decision(state, defs, m),
            escalated=m.escalated,
        )
        m.c2 = obj
    except Exception as e:  # noqa: BLE001 — 模型失败一律退让（4.3）
        m.c2_err = f"{type(e).__name__}: {e}"


def _take_c2(state: WorldState, defs: Defs, m: _Mind) -> None:
    """C2 的答案按 7.6 的表校验后落成抉择；动作不在清单里 → 退到 ★ / wait。"""
    dec = m.c2
    assert dec is not None
    aid = str(dec.action or "").strip()
    action = wait_action(m.acts) if aid == NONE else m.by_id.get(aid)
    if action is None:
        m.decision = _rule_decision(state, defs, m, f"C2 action {aid!r} not on the list")
        m.stage = "done"
        return
    m.action = action
    m.via = "claude"
    sp = None
    if dec.speech is not None:
        sp = talk.from_out(state, defs, m.c, dec.speech, m.opts, m.events)
        if sp is None:
            m.notes.append(f"C2 speech {dec.speech.act!r} dropped by validation")
    _finish(m, sp, inner=render.clip(dec.inner, INNER_MAX), display=render.clip(dec.display, DISPLAY_MAX))


# ---------------------------------------------------------------- J2b


def j2b_questions(state: WorldState, defs: Defs, m: _Mind) -> dict[str, Any]:
    q = talk.questions("j2b_questions")
    lab = talk.labels
    acts = talk.model_acts(state, defs, m.c, m.opts)
    qs: dict[str, Any] = {
        "act": {
            "type": "choice",
            "instructions": str(q["act"]),
            "criteria": {a: lab("acts").get(a, a) for a in acts},
        }
    }
    to = {pid: render.name_for(state, defs, m.actor, pid) for pid in talk.hearers(state, m.c)}
    to["all"] = str(q["all"])
    qs["to"] = {"type": "choice", "instructions": str(q["to"]), "criteria": to}
    qs["stance"] = {
        "type": "choice",
        "instructions": str(q["stance"]),
        "criteria": {s: lab("stances").get(s, s) for s in talk.STANCES},
    }
    m.j2b_facts = {}
    for i, fid in enumerate(m.opts.facts, 1):
        f = state.facts.get(fid)
        if f is None:
            continue
        qid = f"tell_{i}"
        m.j2b_facts[qid] = fid
        text = render.render_for(f.text, state, defs, m.actor)
        qs[qid] = {"type": "noul", "instructions": str(q["tell"]).format(fact=text)}
    m.j2b_conds = {}
    for v in m.opts.requests:
        rid = v.request.id
        replies = [r for r in v.allowed_replies if r in m.opts.acts]
        if not replies:
            continue
        line = render.request_line(state, defs, m.c, v.request)
        qs[f"reply_{rid}"] = {
            "type": "choice",
            "instructions": str(q["reply"]).format(request=line),
            "criteria": {r: lab("replies").get(r, r) for r in replies},
        }
        if "bargain" in replies and v.counter_options:
            opts = {f"c{j}": opt for j, opt in enumerate(v.counter_options, 1)}
            m.j2b_conds[rid] = opts
            qs[f"cond_{rid}"] = {
                "type": "choice",
                "instructions": str(q["cond"]),
                "criteria": {
                    k: render.render_for(talk.describe_do(talk.flat(o)), state, defs, m.actor) for k, o in opts.items()
                },
            }
    qs["whisper"] = {"type": "noul", "instructions": str(q["whisper"])}
    return qs


def _stance_for(act: str | None) -> str:
    return {
        "refuse": "cold",
        "insult": "mocking",
        "taunt": "mocking",
        "plead": "pleading",
        "threaten": "angry",
        "comfort": "warm",
        "greet": "warm",
    }.get(act or "", "plain")


def j2b_hints(state: WorldState, defs: Defs, m: _Mind) -> dict[str, Any]:
    """Mock 的提示：★ 的那一步要说什么就说什么；有请求冲着来，按裁决给出内核最顺的答复。"""
    hints: dict[str, Any] = {"whisper": 0.0}
    star = m.opts.star or {}
    fact = None
    pv = talk.primary_view(m.opts, m.events)
    acts = talk.model_acts(state, defs, m.c, m.opts)
    if star.get("act") in acts:
        hints["act"] = star["act"]
        hints["to"] = star.get("to") or "all"
        fact = star.get("fact")
    elif pv is not None:
        rep = talk.preferred_reply(state, m.c, pv, m.opts)
        hints["act"] = rep
        r = pv.request
        hints["to"] = r.to if r.frm == m.actor else r.frm
        if r.kind == "question" and rep == "tell":
            fact = talk.relevant_fact(state, m.c, m.opts, (r.action.get("params") or {}).get("about"))
    hints["stance"] = _stance_for(hints.get("act"))
    for qid, fid in m.j2b_facts.items():
        hints[qid] = 1.0 if fid == fact else 0.0
    for v in m.opts.requests:
        hints[f"reply_{v.request.id}"] = talk.preferred_reply(state, m.c, v, m.opts)
        if v.request.id in m.j2b_conds:
            hints[f"cond_{v.request.id}"] = "c1"
    return hints


async def _ask_j2b(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    if not llm.jev_available:
        m.j2b_err = "jev unavailable"
        return
    try:
        assert m.action is not None
        q = talk.questions("j2b_questions")
        decided = str(q["decided"]).format(action=render.render_for(m.action.desc, state, defs, m.actor))
        qs = j2b_questions(state, defs, m)
        m.j2b = await llm.jev.ask("J2b", m.text + "\n" + decided, qs, hints=j2b_hints(state, defs, m))
    except Exception as e:  # noqa: BLE001 — 模型失败一律退让（4.3）
        m.j2b_err = f"{type(e).__name__}: {e}"


def _take_j2b(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    if m.j2b is None:
        m.notes.append(f"J2b failed ({m.j2b_err}); no speech")
        _finish(m, None)
        return
    th = llm.settings.jev.thresholds
    a = m.j2b
    act = _sample(state, a["act"], th.sample_floor) or ""
    to = _sample(state, a["to"], th.sample_floor)
    stance = _sample(state, a["stance"], th.sample_floor) or "plain"
    told = sorted(
        ((float(a[qid].get("noul", 0.0)), qid) for qid in m.j2b_facts if qid in a),
        key=lambda x: (-x[0], x[1]),
    )
    facts = [m.j2b_facts[qid] for p, qid in told if p >= th.noul_yes][: talk.MAX_TELL]
    replies: dict[str, str] = {}
    conds: dict[str, dict] = {}
    for v in m.opts.requests:
        rid = v.request.id
        if f"reply_{rid}" in a:
            r = _sample(state, a[f"reply_{rid}"], th.sample_floor)
            if r:
                replies[rid] = r
        if f"cond_{rid}" in a:
            k = _sample(state, a[f"cond_{rid}"], th.sample_floor)
            if k and k in m.j2b_conds.get(rid, {}):
                conds[rid] = m.j2b_conds[rid][k]
    whisper = float(a["whisper"].get("noul", 0.0)) >= th.noul_yes
    sp = talk.compose(
        state,
        defs,
        m.c,
        m.opts,
        act=act,
        to=None if to in (None, "all") else to,
        stance=stance,
        facts=facts,
        replies=replies,
        conds=conds,
        whisper=whisper,
        events=m.events,
    )
    if sp is None:
        m.notes.append(f"J2b speech {act!r} dropped by validation")
    _finish(m, sp)


# ---------------------------------------------------------------- the phases


def _after_first(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    th = llm.settings.jev.thresholds
    if m.mode == "jev_first":
        if m.j2a is None:
            m.notes.append(f"J2a failed ({m.j2a_err}); → C2")
            m.stage = "c2"
            return
        esc = m.important if m.offscreen else route.should_escalate(m.j2a, th, m.important, keys=["action"])
        if esc and llm.claude_available:
            m.escalated = True
            m.stage = "c2"
            return
        if esc:
            m.notes.append("escalation wanted, claude unavailable; keep J2")
        _take_j2a(llm, state, defs, m)
        return
    # claude_first / claude_only
    if m.c2 is not None:
        _take_c2(state, defs, m)
        return
    m.notes.append(f"C2 failed ({m.c2_err})")
    if m.mode == "claude_first":
        m.stage = "j2a"
    else:
        m.decision = _rule_decision(state, defs, m, "fallback ★/wait")
        m.stage = "done"


def _after_second(llm: Any, state: WorldState, defs: Defs, m: _Mind) -> None:
    if m.mode == "jev_first":
        if m.c2 is not None:
            _take_c2(state, defs, m)
            return
        m.notes.append(f"C2 failed ({m.c2_err})")
        if m.j2a is not None:
            _take_j2a(llm, state, defs, m)  # 升级失败：J2 已经答了，用它的答案
        else:
            m.decision = _rule_decision(state, defs, m, "fallback ★/wait")
            m.stage = "done"
        return
    # claude_first 的退路：J2
    if m.j2a is not None:
        _take_j2a(llm, state, defs, m)
    else:
        m.notes.append(f"J2a failed ({m.j2a_err})")
        m.decision = _rule_decision(state, defs, m, "fallback ★/wait")
        m.stage = "done"


async def _run(
    llm: Any,
    state: WorldState,
    defs: Defs,
    actors: Sequence[str],
    events: Sequence[Event],
    offscreen: bool,
) -> list[MindDecision]:
    minds = [_prepare(llm, state, defs, a, events, offscreen) for a in actors]

    async def call(m: _Mind) -> None:
        if m.stage == "j2a":
            await _ask_j2a(llm, state, defs, m)
        elif m.stage == "c2":
            await _ask_c2(llm, state, defs, m)

    # 第一轮请求：J2a（jev_first）或 C2（claude_first / claude_only）
    first = [m for m in minds if m.stage in ("j2a", "c2")]
    await asyncio.gather(*(call(m) for m in first))
    for m in first:
        _after_first(llm, state, defs, m)
    # 第二轮：升级或 J2 失败后的 C2；claude_first 失败后的 J2a
    second = [m for m in first if m.stage in ("j2a", "c2")]
    await asyncio.gather(*(call(m) for m in second))
    for m in second:
        _after_second(llm, state, defs, m)
    # 第三轮：要开口的发 J2b
    talkers = [m for m in minds if m.stage == "j2b"]
    await asyncio.gather(*(_ask_j2b(llm, state, defs, m) for m in talkers))
    for m in talkers:
        _take_j2b(llm, state, defs, m)
    out: list[MindDecision] = []
    for m in minds:
        if m.decision is None:  # 不该发生；保底
            m.decision = _rule_decision(state, defs, m, "no decision reached")
        out.append(_finalize(state, defs, m.decision, offscreen))
    return out


async def decide_one(
    llm: Any,
    state: WorldState,
    defs: Defs,
    actor: str,
    *,
    events: Sequence[Event] = (),
    offscreen: bool = False,
) -> MindDecision:
    """一个人的抉择。``offscreen``：此人不在玩家眼前，只在要紧时才升级给 Claude。"""
    return (await _run(llm, state, defs, [actor], events, offscreen))[0]


async def decide_all(
    llm: Any, state: WorldState, defs: Defs, actors: Sequence[str], *, events: Sequence[Event] = ()
) -> list[MindDecision]:
    """同一微轮里各人的抉择，模型请求并行发出；结果按 ``actors`` 的顺序。"""
    return await _run(llm, state, defs, list(actors), events, False)


__all__ = [
    "MindDecision",
    "pick_reactors",
    "decide_one",
    "decide_all",
    "order",
    "apply_decision",
    "is_important",
    "preferred_star",
    "rule_action",
    "involved",
    "addressed",
    "agreed_action",
    "closes_situation",
    "keep_word",
    "speech_stands",
    "MAX_REACTORS",
]
