"""牵挂（方案 8.4）：玩家视角下还没了结的事，由规则维护，``/牵挂`` 可以查看。

| 种类 | 何时生成 | 何时了结 | 权重 |
| 期限 deadline | 玩家身上有带期限的状态；玩家知道某人的期限 | 状态解除或到期 | 3，剩不到两日时 5 |
| 承诺 promise | 玩家许了诺，或别人对玩家许了诺 | 守诺、失约，或对方身故 | 4 |
| 眼前事 event | 玩家所在的地点有情境在进行 | 情境散场 | 4 |
| 心愿 wish | /心愿（game 经 parser.map_wish 调 add_wish） | 玩家撤销，或判定达成 | 3 |
| 线索 lead | 玩家得知一件指向某地、某人、某物的事 | 到过、见过、拿到，或线索失效 | 2 |

所有改动只经 ``thread`` / ``close_thread`` / ``teller`` 效果。规则生成的牵挂用可预测的 id：
``dl:<人>:<状态>``、``pr:<许诺>``、``ev:<情境实例>``、``lead:<事实>:<实体>``、``wish:<n>``。
开局写在 opening.yaml 里的牵挂照通用规则了结（相关的人都死了；到了 expires；线索的对象已到手）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tianlong.kernel import actions, perception, predicates, refs
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_DAY, Ctx, Event, Thread, WorldState, get_status, is_alive
from tianlong.storyteller import common

WEIGHTS = {"deadline": 3, "deadline_near": 5, "promise": 4, "event": 4, "wish": 3, "lead": 2}
NEAR_DEADLINE = 2 * TICKS_PER_DAY
LEAD_CAP = 8  # 同时挂着的线索至多几条；超出时最早的一条作罢
LEAD_PER_FACT = 2
KIND_LABEL = {"deadline": "期限", "promise": "承诺", "event": "眼前事", "wish": "心愿", "lead": "线索"}
KIND_ORDER = ("deadline", "promise", "event", "wish", "lead")
PERSON_VERBS = ("follow", "give", "show", "help", "release", "examine", "use")
ITEM_VERBS = ("take", "use", "give", "show", "steal", "examine")


# ---------------------------------------------------------------- 期限


def deadline_status(defs: Defs, kind: str) -> bool:
    """哪些带期限的状态算「期限」：到期致死的中毒，以及 world.yaml 里 deadline_statuses 列出的。"""
    extra = defs.world.model_extra or {}
    return kind.startswith("poisoned") or kind in (extra.get("deadline_statuses") or [])


def _status_word(defs: Defs, kind: str) -> str:
    labels = defs.world.labels or {}
    if kind in labels:
        return str(labels[kind])
    return "毒" if kind.startswith("poisoned") else "麻烦"


def _inflicts(it: Any, kind: str) -> bool:
    inf = it.props.get("inflicts") or {}
    if isinstance(inf, dict) and inf.get("status") == kind:
        return True
    for eff in it.props.get("on_use") or []:
        if isinstance(eff, dict) and isinstance(eff.get("add_status"), list) and kind in eff["add_status"][1:2]:
            return True
    return False


def _known_cures(state: WorldState, kind: str) -> list[str]:
    """玩家知道的、与解这个状态有关的东西：知道名字的解药；以及他相信的、提到解药或下毒之物的
    事里说到的人、地、物（「某某的解药在某人身上」）。不拿玩家不知道的解药去排选项。"""
    p = common.player(state)
    cures = {it.id for it in state.items.values() if it.in_world and kind in (it.props.get("cures") or [])}
    poisons = {it.id for it in state.items.values() if _inflicts(it, kind)}
    out: list[str] = [iid for iid in sorted(cures) if iid in state.known_terms]
    if p is None:
        return out
    for fid, f in state.facts.items():
        if not common.believes(p, fid):
            continue
        ids = refs.refs_in(f.text)
        if not set(ids) & (cures | poisons):
            continue
        for eid in ids:
            if eid in poisons or eid == state.player:
                continue
            if eid in state.chars or eid in state.locations or eid in state.items:
                out.append(eid)
    return list(dict.fromkeys(out))


def _player_knows_deadline(state: WorldState, cid: str, kind: str, events: Iterable[Event]) -> bool:
    """玩家知不知道某人身上的期限：亲眼见过（或听说过）他中毒那件事。"""
    p = common.player(state)
    if p is None:
        return False
    want = "poisoned" if kind.startswith("poisoned") else kind
    for ev in list(events) + list(state.events_tail):
        if not any(k.get("k") == want and k.get("who") == cid for k in ev.kinds):
            continue
        if ev.witnesses.get(state.player) == "saw" or common.believes(p, f"E{ev.id}"):
            return True
    return False


def _deadline_wants(state: WorldState, defs: Defs, events: Iterable[Event], want: dict[str, dict]) -> None:
    now = state.tick
    evs = list(events)
    for c in state.chars.values():
        if not is_alive(c):
            continue
        for s in c.statuses:
            if s.until is None or s.until <= now or not deadline_status(defs, s.kind):
                continue
            tid = f"dl:{c.id}:{s.kind}"
            existing = state.threads.get(tid)
            mine = c.id == state.player
            if not mine and not (existing is not None and not existing.closed):
                if not _player_knows_deadline(state, c.id, s.kind, evs):
                    continue
            word = _status_word(defs, s.kind)
            when = common.clock(s.until)
            src = s.source if s.source in state.chars and s.source != c.id else None
            if mine:
                text = f"身上的{word}到{when}就要发作"
                if src:
                    text = f"身上中的{word}（⟦{src}⟧下的）到{when}就要发作"
            else:
                text = f"⟦{c.id}⟧身上的{word}到{when}就要发作"
            ref_ids = [c.id] + ([src] if src else []) + _known_cures(state, s.kind)
            near = s.until - now < NEAR_DEADLINE
            want[tid] = {
                "id": tid,
                "kind": "deadline",
                "text": text,
                "refs": list(dict.fromkeys(ref_ids)),
                "weight": WEIGHTS["deadline_near"] if near else WEIGHTS["deadline"],
                "expires": s.until,
            }


def _deadline_closed(state: WorldState, defs: Defs, t: Thread) -> bool:
    _, cid, kind = t.id.split(":", 2)
    c = state.chars.get(cid)
    if c is None or not is_alive(c):
        return True
    s = get_status(c, kind)
    return s is None or (s.until is not None and s.until <= state.tick)


# ---------------------------------------------------------------- 承诺


def _promise_wants(state: WorldState, want: dict[str, dict]) -> None:
    me = state.player
    for p in state.promises.values():
        if p.status != "open" or me not in (p.frm, p.to) or p.frm == p.to:
            continue
        other = p.to if p.frm == me else p.frm
        o = state.chars.get(other)
        if o is None or not is_alive(o):
            continue
        what = p.text or "那件事"
        text = f"你答应了⟦{other}⟧：{what}" if p.frm == me else f"⟦{other}⟧答应了你：{what}"
        tid = f"pr:{p.id}"
        want[tid] = {
            "id": tid,
            "kind": "promise",
            "text": text,
            "refs": [other],
            "weight": WEIGHTS["promise"],
            "expires": p.deadline,
        }


def _promise_closed(state: WorldState, t: Thread) -> bool:
    pid = t.id.split(":", 1)[1]
    p = state.promises.get(pid)
    if p is None or p.status != "open":
        return True
    other = p.to if p.frm == state.player else p.frm
    o = state.chars.get(other)
    return o is None or not is_alive(o)


# ---------------------------------------------------------------- 眼前事


def _event_text(state: WorldState, defs: Defs, sid: str) -> tuple[str, list[str]]:
    s = state.situations[sid]
    d = defs.situations.get(s.kind)
    people = [cid for cid in dict.fromkeys(s.roles.values()) if cid != state.player]
    title = (d.model_extra or {}).get("title") if d is not None else None
    if title:
        return refs.fill_roles(str(title), s.roles), people
    if state.player in s.roles.values():
        if people:
            return f"你和⟦{people[0]}⟧之间的事还没完", people
        return "你卷进的那件事还没完", people
    if len(people) >= 2:
        return f"眼前⟦{people[0]}⟧和⟦{people[1]}⟧那边正有事", people
    if people:
        return f"眼前⟦{people[0]}⟧那边正有事", people
    return "眼前正有事", people


def _event_wants(state: WorldState, defs: Defs, want: dict[str, dict]) -> None:
    p = common.player(state)
    if p is None or not perception.on_map(p):
        return
    for s in state.situations.values():
        if s.loc != p.loc:
            continue
        text, people = _event_text(state, defs, s.id)
        tid = f"ev:{s.id}"
        want[tid] = {"id": tid, "kind": "event", "text": text, "refs": people, "weight": WEIGHTS["event"]}


# ---------------------------------------------------------------- 线索


def _lead_target_kind(state: WorldState, eid: str) -> str | None:
    if eid in state.chars:
        return "char"
    if eid in state.locations:
        return "loc"
    if eid in state.items:
        return "item"
    return None


def lead_reached(state: WorldState, eid: str) -> bool:
    """线索的对象到了手：到过那地方、见到那人、拿到那东西；或者线索已经失效（人死了、东西没了）。"""
    p = common.player(state)
    if p is None:
        return True
    kind = _lead_target_kind(state, eid)
    if kind == "loc":
        return p.loc == eid or eid in state.visited
    if kind == "char":
        c = state.chars[eid]
        if not is_alive(c) or not c.onstage:
            return True
        return eid in common.visible_people_ids(state)
    if kind == "item":
        it = state.items[eid]
        return not it.in_world or it.holder == p.id
    return True


def _lead_effects(state: WorldState, defs: Defs, want: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """新相信的事实里指向某地、某人、某物的，各生成一条线索。返回（效果，处理过的事实）。"""
    p = common.player(state)
    first = "lead_facts" not in state.storyteller
    seen = list(common.teller(state, "lead_facts", []))
    if p is None:
        return [], seen
    done = set(seen)
    effs: list[dict] = []
    # 已经挂在期限上的人和物（「解药在某人身上」）不再另生线索
    covered = {r for t in state.threads.values() if t.kind == "deadline" and not t.closed for r in t.refs}
    covered |= {r for d in want.values() if d["kind"] == "deadline" for r in d["refs"]}
    for fid in sorted(p.beliefs):
        if fid in done or not common.believes(p, fid) or fid not in state.facts:
            continue
        seen.append(fid)
        if first:
            continue  # 开局就知道的事不算「新得知」
        f = state.facts[fid]
        if set(refs.refs_in(f.text)) & covered:
            continue
        n = 0
        for eid in refs.refs_in(f.text):
            if n >= LEAD_PER_FACT:
                break
            if eid == state.player or _lead_target_kind(state, eid) is None or lead_reached(state, eid):
                continue
            tid = f"lead:{fid}:{eid}"
            if tid in state.threads:
                continue
            effs.append(
                {
                    "thread": {
                        "id": tid,
                        "kind": "lead",
                        "text": f"关于⟦{eid}⟧：{f.text}",
                        "refs": [eid],
                        "weight": WEIGHTS["lead"],
                    }
                }
            )
            n += 1
    return effs, seen


# ---------------------------------------------------------------- 心愿


def _wish_done(state: WorldState, defs: Defs, t: Thread) -> bool:
    p = common.player(state)
    if p is None:
        return False
    cond = common.teller(state, "wish_done", {}).get(t.id)
    if cond is not None:
        try:
            return predicates.holds(state, defs, cond, Ctx(self_id=state.player))
        except predicates.KernelError:
            return False
    for eid in t.refs:
        if eid in state.items:
            if state.items[eid].holder == p.id:
                return True
        elif eid in defs.arts:
            if p.arts.get(eid, 0) >= 1:
                return True
    return False


def add_wish(state: WorldState, defs: Defs, thread_arg: dict[str, Any]) -> list[Event]:
    """玩家用 /心愿 写下的一句话（parser.map_wish 已把它对到人、物、本事上）。

    ``thread_arg``：``{text, refs, weight?, id?, done_when?}``；``done_when`` 是一个谓词（self 指玩家），
    成立即算达成；不写时，refs 里的东西到手、本事学会即算达成，人不自动判定。
    """
    n = 1 + sum(1 for tid in state.threads if tid.startswith("wish:"))
    tid = str(thread_arg.get("id") or f"wish:{n}")
    effs: list[dict] = [
        {
            "thread": {
                "id": tid,
                "kind": "wish",
                "text": str(thread_arg.get("text", "")),
                "refs": list(thread_arg.get("refs") or []),
                "weight": int(thread_arg.get("weight", WEIGHTS["wish"])),
                "expires": thread_arg.get("expires"),
            }
        }
    ]
    if thread_arg.get("done_when") is not None:
        conds = common.teller(state, "wish_done", {})
        conds[tid] = thread_arg["done_when"]
        effs.append({"teller": ["wish_done", conds]})
    return common.book(state, defs, effs)


def remove_wish(state: WorldState, defs: Defs, tid: str) -> list[Event]:
    """玩家撤销一条心愿。"""
    if tid not in state.threads:
        return []
    return common.book(state, defs, [{"close_thread": tid}])


# ---------------------------------------------------------------- 更新


def _generic_closed(state: WorldState, t: Thread) -> bool:
    """不是规则生成的牵挂（开局、机会牌给的）：相关的人都死了，或过了 expires；线索照线索的规则。"""
    if t.kind == "lead":
        return bool(t.refs) and lead_reached(state, t.refs[0])
    if t.expires is not None and t.expires < state.tick:
        return True
    people = [r for r in t.refs if r in state.chars]
    return bool(people) and all(not is_alive(state.chars[r]) for r in people)


def _differs(t: Thread, d: dict) -> bool:
    return (
        t.closed
        or t.text != d["text"]
        or t.weight != d["weight"]
        or t.expires != d.get("expires")
        or list(t.refs) != list(d.get("refs") or [])
    )


def update(state: WorldState, defs: Defs, events: Iterable[Event] = ()) -> list[Event]:
    """按 8.4 的规则生成、更新、了结牵挂。每回合（或每次世界推进之后）调用一次。"""
    if common.player(state) is None:
        return []
    want: dict[str, dict] = {}
    _deadline_wants(state, defs, events, want)
    _promise_wants(state, want)
    _event_wants(state, defs, want)
    effs: list[dict] = []
    # 了结
    for t in list(state.threads.values()):
        if t.closed or t.id in want:
            continue
        prefix = t.id.split(":", 1)[0] if ":" in t.id else ""
        if prefix == "dl":
            done = _deadline_closed(state, defs, t)
        elif prefix == "pr":
            done = _promise_closed(state, t)
        elif prefix == "ev":
            done = t.id[3:] not in state.situations
        elif t.kind == "wish":
            done = _wish_done(state, defs, t)
        else:
            done = _generic_closed(state, t)
        if done:
            effs.append({"close_thread": t.id})
    # 生成与更新
    for tid, d in want.items():
        t = state.threads.get(tid)
        if t is None or _differs(t, d):
            effs.append({"thread": d})
    lead_effs, seen = _lead_effects(state, defs, want)
    effs += lead_effs
    if "lead_facts" not in state.storyteller or seen != common.teller(state, "lead_facts", []):
        effs.append({"teller": ["lead_facts", seen]})
    # 线索太多时，最早的作罢
    closing = {e["close_thread"] for e in effs if "close_thread" in e}
    open_leads = [t.id for t in state.threads.values() if t.kind == "lead" and not t.closed and t.id not in closing]
    open_leads += [e["thread"]["id"] for e in lead_effs]
    for tid in open_leads[: max(0, len(open_leads) - LEAD_CAP)]:
        if tid in state.threads:
            effs.append({"close_thread": tid})
        else:
            effs = [e for e in effs if not ("thread" in e and e["thread"]["id"] == tid)]
    return common.book(state, defs, effs)


# ---------------------------------------------------------------- 查询


def open_threads(state: WorldState) -> list[Thread]:
    return [t for t in state.threads.values() if not t.closed]


def signature(state: WorldState) -> list[str]:
    """牵挂的指纹（停滞检测用）：开着的牵挂 id。"""
    return sorted(t.id for t in open_threads(state))


class Matcher:
    """一个动作推进了哪些牵挂（8.4 计分表「推进一条牵挂」）。按回合预先算好各条牵挂要去的地方。"""

    def __init__(self, state: WorldState, defs: Defs) -> None:
        self.state = state
        self.defs = defs
        self.threads = open_threads(state)
        p = common.player(state)
        self.here = p.loc if p is not None else None
        self.visible = set(common.visible_people_ids(state))
        self.following = p.following if p is not None else None
        self._hop: dict[str, str | None] = {}

    def hop(self, loc: str) -> str | None:
        """去某地最短路上的第一站（只走玩家走得了的出口）。"""
        if loc not in self._hop:
            route = (
                actions.path(self.state, self.defs, self.state.player, self.here, loc)
                if self.here is not None and loc != self.here
                else []
            )
            self._hop[loc] = route[0].to if route else None
        return self._hop[loc]

    def _situation_of(self, t: Thread) -> str | None:
        return t.id[3:] if t.id.startswith("ev:") else None

    def advances(self, a: ActionInst, t: Thread) -> bool:
        p = a.params
        sid = self._situation_of(t)
        if sid is not None:
            if a.verb == "setpiece" and p.get("situation") == sid:
                return True
            if "intervene" in a.tags and p.get("target") in t.refs:
                return True
        if t.id.startswith("pr:"):
            pr = self.state.promises.get(t.id[3:])
            if pr is not None and pr.frm == self.state.player and pr.do:
                if actions.matches_do(a, pr.do, Ctx(self_id=self.state.player), state=self.state):
                    return True
        for eid in t.refs:
            if eid == self.state.player:
                continue
            if eid in self.state.chars:
                if p.get("target") == eid and a.verb in PERSON_VERBS and eid in self.visible:
                    if a.verb == "examine" and f"char:{eid}" in self.state.examined:
                        continue
                    if a.verb == "follow" and self.following == eid:
                        continue  # 已经跟着他：这个动作是「不再跟着」
                    return True
            elif eid in self.state.locations:
                if a.verb in ("go", "flee") and p.get("exit") in (eid, self.hop(eid)):
                    return True
            elif eid in self.state.items:
                if p.get("item") == eid and a.verb in ITEM_VERBS:
                    if a.verb == "examine" and f"item:{eid}" in self.state.examined:
                        continue
                    return True
            elif "#" in eid:
                loc, _, feat = eid.partition("#")
                if loc == self.here and p.get("feature") == feat and a.verb in ("examine", "search"):
                    return True
        return False

    def tied(self, a: ActionInst) -> list[Thread]:
        return [t for t in self.threads if self.advances(a, t)]


# ---------------------------------------------------------------- /牵挂


def render_threads(state: WorldState, defs: Defs) -> str:
    """/牵挂 的画面：开着的牵挂按种类分组，带期限的写明还剩多久；末尾附近来了结的几条。"""
    opened = open_threads(state)
    lines: list[str] = ["【牵挂】"]
    if not opened:
        lines.append("眼下没有什么放不下的事。")
    for kind in KIND_ORDER:
        group = sorted((t for t in opened if t.kind == kind), key=lambda t: -t.weight)
        if not group:
            continue
        lines.append(f"{KIND_LABEL[kind]}：")
        for t in group:
            tail = f"（{common.days_left_text(state, t.expires)}）" if t.expires is not None else ""
            lines.append(f"  · {common.pv(state, defs, t.text)}{tail}")
    closed = [t for t in state.threads.values() if t.closed][-3:]
    if closed:
        lines.append("近来了结的：")
        for t in closed:
            lines.append(f"  · {common.pv(state, defs, t.text)}")
    return "\n".join(lines)
