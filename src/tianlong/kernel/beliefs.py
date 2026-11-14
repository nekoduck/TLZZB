"""信念更新（方案 6.7）：断言的结论、听人说的置信度公式与「你的判断」。

```text
亲眼所见    1.0
听人说      0.5 + trust/200 + 0.2（出示了实物）- 0.3（与自己确信的事相悖）- 0.2（unsourced）
结果 >= 0.6 信；0.3 到 0.6 将信将疑；低于 0.3 不信（此话若对说者有利，对说者 trust -5）
```
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tianlong.kernel import effects, memory, mood
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, Character, Claim, MemoryItem, WorldState

SAW = 1.0
BELIEVE = 0.6
DOUBT = 0.3
SURE = 0.9  # 「确信」
SHOULD_KNOW_WINDOW = TICKS_PER_DAY  # 一日之内跟说者在一起过，就「本该知道」

Label = Literal["believe", "doubt", "disbelieve", "unchanged"]


class Judgement(BaseModel):
    fact: str | None = None
    verdict: str = "true"
    confidence: float | None = None
    label: Label = "unchanged"
    text: str = ""


def contradicted(state: WorldState, listener: Character, fid: str) -> bool:
    f = state.facts.get(fid)
    if f is None:
        return False
    for other in f.contradicts:
        b = listener.beliefs.get(other)
        if b is not None and b.confidence >= SURE:
            return True
    for oid, b in listener.beliefs.items():
        if b.confidence >= SURE:
            of = state.facts.get(oid)
            if of is not None and fid in of.contradicts:
                return True
    return False


def hearsay(
    state: WorldState,
    listener: Character,
    speaker: str,
    fid: str,
    *,
    shown: bool = False,
    unsourced: bool = False,
) -> float:
    r = listener.rel.get(speaker)
    trust = r.trust if r is not None else 0
    conf = 0.5 + trust / 200
    if shown:
        conf += 0.2
    if contradicted(state, listener, fid):
        conf -= 0.3
    if unsourced:
        conf -= 0.2
    return round(max(0.0, min(1.0, conf)), 4)


def label(conf: float) -> Label:
    if conf >= BELIEVE:
        return "believe"
    if conf >= DOUBT:
        return "doubt"
    return "disbelieve"


_LABEL_TEXT = {"believe": "信了", "doubt": "半信半疑", "disbelieve": "不信", "unchanged": "心里有数"}


def judge_text(
    lab: Label, *, shown: bool = False, unsourced: bool = False, contra: bool = False, trusted: bool = False
) -> str:
    """附在那句话后面的一句话，例如「你的判断：半信半疑，他拿不出凭据」。"""
    why = ""
    if contra:
        why = "这与你确知的事相悖"
    elif shown:
        why = "他拿出了实物"
    elif unsourced:
        why = "他拿不出凭据"
    elif trusted:
        why = "你信得过他"
    elif lab != "believe":
        why = "他拿不出凭据"
    return f"你的判断：{_LABEL_TEXT[lab]}" + (f"，{why}" if why else "")


def should_know(state: WorldState, listener: str, speaker: str) -> bool:
    """听者当时在场或本该知道：一日之内亲见过说者的所作所为。"""
    for ev in reversed(state.events_tail):
        if ev.tick < state.tick - SHOULD_KNOW_WINDOW:
            break
        if ev.actor == speaker and ev.witnesses.get(listener) == "saw":
            return True
    return False


def hear(
    state: WorldState,
    defs: Defs,
    listener: Character,
    speaker: str,
    fid: str,
    *,
    shown: bool = False,
    unsourced: bool = False,
    beneficial: bool = False,
) -> Judgement:
    """按「听人说」更新一条信念。"""
    conf = hearsay(state, listener, speaker, fid, shown=shown, unsourced=unsourced)
    lab = label(conf)
    old = listener.beliefs.get(fid)
    if old is None or old.confidence < conf:
        effects.learn(state, listener, fid, conf, f"told:{speaker}")
    if lab == "disbelieve" and beneficial:
        mood.adj_rel(state, listener.id, speaker, "trust", -5)
    r = listener.rel.get(speaker)
    return Judgement(
        fact=fid,
        verdict="unsourced" if unsourced else "true",
        confidence=conf,
        label=lab,
        text=judge_text(
            lab,
            shown=shown,
            unsourced=unsourced,
            contra=contradicted(state, listener, fid),
            trusted=r is not None and r.trust >= 50,
        ),
    )


def claim_verdict(state: WorldState, speaker: str, ref: str | None, *, self_claim: bool = False) -> str:
    """给玩家的一条断言下结论（6.7）。ref 是事实 id，或事件 id（``E12`` / ``event:12`` / ``12``）。"""
    c = state.chars[speaker]
    if ref is None:
        return "false_self" if self_claim else "opinion"
    eid = _event_id(ref)
    if eid is not None and (ref not in state.facts or self_claim):
        for ev in state.events_tail:
            if ev.id == eid:
                if self_claim:
                    return "true" if ev.actor == speaker else "false_self"
                return "true" if speaker in ev.witnesses or ev.actor == speaker else "unsourced"
        return "false_self" if self_claim else "unsourced"
    if ref in state.facts:
        b = c.beliefs.get(ref)
        return "true" if b is not None and b.confidence >= BELIEVE else "unsourced"
    return "false_self" if self_claim else "opinion"


def _event_id(ref: str) -> int | None:
    s = ref
    if s.startswith("event:"):
        s = s[6:]
    elif s.startswith("E"):
        s = s[1:]
    return int(s) if s.isdigit() else None


def hear_claim(
    state: WorldState,
    defs: Defs,
    listener: Character,
    speaker: str,
    claim: Claim,
    *,
    shown: bool = False,
    beneficial: bool = False,
) -> Judgement:
    """听者按 6.7 的表对待玩家的一条断言。"""
    v = claim.verdict
    fid = claim.ref if claim.ref in state.facts else None
    if v == "opinion":
        return Judgement(fact=fid, verdict=v, label="unchanged", text="你的判断：只当他在说闲话")
    if v == "false_self":
        if should_know(state, listener.id, speaker):
            mood.adj_rel(state, listener.id, speaker, "trust", -15)
            return Judgement(
                fact=fid,
                verdict=v,
                confidence=0.0,
                label="disbelieve",
                text="你的判断：不信，他根本没做过这事",
            )
        v = "unsourced"
    if v == "unsourced":
        if fid is None:
            mood.adj_mood(listener, "suspicion", 15)
            return Judgement(
                fact=None, verdict=v, label="disbelieve", text=judge_text("disbelieve", unsourced=True)
            )
        old = listener.beliefs.get(fid)
        if old is not None and old.confidence >= BELIEVE:
            mood.adj_mood(listener, "suspicion", 25)
            memory.write(
                state,
                listener,
                MemoryItem(
                    tick=state.tick,
                    kind="told",
                    text=f"⟦{speaker}⟧怎么会知道：{state.facts[fid].text}",
                    entities=[speaker],
                    salience=6,
                ),
            )
            return Judgement(
                fact=fid, verdict=v, label="unchanged", text="你的判断：此事是真，可他怎么会知道"
            )
        mood.adj_mood(listener, "suspicion", 15)
        return hear(state, defs, listener, speaker, fid, shown=shown, unsourced=True, beneficial=beneficial)
    if fid is None:
        return Judgement(fact=None, verdict=v, label="believe", text="你的判断：确有其事")
    return hear(state, defs, listener, speaker, fid, shown=shown, beneficial=beneficial)
