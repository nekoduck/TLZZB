"""开口的选项（方案 2.1、2.3、8.4）：三选项里除了身体动作，还有「说一句话」。

每个候选是一句内核的 ``Speech``（言语行为、对象、要旨，请求类带所求之事），外加一句中性的意图描述
（``desc``，实体写成 ⟦id⟧）与它在计分表里命中的各行（``contrib``：方向 → 分）。配它的身体动作是 1 刻的
``wait``，与解析器处理「只说话」的做法相同；结算仍走 ``resolve.apply(action, speech)``，请求当场由内核裁决。

候选的来历（都只用玩家看得见、知道的东西；玩家不信的事不说，不知道的名字不提）：

| 一类 | 言语行为 | 何时给 | 计分表 |
| 答复 | agree / refuse / tell | 有人求你、问你，还没答复 | 人物 +30；险境里对有敌意的人照办，自保 +35 |
| 相劝 | request（所求是情境动作） | 眼前的情境里，某个角色有一招不是他性子里会出的（没有 lean）、又不伤人的，此刻出得了 | 介入 +50 |
| 示警 | warn | 眼前的情境里有人正要对某人下手；或你相信一件对某人不利的事 | 介入 +50（情境里） |
| 求情 | plead / request | 有人对你有敌意；有人扣着人、正要对人下手 | 自保 +35（险境里为自己）；介入 +50（替情境里的人） |
| 相求 | request | 放了你、解药、让你跟情境里的人同行、教你本事 | 自保、推进（按牵挂）、介入 +50、成长 +25 |
| 安慰 | comfort | 你有好感的人受了伤、被扣着、刚吃了亏 | 人物 +30 |
| 打听 | ask | 向在场的人问一条牵挂里提到的人、地、物；问一个不知其名的人的来历 | 推进（按牵挂）、人物 +30 |
| 告知 | tell | 你相信、与对方有关、对方还不知道的一件事 | 人物 +30；说的是情境里另一方的事，介入 +50 |
| 招呼 | greet | 初次见面、还没打过招呼 | 人物 +30 |

「人物 +30」的「未了的话头」在这里就是「这句话还没说过」：同一句话说过一次（同一个请求、同一件事、同一个
问题）就不再给，免得把选项变成复读（内核对复读的请求每次减 10 分）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tianlong.kernel import actions, danger, growth, mood, perception, refs
from tianlong.kernel import speech as kspeech
from tianlong.kernel.defs import Defs, SituationAction, SituationDef
from tianlong.kernel.state import (
    TICKS_PER_DAY,
    TICKS_PER_HOUR,
    Character,
    SituationState,
    Speech,
    Thread,
    WorldState,
    has_status,
    injury_level,
)
from tianlong.storyteller import common

FAMILY_CAP = 2  # 每一类至多给几个候选
TOTAL_CAP = 10
TALK_TIERS = ("soul", "named")  # 只对有魂、有名的人出开口的选项（路人不抉择，答不了话）
HARM_STATUSES = ("captive", "bound", "unconscious")
HELD = ("bound", "captive")
WARN_TAG = "danger"  # 带这个标签的事实说给当事人听，算示警而不是告知
REASK_AFTER = 2 * TICKS_PER_DAY  # 被回绝的请求、问过的话，两日之内不再给
RECENT_HARM = 2 * TICKS_PER_HOUR  # 「刚吃了亏」「刚动过手」看多久以内的事
WARN_GAP = TICKS_PER_HOUR  # 同一个人一个时辰之内只提醒一次
EVENT_KINDS = ("harmed", "threaten", "captured")
THREAD_ASK_KINDS = ("lead", "deadline", "wish")
# 情境动作的事实句多半带个「了」（「拱手赔了个不是」）；写成劝人去做的事时去掉句中的「了」
_LE = re.compile(r"(?<=[^，。；、：！？\s⟧])了(?=[^，。；、：！？\s结解断却然得无])")


@dataclass
class SpeechCand:
    """一个开口的候选。"""

    family: str
    speech: Speech
    desc: str  # 一句中性的意图，实体写成 ⟦id⟧
    contrib: dict[str, float] = field(default_factory=dict)  # 方向 → 计分表里命中的那一行
    thread_ids: list[str] = field(default_factory=list)
    rank: tuple = ()  # 同一类里的先后（小的在前）


# ---------------------------------------------------------------- 指纹


def _canon(v: Any) -> str:
    if isinstance(v, dict):
        return "(" + ",".join(f"{k}={_canon(v[k])}" for k in sorted(v)) + ")"
    if isinstance(v, list | tuple):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    return str(v)


def ask_key(ask: dict | None) -> str:
    """所求之事的规范串：动词、参数，以及 permit / stop / swap 这类附注；不看描述文字。"""
    if not ask:
        return ""
    rest = {k: v for k, v in ask.items() if k not in ("verb", "params", "desc")}
    return f"{ask.get('verb')}{_canon(dict(ask.get('params') or {}))}" + (_canon(rest) if rest else "")


def speech_signature(sp: Speech) -> str:
    """一句话的规范串（去重、「上回合选过」用），与 ``ActionInst.signature`` 同一用途。"""
    parts = [f"act={sp.act}", "to=" + ",".join(sp.to)]
    if sp.ask_action:
        parts.append("ask=" + ask_key(sp.ask_action))
    if sp.about:
        parts.append(f"about={sp.about}")
    if sp.fact_refs:
        parts.append("facts=" + ",".join(sp.fact_refs))
    if sp.request and sp.act in ("agree", "refuse", "bargain", "tell"):
        parts.append(f"request={sp.request}")
    return "say:" + ";".join(parts)


def plain_desc(sp: Speech) -> str:
    """没另给描述时，一句话的中性描述：对谁、哪种言语行为、要旨。"""
    to = "、".join(refs.ref(t) for t in sp.to) or "在场的人"
    phrase = kspeech.ACT_PHRASE.get(sp.act, "说")
    return f"对{to}{phrase}" + (f"：{sp.gist}" if sp.gist else "")


def speech_pair(sp: Speech) -> tuple[str, str]:
    """（动词，对象）：近三回合选过同一动词与对象的扣分时用。"""
    return f"say.{sp.act}", f"to={sp.to[0]}" if sp.to else ""


# ---------------------------------------------------------------- 情境动作的性质


def _nested(e: dict) -> list[dict]:
    out: list[dict] = []
    chk = e.get("check")
    if isinstance(chk, dict):
        out += list(chk.get("ok") or []) + list(chk.get("fail") or [])
    sch = e.get("schedule")
    if isinstance(sch, list) and len(sch) >= 2 and isinstance(sch[1], list):
        out += sch[1]
    return out


def harm_targets(effs: Any) -> set[str]:
    """一串效果里会伤到、扣住、毒到谁（原样的参数：``$角色位`` 或 id）。判定的两个分支、延后的效果都算。"""
    out: set[str] = set()
    for e in effs or []:
        if not isinstance(e, dict):
            continue
        v = e.get("injure")
        if isinstance(v, list) and v:
            out.add(str(v[0]))
        if "kill" in e:
            v = e["kill"]
            out.add(str(v[0] if isinstance(v, list) and v else v))
        v = e.get("add_status")
        if isinstance(v, list) and len(v) >= 2:
            kind = str(v[1])
            if kind in HARM_STATUSES or kind.startswith("poisoned"):
                out.add(str(v[0]))
        out |= harm_targets(_nested(e))
    return out


def flags_set(effs: Any) -> set[str]:
    out: set[str] = set()
    for e in effs or []:
        if not isinstance(e, dict):
            continue
        v = e.get("set_flag")
        if isinstance(v, list) and v:
            out.add(str(v[0]))
        elif isinstance(v, str):
            out.add(v)
        out |= flags_set(_nested(e))
    return out


def flags_read(pred: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(pred, dict):
        for k, v in pred.items():
            if k == "flag":
                out.add(str(v[0] if isinstance(v, list) and v else v))
            else:
                out |= flags_read(v)
    elif isinstance(pred, list):
        for v in pred:
            out |= flags_read(v)
    return out


def harmless(a: SituationAction) -> bool:
    """这一招不伤人、不扣人、不致命：才拿来劝人。"""
    return not a.lethal and not harm_targets(a.effects)


def sours(effs: Any, roles: dict[str, str], cid: str) -> bool:
    """这一招会让 ``cid`` 对人生出恶感（冲着他来的一招：赶他走、给他难堪）。"""
    for e in effs or []:
        if not isinstance(e, dict):
            continue
        v = e.get("rel")
        if isinstance(v, list) and len(v) >= 4:
            who = str(v[0])
            who = roles.get(who[1:], who) if who.startswith("$") else who
            if who == cid and isinstance(v[3], int | float) and v[3] < 0:
                return True
        if sours(_nested(e), roles, cid):
            return True
    return False


def ends_situation(d: SituationDef, a: SituationAction) -> bool:
    """这一招立的旗标正是情境散场条件里看的：做了它，这场事多半就了结了。"""
    return bool(flags_set(a.effects) & flags_read(d.exit_when))


def move_text(s: SituationState, a: SituationAction, name: str, cid: str) -> str | None:
    """情境动作写成「劝某人去做的事」：取它的描述（没有就取第一句事实句），去掉句首的主语与句中的「了」。

    事实句不以这个人开头的写不成一句劝人的话，返回 None。
    """
    text = a.desc
    if not text:
        for eff in a.effects:
            if isinstance(eff, dict) and isinstance(eff.get("emit"), dict):
                text = str(eff["emit"].get("text", ""))
                break
    filled = refs.fill_roles(text or "", s.roles, cid).strip().rstrip("。")
    head = refs.ref(cid)
    if not filled.startswith(head):
        return None
    body = _LE.sub("", filled[len(head):]).strip("，、 ")
    return body or None


# ---------------------------------------------------------------- 候选


class _Builder:
    def __init__(
        self,
        state: WorldState,
        defs: Defs,
        me: Character,
        so: kspeech.SpeechOptions,
        threads: list[Thread],
        danger_level: int,
        present_last: set[str] | None,
    ) -> None:
        self.state = state
        self.defs = defs
        self.me = me
        self.so = so
        self.acts = set(so.acts)
        self.threads = threads
        self.thread_ids = {t.id for t in threads}
        self.weights = {t.id: t.weight for t in threads}
        self.danger = danger_level
        self.present_last = present_last
        self.hearers = kspeech.audience(state, me)
        self.people = [o for o in self.hearers if o.tier in TALK_TIERS]
        self.people_ids = {o.id for o in self.people}
        self.order = {o.id: i for i, o in enumerate(self.hearers)}
        self.visible = perception.visible_people(state, me)
        self.sits = [s for s in state.situations.values() if s.loc == me.loc and s.kind in defs.situations]
        self.in_sit: dict[str, list[SituationState]] = {}
        for s in self.sits:
            for cid in s.roles.values():
                self.in_sit.setdefault(cid, []).append(s)
        self.hostile = {o.id for o in self.hearers if danger.is_hostile(state, o, me.id)}
        self.held = any(has_status(me, k) for k in HELD)

    # ---- 小件

    def ref(self, eid: str) -> str:
        return refs.ref(eid)

    def safe(self, text: str) -> str:
        return common.safe_text(self.state, self.defs, text)

    def just_arrived(self, cid: str) -> bool:
        return self.present_last is not None and cid not in self.present_last

    def fond_of(self, cid: str) -> bool:
        """玩家对这个人有好感（自己的心思自己知道）。"""
        r = self.me.rel.get(cid)
        return r is not None and r.affinity > 0

    def acquainted(self, cid: str) -> bool:
        return mood.band(self.defs, self.state, self.me.id, cid) in ("familiar", "trusted")

    def controls(self, t: Character, h: Character) -> bool:
        """t 管得着 h 的去留：h 被捆、被押，下手的是 t 或 t 的手下（没写是谁下的手，谁都可以谈）。"""
        srcs = [s.source for s in h.statuses if s.kind in HELD]
        if not srcs:
            return False
        for src in srcs:
            if not src:
                return True
            captor = self.state.chars.get(src)
            if src == t.id or (captor is not None and captor.superior == t.id):
                return True
        return False

    def ev_threads(self, cids: list[str]) -> list[str]:
        """这几个人所在的眼前情境对应的牵挂。"""
        out: list[str] = []
        for cid in cids:
            for s in self.in_sit.get(cid, []):
                tid = f"ev:{s.id}"
                if tid in self.thread_ids and tid not in out:
                    out.append(tid)
        return out

    def ref_threads(self, ents: set[str]) -> list[str]:
        """提到这些人、地、物的牵挂（眼前事除外，那一种只认情境里的人）。"""
        return [t.id for t in self.threads if t.kind != "event" and ents & set(t.refs)]

    def push(self, tids: list[str]) -> float:
        return 10.0 * max((self.weights.get(t, 0) for t in tids), default=0)

    def requested(self, to: str, ask: dict) -> bool:
        """这件事已经向他提过：还开着、还在谈、已经答应；或者两日之内被回绝过。"""
        key = ask_key(ask)
        for r in self.state.requests.values():
            if r.frm != self.me.id or r.to != to or r.kind == "question" or ask_key(r.action) != key:
                continue
            if r.status in ("open", "countered", "agreed"):
                return True
            if r.created > self.state.tick - REASK_AFTER:
                return True
        return False

    def asked(self, to: str, about: str) -> bool:
        for r in self.state.requests.values():
            if r.frm != self.me.id or r.to != to or r.kind != "question":
                continue
            if (r.action.get("params") or {}).get("about") == about and r.created > self.state.tick - REASK_AFTER:
                return True
        return False

    def said(self, to: str, act: str, *, fact: str | None = None, within: int | None = None) -> bool:
        """玩家对他说过这样一句话（只查事件日志的尾巴）。"""
        for ev in reversed(self.state.events_tail):
            if within is not None and ev.tick < self.state.tick - within:
                break
            sp = ev.speech
            if ev.actor != self.me.id or sp is None or sp.act != act or to not in sp.to:
                continue
            if fact is None or fact in sp.fact_refs:
                return True
        return False

    def recently_hurt(self, cid: str, by: str | None = None) -> bool:
        """玩家亲眼看见他刚吃了亏（被打、被吓、被擒）；给了 by 就只算那个人下的手。"""
        for ev in reversed(self.state.events_tail):
            if ev.tick < self.state.tick - RECENT_HARM:
                break
            if ev.witnesses.get(self.me.id) != "saw":
                continue
            for k in ev.kinds:
                if k.get("k") in EVENT_KINDS and k.get("who") == cid and (by is None or k.get("by") == by):
                    return True
        return False

    def pending_harm(self, s: SituationState, role: str) -> str | None:
        """这场情境里，眼下有谁正要对这个角色下手：别的角色有一招性子里会出的、此刻出得了、又会伤到他的。"""
        d = self.defs.situations[s.kind]
        victim = s.roles.get(role)
        for name, b in d.actions.items():
            holder = s.roles.get(b.role)
            if b.role == role or not b.lean or not holder or holder in (victim, self.me.id):
                continue
            hit = harm_targets(b.effects)
            if f"${role}" not in hit and victim not in hit:
                continue
            if actions.build(self.state, self.defs, holder, "setpiece", {"situation": s.id, "name": name}) is None:
                continue
            return holder
        return None

    def threat_to(self, cid: str) -> str | None:
        for s in self.in_sit.get(cid, []):
            for role, holder in s.roles.items():
                if holder == cid and (h := self.pending_harm(s, role)) is not None:
                    return h
        return None

    def plead_act(self, t: Character) -> str:
        """求情用 plead 还是 request：内核只在对方有敌意、扣着人时才认 plead。"""
        return "plead" if "plead" in self.acts and kspeech.can_plead_to(self.state, self.me, t) else "request"

    def person(self, t: Character) -> dict[str, float]:
        return {"人物": 30.0} if t.tier in TALK_TIERS else {}

    # ---- 答复

    def replies(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        views = sorted(self.so.requests, key=lambda v: (-v.request.created, v.request.id))
        for v in views:
            r = v.request
            t = self.state.chars.get(r.frm)
            if r.to != self.me.id or r.status != "open" or t is None or t.id not in self.order:
                continue  # 对方开了条件的：照条件去做是身体动作，不在这里
            allowed = [a for a in v.allowed_replies if a in self.acts]
            contrib = self.person(t)
            if r.kind == "question":
                about = (r.action.get("params") or {}).get("about")
                fid = self.fact_about(about)
                if fid is None or "tell" not in self.acts:
                    continue
                f = self.state.facts[fid]
                sp = Speech(act="tell", to=[t.id], request=r.id, fact_refs=[fid], gist=self.safe(f.text))
                out.append(SpeechCand("reply", sp, f"回答{self.ref(t.id)}的问话", contrib))
                continue
            what = str(r.action.get("desc") or kspeech.ask_phrase(r.action))
            if "agree" in allowed:
                if self.danger >= 2 and t.id in self.hostile:
                    contrib = {**contrib, "自保": 35.0}  # 照办
                sp = Speech(act="agree", to=[t.id], request=r.id, gist="答应了对方所求的事")
                out.append(SpeechCand("reply", sp, f"答应{self.ref(t.id)}：{what}", contrib))
            elif "refuse" in allowed:
                sp = Speech(act="refuse", to=[t.id], request=r.id, stance="cold", gist="不肯答应对方所求的事")
                out.append(SpeechCand("reply", sp, f"回绝{self.ref(t.id)}：{what}", contrib))
        return out

    def fact_about(self, about: Any) -> str | None:
        if not isinstance(about, str) or not about:
            return None
        for fid in self.so.facts:
            f = self.state.facts.get(fid)
            if f is not None and about in refs.refs_in(f.text):
                return fid
        return None

    # ---- 相劝：劝情境里的人出一招他性子里不会出的

    def persuade(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "request" not in self.acts:
            return out
        for s in self.sits:
            d = self.defs.situations[s.kind]
            for name, a in d.actions.items():
                t = self.state.chars.get(s.roles.get(a.role, ""))
                if a.lean or t is None or t.id == self.me.id or t.id not in self.people_ids or not harmless(a):
                    continue
                params = {"situation": s.id, "name": name}
                built = actions.build(self.state, self.defs, t.id, "setpiece", params)
                if built is None or actions.is_legal(self.state, self.defs, built) is None:
                    continue  # 此刻出不了这一招（前提不成立），或撞了他的底线
                what = move_text(s, a, name, t.id)
                if what is None:
                    continue
                if sours(a.effects, s.roles, self.me.id):
                    continue  # 冲着自己来的一招（喝令你离开）：没有人会劝别人这样对付自己
                ask = {"verb": "setpiece", "params": params, "desc": what}
                if self.requested(t.id, ask):
                    continue
                urgent = self.pending_harm(s, a.role) is not None
                sp = Speech(
                    act="request",
                    to=[t.id],
                    whisper=True,
                    gist=self.safe(f"劝对方{what}"),
                    ask_action=ask,
                    leverage={"urgent": True} if urgent else {},  # 有人正要对他下手：这句劝正帮到他的急需
                )
                tids = self.ev_threads([t.id])
                contrib = {"介入": 50.0, **self.person(t)}
                if tids:
                    contrib["推进"] = self.push(tids)
                rank = (0 if urgent else 1, 0 if ends_situation(d, a) else 1, len(out))
                out.append(SpeechCand("persuade", sp, f"低声劝{self.ref(t.id)}{what}", contrib, tids, rank))
        return sorted(out, key=lambda c: c.rank)

    # ---- 示警

    def warn(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "warn" not in self.acts:
            return out
        for t in self.people:
            h = self.threat_to(t.id)
            if h is None or has_status(t, "alerted") or self.said(t.id, "warn", within=WARN_GAP):
                continue
            sp = Speech(act="warn", to=[t.id], whisper=True, gist=self.safe(f"要对方当心{self.ref(h)}"))
            tids = self.ev_threads([t.id])
            contrib = {"介入": 50.0, **self.person(t)}
            if tids:
                contrib["推进"] = self.push(tids)
            out.append(SpeechCand("warn", sp, f"低声提醒{self.ref(t.id)}当心{self.ref(h)}", contrib, tids))
        for fid in self.so.facts:
            f = self.state.facts.get(fid)
            if f is None or WARN_TAG not in f.tags:
                continue
            ents = set(refs.refs_in(f.text))
            for t in self.people:
                if t.id not in ents or t.id in self.hostile or self.told(t, fid):
                    continue
                sp = Speech(act="warn", to=[t.id], fact_refs=[fid], gist=self.safe(f.text))
                tids = self.ref_threads(ents)
                contrib = self.person(t)
                if tids:
                    contrib["推进"] = self.push(tids)
                out.append(SpeechCand("warn", sp, f"提醒{self.ref(t.id)}：{f.text}", contrib, tids))
        return out

    # ---- 求情

    def plead(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        me = self.me
        # 为自己：对方对你有敌意
        if "plead" in self.acts:
            for t in self.people:
                if t.id not in self.hostile:
                    continue
                if self.held and self.controls(t, me):
                    ask: dict[str, Any] = {"verb": "release", "params": {"target": me.id}, "desc": f"放了{self.ref(me.id)}"}
                    desc = f"求{self.ref(t.id)}放了{self.ref(me.id)}"
                else:
                    ask = {
                        "verb": "wait",
                        "params": {"ticks": 4},
                        "stop": True,
                        "desc": f"住手，别再为难{self.ref(me.id)}",
                    }
                    desc = f"求{self.ref(t.id)}手下留情"
                if self.requested(t.id, ask):
                    continue
                sp = Speech(
                    act="plead",
                    to=[t.id],
                    stance="pleading",
                    gist=self.safe(str(ask["desc"])),
                    ask_action=ask,
                    action_class="costly",
                )
                contrib = self.person(t)
                if self.danger >= 2:
                    contrib["自保"] = 35.0  # 求饶
                out.append(SpeechCand("plead", sp, desc, contrib, rank=(0, len(out))))
        # 替别人：他扣着人，或正要对人下手、刚动过手
        if "request" not in self.acts and "plead" not in self.acts:
            return out
        for t in self.people:
            for h in self.visible:
                if h.id in (t.id, me.id):
                    continue
                if self.controls(t, h):
                    captors = {s.source for s in h.statuses if s.kind in HELD}
                    boss = any((c := self.state.chars.get(src or "")) is not None and t.superior == c.id for src in captors)
                    cls = "betray" if boss and t.id not in captors else "costly"  # 手下背着上头放人
                    ask = {"verb": "release", "params": {"target": h.id}, "desc": f"放了{self.ref(h.id)}"}
                elif self.threat_to(h.id) == t.id or self.recently_hurt(h.id, by=t.id):
                    cls = "costly"
                    ask = {
                        "verb": "wait",
                        "params": {"ticks": 4},
                        "stop": True,
                        "desc": f"住手，别再为难{self.ref(h.id)}",
                    }
                else:
                    continue
                if self.requested(t.id, ask):
                    continue
                sp = Speech(
                    act=self.plead_act(t),
                    to=[t.id],
                    stance="pleading",
                    gist=self.safe(str(ask["desc"])),
                    ask_action=ask,
                    action_class=cls,
                )
                tids = self.ev_threads([t.id, h.id]) + self.ref_threads({h.id})
                contrib = self.person(t)
                if self.in_sit.get(t.id) or self.in_sit.get(h.id):
                    contrib["介入"] = 50.0
                if tids:
                    contrib["推进"] = self.push(tids)
                mine = 0 if (self.fond_of(h.id) or self.ref_threads({h.id})) else 1
                desc = f"替{self.ref(h.id)}向{self.ref(t.id)}求情"
                out.append(SpeechCand("plead", sp, desc, contrib, tids, rank=(1, mine, len(out))))
        return sorted(out, key=lambda c: c.rank)

    # ---- 相求：放了你、解药、同行、教你本事

    def requests(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "request" not in self.acts:
            return out
        me = self.me
        P = self.ref(me.id)
        calm = [t for t in self.people if t.id not in self.hostile]
        # 放了你（对你有敌意的人归「求情」）
        if self.held:
            for t in calm:
                ask: dict[str, Any] = {"verb": "release", "params": {"target": me.id}, "desc": f"放了{P}"}
                if not self.controls(t, me) or self.requested(t.id, ask):
                    continue
                sp = Speech(act="request", to=[t.id], gist=self.safe(str(ask["desc"])), ask_action=ask, action_class="costly")
                contrib = {"自保": 35.0 if self.danger >= 2 else 20.0, **self.person(t)}
                out.append(SpeechCand("request", sp, f"求{self.ref(t.id)}放了{P}", contrib, rank=(0, len(out))))
        # 解药：牵挂里有期限，解药就在眼前某人身上，而且玩家知道该找他（牵挂里挂着他或那味解药，或解药就在明处）
        for th in self.threads:
            if not th.id.startswith("dl:"):
                continue
            _, who, kind = th.id.split(":", 2)
            patient = self.state.chars.get(who)
            if patient is None:
                continue
            for t in self.people:
                if t.id == who:
                    continue
                cure = next(
                    (
                        iid
                        for iid in t.inventory
                        if (it := self.state.items.get(iid)) is not None
                        and kind in (it.props.get("cures") or [])
                        and (t.id in th.refs or iid in th.refs or common.item_visible(self.state, self.defs, iid))
                    ),
                    None,
                )
                if cure is None:
                    continue
                if patient.loc == me.loc and perception.on_map(patient):
                    ask = {"verb": "use", "params": {"item": cure, "target": who}, "desc": f"给{self.ref(who)}解药"}
                else:
                    ask = {"verb": "give", "params": {"item": cure, "target": me.id}, "desc": f"把解药交给{P}"}
                if self.requested(t.id, ask):
                    continue
                sp = Speech(
                    act=self.plead_act(t) if t.id in self.hostile else "request",
                    to=[t.id],
                    stance="pleading",
                    gist=self.safe(str(ask["desc"])),
                    ask_action=ask,
                    action_class="costly",
                )
                contrib = {"推进": 10.0 * th.weight, **self.person(t)}
                desc = f"求{self.ref(t.id)}{ask['desc']}"
                out.append(SpeechCand("request", sp, desc, contrib, [th.id], rank=(1, -th.weight, len(out))))
        # 同行：眼前的情境里，管着去留的人正在处置一个你放不下的人，求他让你跟那人一道走
        for t in calm:
            for h in self.visible:
                if h.id in (t.id, me.id) or not (self.controls(t, h) or (self.held and self.controls(t, me))):
                    continue
                if not any(h.id in s.roles.values() for s in self.in_sit.get(t.id, [])):
                    continue
                tids = self.ref_threads({h.id})
                if not (tids or self.fond_of(h.id) or me.following == h.id):
                    continue
                ask = {
                    "verb": "wait",
                    "params": {"ticks": 1},
                    "permit": {"verb": "follow", "target": h.id},
                    "desc": f"让{P}跟{self.ref(h.id)}一道走",
                }
                if self.requested(t.id, ask):
                    continue
                sp = Speech(
                    act="request",
                    to=[t.id],
                    gist=self.safe(str(ask["desc"])),
                    ask_action=ask,
                    action_class="costly" if self.controls(t, h) else "effort",
                )
                tids = tids + self.ev_threads([t.id, h.id])
                contrib = {"介入": 50.0, **self.person(t)}  # 带人走、同行：改变情境里的人的处境
                if tids:
                    contrib["推进"] = self.push(tids)
                desc = f"求{self.ref(t.id)}{ask['desc']}"
                out.append(SpeechCand("request", sp, desc, contrib, tids, rank=(2, len(out))))
        # 求教：听说过的本事；识字只向相熟的人开口。紧张的场面里、被扣着、身处险境时不提这个
        # （内核在紧张的场面里也不许研读、传授）
        quiet = not (actions.tense_for(self.state, me) or self.held or self.danger >= 2)
        for t in calm if quiet else []:
            if self.in_sit.get(t.id):
                continue  # 人家正有事
            arts = [a for a in sorted(t.arts) if a in self.state.known_terms]
            if self.acquainted(t.id):
                arts.append(growth.LITERACY)
            for art in arts:
                if not growth.can_teach(self.state, self.defs, t, me, art):
                    continue
                label = "识字" if art == growth.LITERACY else self.ref(art)
                ask = {"verb": "teach", "params": {"target": me.id, "art": art}, "desc": f"教{P}{label}"}
                if self.requested(t.id, ask):
                    continue
                sp = Speech(act="request", to=[t.id], gist=self.safe(str(ask["desc"])), ask_action=ask)
                tids = self.ref_threads({art})
                contrib = {"成长": 25.0}
                if tids:
                    contrib["推进"] = self.push(tids)
                desc = f"求{self.ref(t.id)}{ask['desc']}"
                out.append(SpeechCand("request", sp, desc, contrib, tids, rank=(3, len(out))))
                break
        return sorted(out, key=lambda c: c.rank)

    # ---- 安慰

    def comfort(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "comfort" not in self.acts:
            return out
        hour = self.state.tick // TICKS_PER_HOUR
        for t in self.people:
            if t.id in self.hostile or not self.fond_of(t.id) or t.comforted.get(self.me.id) == hour:
                continue
            poorly = (
                injury_level(t) >= 1
                or any(has_status(t, k) for k in HELD)
                or any(s.kind.startswith("poisoned") for s in t.statuses)
                or self.recently_hurt(t.id)
            )
            if not poorly:
                continue
            sp = Speech(act="comfort", to=[t.id], stance="warm", gist="宽慰对方几句")
            out.append(SpeechCand("comfort", sp, f"安慰{self.ref(t.id)}几句", self.person(t)))
        return out

    # ---- 打听

    def ask_whom(self, about: str) -> Character | None:
        """向谁打听：先问相熟的，再问有魂的；不看对方知道什么（那是玩家看不见的）。"""
        pool = [
            t
            for t in self.people
            if t.id != about and t.id not in self.hostile and not self.in_sit.get(t.id) and not self.asked(t.id, about)
        ]
        if not pool:
            return None
        return min(pool, key=lambda t: (not self.acquainted(t.id), t.tier != "soul", self.order[t.id]))

    def ask(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "ask" not in self.acts:
            return out
        state = self.state
        seen: set[str] = set()
        here = {o.id for o in self.visible}
        for th in sorted(self.threads, key=lambda t: -t.weight):
            if th.kind not in THREAD_ASK_KINDS:
                continue
            for e in th.refs:
                if e in seen or e == self.me.id or e in here or e == self.me.loc or "#" in e:
                    continue
                if e not in state.chars and e not in state.locations and e not in state.items and e not in self.defs.arts:
                    continue
                if e in state.items and state.items[e].holder == self.me.id:
                    continue
                t = self.ask_whom(e)
                if t is None:
                    continue
                seen.add(e)
                sp = Speech(act="ask", to=[t.id], about=e, gist=self.safe(f"问起{self.ref(e)}"))
                contrib = {"推进": 10.0 * th.weight, **self.person(t)}
                desc = f"向{self.ref(t.id)}打听{self.ref(e)}的事"
                out.append(SpeechCand("ask", sp, desc, contrib, [th.id], rank=(0, -th.weight, len(out))))
                break
        # 不知其名的人：问一问来历
        strangers = [
            t
            for t in self.people
            if t.id not in state.known_terms
            and t.id not in self.hostile
            and not self.in_sit.get(t.id)
            and not self.asked(t.id, t.id)
        ]
        strangers.sort(key=lambda t: (not self.just_arrived(t.id), t.id not in self.me.rel, t.tier != "soul", self.order[t.id]))
        for t in strangers[:1]:
            sp = Speech(act="ask", to=[t.id], about=t.id, gist="问对方的来历")
            out.append(SpeechCand("ask", sp, f"问一问{self.ref(t.id)}的来历", self.person(t), rank=(1, len(out))))
        return sorted(out, key=lambda c: c.rank)

    # ---- 告知

    def told(self, t: Character, fid: str) -> bool:
        """这件事不必再对他说：他本来就知道；是他告诉你的；你对他说过；事发时他就在场。

        「他本来就知道」查的是内核里对方的信念：不这样筛，选项里会满是「把某人自己的事告诉某人」。
        露出去的只有「这件事对他是新闻」，不是任何事实本身。
        """
        if common.believes(t, fid):
            return True
        b = self.me.beliefs.get(fid)
        if b is not None and b.source == f"told:{t.id}":
            return True
        if self.said(t.id, "tell", fact=fid) or self.said(t.id, "warn", fact=fid):
            return True
        if fid.startswith("E") and fid[1:].isdigit():
            eid = int(fid[1:])
            return any(ev.id == eid and t.id in ev.witnesses for ev in self.state.events_tail)
        return False

    def tell(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "tell" not in self.acts:
            return out
        for fid in self.so.facts:
            f = self.state.facts.get(fid)
            if f is None or WARN_TAG in f.tags:
                continue
            ents = refs.refs_in(f.text)
            eset = set(ents)
            for t in self.people:
                if t.id in self.hostile or self.told(t, fid):
                    continue
                # 与他有关：说到了他（不是他自己做的事）；说到与他同在一场情境里的另一方；
                # 或者说到一条也挂着他的牵挂里的人、地、物
                rivals = {cid for s in self.in_sit.get(t.id, []) for cid in s.roles.values()} - {t.id, self.me.id}
                testimony = bool(eset & rivals)
                about_him = t.id in eset and ents[0] != t.id
                tids = [
                    th.id
                    for th in self.threads
                    if th.kind != "event" and t.id in th.refs and eset & (set(th.refs) - {t.id})
                ]
                if not (about_him or testimony or tids):
                    continue
                sp = Speech(act="tell", to=[t.id], fact_refs=[fid], gist=self.safe(f.text))
                contrib = self.person(t)
                if testimony:
                    contrib["介入"] = 50.0  # 作证、报信
                    tids = tids + self.ev_threads([t.id])
                if tids:
                    contrib["推进"] = self.push(tids)
                rank = (-max(contrib.values(), default=0.0), len(out))
                out.append(SpeechCand("tell", sp, f"把一件事告诉{self.ref(t.id)}：{f.text}", contrib, tids, rank))
        return sorted(out, key=lambda c: c.rank)

    # ---- 招呼

    def greet(self) -> list[SpeechCand]:
        out: list[SpeechCand] = []
        if "greet" not in self.acts:
            return out
        fresh = [
            t
            for t in self.people
            if self.me.id not in t.rel and t.id not in self.hostile and not self.in_sit.get(t.id)
        ]
        fresh.sort(
            key=lambda t: (
                not self.just_arrived(t.id),
                t.id not in self.state.known_terms,
                t.tier != "soul",
                self.order[t.id],
            )
        )
        for t in fresh:
            sp = Speech(act="greet", to=[t.id], stance="warm")
            out.append(SpeechCand("greet", sp, f"向{self.ref(t.id)}打个招呼", self.person(t)))
        return out


def sayable(state: WorldState, defs: Defs, sp: Speech, *, acts: set[str] | None = None) -> bool:
    """这句话此刻说得出口（与 ``kernel.speech.process`` 的入门校验同一口径）：言语行为可用、对象听得见、
    不撞说话人的底线；求情要内核认；说的事自己相信。``acts`` 是已经算好的可用言语行为（省一次计算）。"""
    me = state.chars.get(state.player)
    if me is None:
        return False
    if sp.act not in (acts if acts is not None else kspeech.available_acts(state, defs, me)):
        return False
    heard = {o.id: o for o in kspeech.audience(state, me)}
    if not sp.to or any(t not in heard for t in sp.to):
        return False
    if sp.act == "plead" and not kspeech.can_plead_to(state, me, heard[sp.to[0]]):
        return False
    if sp.act == "order" and heard[sp.to[0]].superior != me.id:
        return False
    for fid in sp.fact_refs:
        if not common.believes(me, fid) or kspeech.say_blocked(state, defs, me, sp.act, fact=fid, to=sp.to):
            return False
    return not kspeech.say_blocked(state, defs, me, sp.act, to=sp.to)


def speech_candidates(
    state: WorldState,
    defs: Defs,
    *,
    threads: list[Thread],
    danger_level: int = 0,
    present_last: set[str] | None = None,
    names: list[str] | None = None,
) -> list[SpeechCand]:
    """玩家此刻说得出口、值得一说的话。不问模型，不改状态。

    ``threads``：开着的牵挂；``present_last``：上一回给选项时在场的人（用来认「刚到场」）；
    ``names``：玩家还不知道的专名（描述里露出来的候选不要）。
    """
    me = state.chars.get(state.player)
    if me is None or not perception.on_map(me):
        return []
    so = kspeech.speech_options(state, defs, state.player)
    if not so.acts:
        return []
    b = _Builder(state, defs, me, so, threads, danger_level, present_last)
    out: list[SpeechCand] = []
    seen: set[str] = set()
    for family in (b.replies, b.persuade, b.warn, b.plead, b.requests, b.comfort, b.ask, b.tell, b.greet):
        kept = 0
        for c in family():
            if kept >= FAMILY_CAP:
                break
            sig = speech_signature(c.speech)
            if sig in seen or not sayable(state, defs, c.speech, acts=b.acts):
                continue
            if any(
                common.unresolved_refs(state, defs, text) or common.reveals_unknown_name(state, defs, text, names)
                for text in (c.desc, c.speech.gist)
            ):
                continue
            seen.add(sig)
            out.append(c)
            kept += 1
    return out[:TOTAL_CAP]


__all__ = [
    "SpeechCand",
    "ask_key",
    "plain_desc",
    "speech_candidates",
    "speech_pair",
    "speech_signature",
    "sayable",
]
