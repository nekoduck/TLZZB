"""防作弊集与拒绝集的断言语言（evals/README.md 5.2）与归类集的动作比对（2.3）。

``evaluate(defs, before, after, events, assertion) -> (成立与否, 说明)``；不认识的断言抛 ValueError。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tianlong.kernel import predicates
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, Event, Request, WorldState

OPS = (">=", "<=", ">", "<", "==", "!=")

ASK_ACTION = {
    # asks.type → 内核 Request.action（README 2.4）；requester 是玩家
    "release": lambda a: {"verb": "release", "params": {"target": a["target"]}},
    "give_item": lambda a: {"verb": "give", "params": {"item": a["item"], "target": "player"}},
    "show_item": lambda a: {"verb": "show", "params": {"item": a["item"], "target": "player"}},
    "teach_art": lambda a: {"verb": "teach", "params": {"art": a.get("art"), "target": "player"}},
    "take_to": lambda a: {"verb": "go", "params": {"exit": a["place"]}},
    "tell_about": lambda a: {"verb": "say", "params": {"act": "tell", "fact": a.get("fact")}},
    "come_along": lambda a: {"verb": "follow", "params": {"target": "player"}},
    "stop_doing": lambda a: {"verb": "wait", "params": {}},
    "take_place_of": lambda a: {"verb": "release", "params": {"target": a["target"]}},
    "let_pass": lambda a: {"verb": "wait", "params": {}},
    "accompany": lambda a: {"verb": "wait", "params": {}},
    "help": lambda a: {"verb": "help", "params": {"target": a["target"]}},
    "cure": lambda a: {"verb": "use", "params": {"item": a["item"], "target": a["target"]}},
    "protect": lambda a: {"verb": "block", "params": {"target": a["target"]}},
    "other": lambda a: {"verb": "wait", "params": {}},
}


def resolve_path(st: Any, path: str) -> Any:
    """点分路径，从 WorldState 走到字段；字典里没有的键取 None。"""
    cur: Any = st
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            raise ValueError(f"bad path {path!r} at {part!r}")
    return cur


def compare(a: float, op: str, b: float) -> bool:
    if op not in OPS:
        raise ValueError(f"unknown operator {op!r}")
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "!=": a != b}[op]


def _plain(x: Any) -> Any:
    return x.model_dump() if hasattr(x, "model_dump") else x


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return {k: _plain(v) for k, v in a.items()} == {k: _plain(v) for k, v in b.items()}
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return [_plain(x) for x in a] == [_plain(x) for x in b]
    return _plain(a) == _plain(b)


def _mood(st: WorldState, cid: str, name: str) -> float:
    c = st.chars[cid]
    m = c.mood
    if isinstance(m, Mapping):
        return float(m.get(name, 0) or 0)
    return float(getattr(m, name, 0) or 0)


def _rel(st: WorldState, a: str, b: str, fld: str) -> float:
    r = st.chars[a].rel.get(b)
    if r is None:
        return 0.0
    if isinstance(r, Mapping):
        return float(r.get(fld, 0) or 0)
    return float(getattr(r, fld, 0) or 0)


def _belief(st: WorldState, cid: str, fid: str) -> float:
    b = st.chars[cid].beliefs.get(fid)
    return float(b.confidence) if b is not None else 0.0


def new_events(before: WorldState, after: WorldState) -> list[Event]:
    """这几回合新记下的事件：after.events_tail 里 id >= before.next_event_id 的那些。"""
    return [e for e in after.events_tail if e.id >= before.next_event_id]


def _req_no(rid: str) -> tuple[int, str]:
    digits = "".join(ch for ch in rid if ch.isdigit())
    return (int(digits) if digits else -1, rid)


def find_request(before: WorldState, after: WorldState, to: str, frm: str | None = None) -> Request | None:
    """README 5.2：frm → to、在这几回合里新建或有改动的请求，取编号最大的那条。"""
    frm = frm or after.player
    hits: list[Request] = []
    for rid, r in after.requests.items():
        if r.frm != frm or r.to != to:
            continue
        old = before.requests.get(rid)
        if old is None or old.model_dump() != r.model_dump():
            hits.append(r)
    return max(hits, key=lambda r: _req_no(r.id)) if hits else None


def check_request(req: Request | None, arg: Mapping[str, Any]) -> tuple[bool, str]:
    if arg.get("absent"):
        return (req is None, "" if req is None else f"request {req.id} exists ({req.verdict}/{req.status})")
    if req is None:
        return False, "no such request (the input was not parsed as a request to that person)"
    bad: list[str] = []
    if "verdict_in" in arg and req.verdict not in arg["verdict_in"]:
        bad.append(f"verdict {req.verdict} not in {list(arg['verdict_in'])}")
    if "verdict_not_in" in arg and req.verdict in arg["verdict_not_in"]:
        bad.append(f"verdict {req.verdict} in {list(arg['verdict_not_in'])}")
    if "status_in" in arg and req.status not in arg["status_in"]:
        bad.append(f"status {req.status} not in {list(arg['status_in'])}")
    if "status_not_in" in arg and req.status in arg["status_not_in"]:
        bad.append(f"status {req.status} in {list(arg['status_not_in'])}")
    if "attempts_ge" in arg and req.attempts < int(arg["attempts_ge"]):
        bad.append(f"attempts {req.attempts} < {arg['attempts_ge']}")
    if "evidence_max" in arg and len(req.evidence) > int(arg["evidence_max"]):
        bad.append(f"evidence {req.evidence} longer than {arg['evidence_max']}")
    return (not bad, "; ".join(bad))


def player_claims(events: Sequence[Event], player: str, ref: str | None = None) -> list[Any]:
    out: list[Any] = []
    for e in events:
        if e.actor != player or e.speech is None:
            continue
        for c in e.speech.claims:
            if ref is None or c.ref == ref:
                out.append(c)
    return out


def _event_matches(e: Event, arg: Mapping[str, Any]) -> bool:
    if "actor" in arg and e.actor != arg["actor"]:
        return False
    if "verb" in arg and e.verb != arg["verb"]:
        return False
    if "verb_in" in arg and e.verb not in arg["verb_in"]:
        return False
    if "outcome" in arg and e.outcome != arg["outcome"]:
        return False
    return True


def evaluate(defs: Defs, before: WorldState, after: WorldState, events: Sequence[Event],
             assertion: Mapping[str, Any]) -> tuple[bool, str]:
    """一条断言（只有一个键的映射）是否成立；第二项是不成立时的说明。"""
    if not isinstance(assertion, Mapping) or len(assertion) != 1:
        raise ValueError(f"an assertion is a one-key mapping, got {assertion!r}")
    key, arg = next(iter(assertion.items()))
    if key == "holds":
        ok = predicates.holds(after, defs, arg, Ctx(self_id=after.player))
        return ok, "" if ok else f"{arg} does not hold"
    if key == "unchanged":
        a, b = resolve_path(before, arg), resolve_path(after, arg)
        ok = _same(a, b)
        return ok, "" if ok else f"{arg}: {_plain(a)!r} → {_plain(b)!r}"
    if key == "tick_delta":
        op, n = arg
        d = after.tick - before.tick
        return compare(d, op, n), f"tick delta {d}"
    if key == "len_delta":
        path, op, n = arg
        d = len(resolve_path(after, path) or []) - len(resolve_path(before, path) or [])
        return compare(d, op, n), f"len delta of {path} = {d}"
    if key == "request":
        req = find_request(before, after, arg["to"], arg.get("from"))
        return check_request(req, arg)
    if key == "claim":
        cl = player_claims(events, after.player, arg.get("ref"))
        verdicts = [c.verdict for c in cl]
        ok = True
        if "any_in" in arg and not any(v in arg["any_in"] for v in verdicts):
            ok = False
        if "none_in" in arg and any(v in arg["none_in"] for v in verdicts):
            ok = False
        return ok, f"claim verdicts {verdicts}"
    if key == "belief":
        cid, fid, op, x = arg
        v = _belief(after, cid, fid)
        return compare(v, op, x), f"belief {cid}.{fid} = {v:.2f}"
    if key == "mood_delta":
        cid, name, op, n = arg
        d = _mood(after, cid, name) - _mood(before, cid, name)
        return compare(d, op, n), f"{cid}.{name} delta {d:g}"
    if key == "rel_delta":
        a, b, fld, op, n = arg
        d = _rel(after, a, b, fld) - _rel(before, a, b, fld)
        return compare(d, op, n), f"{a}→{b}.{fld} delta {d:g}"
    if key == "event":
        found = [e for e in events if _event_matches(e, arg)]
        if arg.get("absent"):
            return (not found, "" if not found else f"event present: #{found[0].id} {found[0].verb}")
        return (bool(found), "" if found else "no matching event")
    raise ValueError(f"unknown assertion {key!r}")


def evaluate_all(defs: Defs, before: WorldState, after: WorldState, events: Sequence[Event],
                 expect: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """逐条检查；返回不成立的那些 {assertion, detail}。"""
    failed: list[dict[str, Any]] = []
    for a in expect:
        try:
            ok, detail = evaluate(defs, before, after, events, a)
        except Exception as e:  # noqa: BLE001 — 断言写错也算不通过，说明原因
            ok, detail = False, f"{type(e).__name__}: {e}"
        if not ok:
            failed.append({"assertion": dict(a), "detail": detail})
    return failed


# ---------------------------------------------------------------- 归类集：动作比对（README 2.3）


def action_matches(verb: str | None, params: Mapping[str, Any], g: Mapping[str, Any], loc: str) -> bool:
    """一个动作（verb 加 ActionInst.params）是否对得上标注的 verb、target、target2、art、name。"""
    if verb != g.get("verb"):
        return False
    p = params
    t, t2 = g.get("target"), g.get("target2")
    feat = f"{loc}#{p['feature']}" if p.get("feature") else None
    if verb in ("go", "flee"):
        ok = p.get("exit") == t
    elif verb in ("follow", "attack", "block", "restrain", "release", "help"):
        ok = p.get("target") == t
    elif verb == "push":
        ok = t in (p.get("target"), feat)
    elif verb in ("hide", "search"):
        ok = feat == t
    elif verb == "examine":
        ok = t in (p.get("target"), p.get("item"), feat)
    elif verb in ("take", "drop"):
        ok = p.get("item") == t
    elif verb in ("use", "give", "show", "steal"):
        ok = p.get("item") == t and p.get("target") == t2
    elif verb == "study":
        ok = t in (p.get("item"), p.get("target"))
    elif verb == "teach":
        ok = p.get("target") == t
    elif verb == "setpiece":
        ok = p.get("name") == g.get("name")
    else:  # look listen wait rest practice struggle
        ok = True
    if ok and g.get("art") is not None:
        ok = p.get("art") == g["art"]
    return bool(ok)
