"""路人的作息（方案 7.6 末段、9.4）：不调任何模型，按 ``content/routines.yaml`` 的「条件 → 动作」表行动。

表的格式（D-011：由本模块定）::

    名字:
      - when: 谓词            # 可省；方案 5.2 的谓词，self 指此人
        do: {verb: …, …}      # 动作模式
        once_per: 48          # 可省；同一对象在这么多刻之内不重复这一条（按最近的事件判断）

自上而下取第一条 ``when`` 成立、而且此刻做得到的（动作合法、特殊对象找得到）。一条都不成立就 ``wait``。

``do`` 的写法：

- ``{verb: setpiece, names: [...]}``：此人在此地进行中的情境里的专用动作。写了 ``names`` 就按先后取第一个
  此刻可做的；没写就取标了 lean 的（★）。
- ``{verb: say, act, to, gist, stance, fact}``：开口。``gist`` 是给叙述用的要旨；``fact: gossip`` 指
  自己相信、对方还不信的传闻（``gossip`` 的事实，或亲眼见过的大事）。
- 其他动词照 ``actions.build`` 的参数：``target``、``target2``、``exit``、``item``、``feature``、``art``、
  ``ticks``、``hours``、``intent``。

特殊对象（在代码里解析，找不到这一条就不成立）：

- ``nearest_outsider``：在场、看得见、清醒，与自己没有共同身份、也不是上下级，近来没被放行过（``vetted``）的人。
- ``nearest_hostile``：对自己或自己的上司有敌意，或刚对自己人动过手的人。
- ``nearest_injured``：受了伤、昏迷、中了毒的人；``injured_ally``：其中与自己有共同身份或上下级的。
- ``nearest_person``：任何看得见、清醒的人。
- ``superior``：自己听命的人（要在场）。
- 出口写 ``away``：第一个走得了的出口。

同一地点的人一样「近」：玩家排第一，其余按 ``state.chars`` 的先后。
"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import actions, danger, perception, predicates
from tianlong.kernel import speech as kspeech
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Ctx, Speech, WorldState, has_status
from tianlong.minds import talk
from tianlong.minds.types import MindDecision, idle_action

SPECIAL_TARGETS = (
    "nearest_outsider",
    "nearest_hostile",
    "nearest_injured",
    "injured_ally",
    "nearest_person",
    "superior",
)
PARAM_KEYS = ("target", "target2", "item", "exit", "feature", "art", "ticks", "hours", "intent")
HOSTILE_WINDOW = 2  # 「刚对自己人动过手」：这么多刻之内
GOSSIP_TAGS = ("event",)


def table(defs: Defs) -> dict[str, list[dict]]:
    raw = defs.routines if isinstance(defs.routines, dict) else {}
    return {str(k): list(v or []) for k, v in raw.items()}


# ---------------------------------------------------------------- who is who


def _allies(c: Character, o: Character) -> bool:
    return bool(set(c.roles) & set(o.roles)) or c.superior == o.id or o.superior == c.id


def _people(state: WorldState, c: Character, *, conscious: bool = True) -> list[Character]:
    people = [
        o
        for o in perception.visible_people(state, c)
        if o.id != c.id and (perception.conscious(o) or not conscious)
    ]
    return sorted(people, key=lambda o: 0 if o.id == state.player else 1)


def _hurt(o: Character) -> bool:
    return (
        has_status(o, "injured")
        or has_status(o, "unconscious")
        or any(s.kind.startswith("poisoned") for s in o.statuses)
    )


def _struck_ally(state: WorldState, c: Character, o: Character) -> bool:
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - HOSTILE_WINDOW:
            break
        if ev.actor != o.id or ev.verb not in ("attack", "restrain") or ev.outcome == "blocked":
            continue
        t = state.chars.get(str(ev.params.get("target")))
        if t is not None and (t.id == c.id or _allies(c, t)):
            return True
    return False


def special_target(state: WorldState, c: Character, name: str) -> str | None:
    if name == "superior":
        ids = {o.id for o in perception.visible_people(state, c)}
        return c.superior if c.superior in ids else None
    if name == "nearest_person":
        ps = _people(state, c)
        return ps[0].id if ps else None
    if name == "nearest_outsider":
        for o in _people(state, c):
            if not _allies(c, o) and not has_status(o, "vetted"):
                return o.id
        return None
    if name == "nearest_hostile":
        for o in _people(state, c):
            if (
                danger.is_hostile(state, o, c.id)
                or (c.superior and danger.is_hostile(state, o, c.superior))
                or _struck_ally(state, c, o)
            ):
                return o.id
        return None
    if name in ("nearest_injured", "injured_ally"):
        for o in _people(state, c, conscious=False):
            if _hurt(o) and (name == "nearest_injured" or _allies(c, o)):
                return o.id
        return None
    return None


def _exit(state: WorldState, defs: Defs, c: Character, value: Any) -> Any:
    if value != "away":
        return value
    for e in actions.usable_exits(state, defs, c):
        if not e.edge:
            return e.to
    return None


def _value(state: WorldState, defs: Defs, c: Character, key: str, value: Any) -> Any:
    if isinstance(value, str) and value in SPECIAL_TARGETS:
        return special_target(state, c, value)
    if key == "exit":
        return _exit(state, defs, c, value)
    if isinstance(value, str):
        return predicates.resolve_id(value, state, Ctx(self_id=c.id))
    return value


def _recent(state: WorldState, c: Character, verb: str, target: str | None, n: int, act: str | None) -> bool:
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - n:
            break
        if ev.actor != c.id:
            continue
        if verb == "say":
            if ev.verb == "say" and ev.params.get("act") == act and (target is None or target in ev.params.get("to", [])):
                return True
        elif ev.verb == verb and (target is None or ev.params.get("target") == target):
            return True
    return False


def gossip_fact(state: WorldState, c: Character, target: str | None) -> str | None:
    """自己相信、对方还不信、自己还没对他说过的传闻：最新知道的先说。"""
    t = state.chars.get(target or "")
    told = {
        f
        for ev in state.events_tail
        if ev.actor == c.id and ev.speech is not None and (t is None or t.id in ev.speech.to)
        for f in ev.speech.fact_refs
    }
    cands = []
    for fid, b in c.beliefs.items():
        f = state.facts.get(fid)
        if f is None or b.confidence < predicates.BELIEVE or fid in told:
            continue
        if not (f.gossip or set(f.tags) & set(GOSSIP_TAGS)):
            continue
        if t is not None:
            tb = t.beliefs.get(fid)
            if tb is not None and tb.confidence >= predicates.BELIEVE:
                continue
        cands.append((-b.tick, fid))
    cands.sort()
    return cands[0][1] if cands else None


# ---------------------------------------------------------------- rows


def _setpiece(state: WorldState, defs: Defs, c: Character, do: dict) -> actions.ActionInst | None:
    """``names`` 按先后取第一个此刻可做、而且刚才没做过的（免得一拍戏反复做）。"""
    from tianlong.minds.decide import _recently_did, preferred_star

    sps = [a for a in actions.legal_actions(state, defs, c.id) if a.verb == "setpiece"]
    names = do.get("names") or ([do["name"]] if do.get("name") else [])
    for n in names:
        if _recently_did(state, c.id, str(n)):
            continue
        for a in sps:
            if a.params.get("name") == n:
                return a
    if names:
        return None
    return preferred_star(state, defs, [a for a in sps if a.star])


def _say(state: WorldState, defs: Defs, c: Character, do: dict) -> Speech | None:
    act = str(do.get("act", "tell"))
    to = do.get("to")
    target = _value(state, defs, c, "target", to) if to is not None else None
    if to is not None and target is None:
        return None
    if isinstance(target, str) and target not in state.chars:
        return None
    facts: list[str] = []
    if do.get("fact") == "gossip":
        f = gossip_fact(state, c, target if isinstance(target, str) else None)
        if f is None:
            return None
        facts = [f]
    elif isinstance(do.get("fact"), str):
        facts = [str(predicates.resolve_id(do["fact"], state, Ctx(self_id=c.id)))]
    opts = kspeech.speech_options(state, defs, c.id)
    if act not in opts.acts:
        return None
    sp = Speech(act=act, to=[target] if isinstance(target, str) else [], stance=str(do.get("stance", "plain")))
    if sp.stance not in talk.STANCES:
        sp.stance = "plain"
    sp.fact_refs = facts
    if act == "ask" and sp.to and sp.to[0] not in c.rel:
        sp.about = sp.to[0]
    if act in talk.ASK_ACTS:
        sp.ask_action = talk.derive_ask(state, c, act, sp.to)
    gist = str(do.get("gist") or "").strip()
    if facts and gist:
        gist = gist + "：" + "；".join(state.facts[f].text.rstrip("。") for f in facts if f in state.facts)
    sp.gist = gist or talk.make_gist(state, c, sp)
    opts = opts.model_copy(update={"facts": list(dict.fromkeys(list(opts.facts) + facts))})
    return talk.validate(state, defs, c, sp, opts)


def _row(state: WorldState, defs: Defs, c: Character, row: dict) -> MindDecision | None:
    if not isinstance(row, dict) or not isinstance(row.get("do"), dict):
        return None
    if row.get("when") is not None and not predicates.holds(state, defs, row["when"], Ctx(self_id=c.id)):
        return None
    do = dict(row["do"])
    verb = str(do.get("verb", "wait"))
    once = int(row.get("once_per") or 0)
    if verb == "setpiece":
        a = _setpiece(state, defs, c, do)
        return None if a is None else MindDecision(actor=c.id, action=a, via="routine")
    if verb == "say":
        sp = _say(state, defs, c, do)
        if sp is None:
            return None
        if once and _recent(state, c, "say", sp.to[0] if sp.to else None, once, sp.act):
            return None
        wait = actions.build(state, defs, c.id, "wait", {"ticks": 1})
        if wait is None:
            return None
        return MindDecision(actor=c.id, action=wait, speech=sp, via="routine")
    params: dict[str, Any] = {}
    for k in PARAM_KEYS:
        if k in do:
            v = _value(state, defs, c, k, do[k])
            if v is None:
                return None
            params[k] = v
    if verb == "wait":
        params.setdefault("ticks", 1)
    if once and _recent(state, c, verb, params.get("target"), once, None):
        return None
    a = actions.build(state, defs, c.id, verb, params)
    if a is None or actions.blocked_by_bottom(state, defs, a):
        return None
    return MindDecision(actor=c.id, action=a, via="routine")


def act(state: WorldState, defs: Defs, char_id: str) -> MindDecision:
    """路人此刻做什么。他的台词只是一句要旨（``speech.gist``），由叙述写成话。"""
    c = state.chars[char_id]
    rows = table(defs).get(c.routine or "", [])
    for i, row in enumerate(rows):
        d = _row(state, defs, c, row)
        if d is not None:
            d.notes.append(f"routine {c.routine}[{i}]")
            return d
    wait = actions.build(state, defs, char_id, "wait", {"ticks": 1})
    note = f"routine {c.routine!r}: no row applies" if c.routine else "no routine"
    return MindDecision(actor=char_id, action=wait or idle_action(char_id), via="routine", notes=[note])


__all__ = ["act", "table", "special_target", "gossip_fact", "SPECIAL_TARGETS"]
