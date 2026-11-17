"""驱动真的回合主循环（game.Game）的两个集：防作弊集与拒绝集（方案 11.5、README 第 5、6 节）。

每条：搭场景 → 开一局 → 依次把输入走完整回合 → 用断言语言（assertions.py）检查前后的世界状态。
两遍：Mock 模型（M3 的闸，必须全过）；真 Jev 只管 J1 归类、其余用规则（看真模型会不会把作弊的话归错）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tianlong import parser
from tianlong.config import Settings
from tianlong.evalrun import assertions, scoring
from tianlong.evalrun.common import EvalConfig, apply_setup, frac, gate, gather_limited, make_result, table
from tianlong.game import Game
from tianlong.kernel.state import Event, WorldState
from tianlong.parser import intake
from tianlong.ui.events import Options, Text

WAIT_INPUT = "等一等"
RULE_ROUTES = ("decide_soul", "decide_named", "replan", "options", "check", "cards", "narrate", "phrase")
REAL_J1_TIMEOUT_S = 8.0
PASS_LABEL = {"mock": "Mock 模型", "jev": "真 Jev 归类（J1），其余用规则", "mock_rules": "Mock 模型，归类只靠规则猜测",
              "mock_labeled": "Mock 模型，归类照条目的标注"}


def game_settings(cfg: EvalConfig, tmp: Path, *, real_j1: bool = False) -> Settings:
    """一局评测用的配置：日志与存档写进临时目录；``real_j1`` 时只有归类走真 Jev，其余一律规则与模板。"""
    s = cfg.settings.model_copy(deep=True)
    s.root = cfg.settings.root
    s.paths.content = str(cfg.settings.path("content"))
    s.paths.runs = str(tmp / "runs")
    s.paths.saves = str(tmp / "saves")
    if cfg.model:
        s.jev.model = cfg.model
    # 这几遍量的都是 J1 一路（Mock 的规则猜测、照标注作答、真 J1），不随 config 里的默认路由走。
    s.route.classify = "jev_first"
    if real_j1:
        for k in RULE_ROUTES:
            setattr(s.route, k, "rules_only")
        s.jev.timeout_s = max(float(s.jev.timeout_s), REAL_J1_TIMEOUT_S)
        s.limits.session_budget_usd = 0.0
    return s


@dataclass
class Played:
    before: WorldState
    after: WorldState
    events: list[Event]
    turns: list[dict[str, Any]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


async def open_game(settings: Settings, setup: Sequence[dict] | None, *, mock: bool, run_id: str, seed: int = 0
                    ) -> Game:
    """开一局，在开局状态上执行 setup（经内核的效果），再走开场；返回可以接着 turn() 的 Game。"""
    g = await Game.new(settings, seed=seed, mock=mock, run_id=run_id)
    apply_setup(g.state, g.defs, setup)
    async for _ in g.start():
        pass
    return g


async def send(g: Game, raw: str) -> dict[str, Any]:
    """走一回合；返回这一屏的叙述、选项与记账。"""
    evs = [ev async for ev in g.turn(raw)]
    rec = g.last_turn
    return {
        "raw": raw,
        "kind": rec.kind if rec is not None else "",
        "notes": list(rec.notes) if rec is not None else [],
        "text": "".join(e.piece for e in evs if isinstance(e, Text)),
        "options": next((list(e.items) for e in evs if isinstance(e, Options)), []),
        "first_text_s": rec.first_text_s if rec is not None else None,
        "events": list(rec.events) if rec is not None else [],
        "ui": evs,
    }


def collect_events(before: WorldState, g: Game, turns: Sequence[Mapping[str, Any]]) -> list[Event]:
    seen: dict[int, Event] = {e.id: e for e in assertions.new_events(before, g.state)}
    for t in turns:
        for e in t.get("events") or []:
            if e.id >= before.next_event_id:
                seen.setdefault(e.id, e)
    return [seen[k] for k in sorted(seen)]


async def play(cfg: EvalConfig, setup: Sequence[dict] | None, inputs: Sequence[str], *, real_j1: bool, run_id: str,
               extra_wait: Any = None, prepare: Any = None) -> Played:
    """README 5.1 的跑法。``extra_wait(before, game, events)`` 返回真时再多走一回合「等一等」（拒绝集看下一回合的答复）；
    ``prepare(game)`` 在开场之后、第一回合之前调用（例如给 Mock 的 J1 换上按标注作答的提示）。"""
    with tempfile.TemporaryDirectory(prefix="tianlong-eval-") as tmp:
        settings = game_settings(cfg, Path(tmp), real_j1=real_j1)
        g = await open_game(settings, setup, mock=not real_j1, run_id=run_id)
        try:
            if prepare is not None:
                prepare(g)
            before = g.state.model_copy(deep=True)
            turns: list[dict[str, Any]] = []
            for raw in inputs:
                turns.append(await send(g, raw))
            if extra_wait is not None and g.state.ended is None and extra_wait(before, g, collect_events(before, g, turns)):
                turns.append(await send(g, WAIT_INPUT))
            events = collect_events(before, g, turns)
            after = g.state.model_copy(deep=True)
            slim = [{k: v for k, v in t.items() if k in ("raw", "kind", "notes", "text")} for t in turns]
            return Played(before, after, events, slim, after.violations[len(before.violations):])
        finally:
            await g.aclose()


def request_info(req: Any) -> dict[str, Any] | None:
    if req is None:
        return None
    return {"id": req.id, "action": req.action, "action_class": req.action_class, "verdict": req.verdict,
            "score": req.score, "status": req.status, "attempts": req.attempts, "evidence": list(req.evidence)}


# ---------------------------------------------------------------- 防作弊集


async def anticheat_item(cfg: EvalConfig, item: Mapping[str, Any], *, real_j1: bool) -> dict[str, Any]:
    rec: dict[str, Any] = {"id": item["id"], "category": item["category"], "passed": False, "failed": [], "turns": []}
    try:
        p = await play(cfg, cfg.scene_setup(item["scene"]), list(item["inputs"]), real_j1=real_j1,
                       run_id=f"anticheat-{item['id']}")
    except Exception as e:  # noqa: BLE001 — 一条跑崩了记下来，其余照跑
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    defs = cfg.world()[0]
    rec["failed"] = assertions.evaluate_all(defs, p.before, p.after, p.events, item["expect"])
    rec["passed"] = not rec["failed"]
    rec["turns"] = p.turns
    rec["violations"] = p.violations
    return rec


def by_category(recs: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    out: dict[str, list[bool]] = {}
    for r in recs:
        out.setdefault(str(r["category"]), []).append(bool(r["passed"]))
    return {k: (sum(v), len(v)) for k, v in out.items()}


def _failure_rows(recs: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    rows = []
    for r in recs:
        if r["passed"]:
            continue
        why = r.get("error") or "；".join(f"{f['assertion']} → {f['detail']}" for f in r["failed"])
        parse = "；".join(n for t in r.get("turns") or [] for n in t.get("notes") or [] if n.startswith("parse"))
        req = r.get("request")
        if req:
            a = req["action"]
            parse += f"；请求 {a.get('verb')} {a.get('params')}，{req['action_class']}，{req['score']} 分 → {req['verdict']}"
        rows.append([r["id"], r.get("category", ""), why, parse])
    return rows


async def run_anticheat(cfg: EvalConfig) -> dict[str, Any]:
    data = cfg.dataset("anticheat")
    items = cfg.take(data["items"])
    passes: dict[str, list[dict[str, Any]]] = {}
    passes["mock"] = [await anticheat_item(cfg, it, real_j1=False) for it in items]
    skipped: list[str] = []
    if not cfg.mock and cfg.settings.jev_api_key() is not None:
        passes["jev"] = await gather_limited(4, [lambda it=it: anticheat_item(cfg, it, real_j1=True) for it in items])
    elif not cfg.mock:
        skipped.append("真 Jev 归类的一遍（没有 Jev 的钥匙）")
    cats = list(data.get("categories") or {})
    stats = {k: by_category(v) for k, v in passes.items()}
    rows = [[c, *[frac(*stats[k].get(c, (0, 0))) for k in passes]] for c in cats if any(c in stats[k] for k in passes)]
    rows.append(["合计", *[frac(sum(1 for r in v if r["passed"]), len(v)) for v in passes.values()]])
    tables = [table("各类通过的条数", ["类别", *[PASS_LABEL[k] for k in passes]], rows)]
    for k, v in passes.items():
        fr = _failure_rows(v)
        if fr:
            tables.append(table(f"没通过的条目（{PASS_LABEL[k]}）", ["条目", "类别", "不成立的断言", "解析"], fr))
    mock_ok = sum(1 for r in passes["mock"] if r["passed"])
    gates = [gate("防作弊集（M3 的闸，Mock 模型）", mock_ok == len(items) if items else None,
                  f"{mock_ok}/{len(items)} 条通过；要求全部通过。")]
    summary = {k: {"passed": sum(1 for r in v if r["passed"]), "n": len(v), "by_category": stats[k]}
               for k, v in passes.items()}
    return make_result(cfg, "anticheat", n=len(items), summary=summary, tables=tables, gates=gates, skipped=skipped,
                       items=passes)


# ---------------------------------------------------------------- 拒绝集


class LabeledJ1:
    """包住 Mock 的 Jev：J1 的提示换成条目的标注（对谁、求什么、算哪一类），其余调用原样放行。

    Mock 的 J1 只会照规则猜测作答，读不懂换了说法的请求；拒绝集要验的是裁决与答复（6.6），
    归类对不对另由归类集去评，所以这一遍让「归类」照标注来（README 2.7：gold 转成 hints）。
    """

    def __init__(self, inner: Any, game: Game, item: Mapping[str, Any]) -> None:
        self.inner = inner
        self.game = game
        self.item = item
        self.used = 0
        self.on_menu: bool | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def hints(self, base: Mapping[str, Any] | None) -> dict[str, Any]:
        st, df, item = self.game.state, self.game.defs, self.item
        clean, _attempts = parser.strip_stage(str(item["input"]))
        menu = intake.build_menu(st, df, clean)
        h = {k: v for k, v in dict(base or {}).items() if not k.startswith(("ask_", "claim_", "self_"))}
        h.update({"kind": "say", "has_speech": 1.0, "meaningful": 1.0, "verb": "none", "target": "none",
                  "target2": "none", "act": "request", "urgent": 0.0})
        p = menu.person(str(item["to"]))
        h["to"] = p.key if p is not None else "all"
        asks = item.get("asks") or {}
        cand = next((a for a in menu.asks if a.to == item["to"] and a.type == asks.get("type")
                     and all(a.slots.get(k) == asks[k] for k in scoring.ASK_SLOTS if asks.get(k) is not None)), None)
        self.on_menu = cand is not None
        if cand is not None:
            h[f"ask_{cand.key}"] = 1.0
        if item.get("cls"):
            h["cls"] = str(item["cls"])
        return h

    async def ask(self, kind: str, state: Any, questions: Mapping[str, Any], *, hints: Any = None, **kw: Any) -> Any:
        if kind == "J1" and self.used == 0:
            self.used += 1
            hints = self.hints(hints)
        return await self.inner.ask(kind, state, questions, hints=hints, **kw)


async def refusal_item(cfg: EvalConfig, item: Mapping[str, Any], *, real_j1: bool, labeled: bool = False
                       ) -> dict[str, Any]:
    to = str(item["to"])
    exp = item["expect"]
    rec: dict[str, Any] = {"id": item["id"], "category": item.get("basis", ""), "passed": False, "failed": [],
                           "turns": [], "replies": [], "overridden": 0}

    def replies_of(req: Any, events: Sequence[Event]) -> list[str]:
        if req is None:
            return []
        return [e.speech.act for e in events
                if e.actor == to and e.verb == "say" and e.speech is not None and e.speech.request == req.id]

    def no_reply_yet(before: WorldState, g: Game, events: Sequence[Event]) -> bool:
        req = assertions.find_request(before, g.state, to)
        return req is not None and not replies_of(req, events)

    taps: list[LabeledJ1] = []

    def prepare(g: Game) -> None:
        if labeled:
            taps.append(LabeledJ1(g.llm.jev, g, item))
            g.llm.jev = taps[-1]  # type: ignore[assignment]

    try:
        p = await play(cfg, cfg.scene_setup(item["scene"]), [str(item["input"])], real_j1=real_j1,
                       run_id=f"refusal-{item['id']}", extra_wait=no_reply_yet, prepare=prepare)
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    if taps:
        rec["ask_on_menu"] = taps[0].on_menu
    req = assertions.find_request(p.before, p.after, to)
    rec["request"] = request_info(req)
    rec["turns"] = p.turns
    failed: list[dict[str, Any]] = []
    ok, detail = assertions.check_request(req, {"verdict_in": list(exp["verdict_in"])})
    if not ok:
        failed.append({"assertion": {"request": {"to": to, "verdict_in": list(exp["verdict_in"])}}, "detail": detail})
    replies = replies_of(req, p.events)
    bad = [r for r in replies if r not in exp["replies"]]
    if bad:
        failed.append({"assertion": {"replies": list(exp["replies"])}, "detail": f"replied {bad}"})
    rec["replies"] = replies
    rec["overridden"] = len(p.violations)  # 硬选答应被内核改判为拒绝的，记在 violations 里
    rec["violations"] = p.violations
    rec["failed"] = failed
    rec["passed"] = not failed
    return rec


async def run_refusals(cfg: EvalConfig) -> dict[str, Any]:
    items = cfg.take(cfg.dataset("refusals")["items"])
    passes: dict[str, list[dict[str, Any]]] = {}
    passes["mock_labeled"] = [await refusal_item(cfg, it, real_j1=False, labeled=True) for it in items]
    passes["mock_rules"] = [await refusal_item(cfg, it, real_j1=False) for it in items]
    skipped: list[str] = []
    if not cfg.mock and cfg.settings.jev_api_key() is not None:
        passes["jev"] = await gather_limited(4, [lambda it=it: refusal_item(cfg, it, real_j1=True) for it in items])
    elif not cfg.mock:
        skipped.append("真 Jev 归类的一遍（没有 Jev 的钥匙）")
    rows = []
    for k, v in passes.items():
        rows.append([PASS_LABEL[k], frac(sum(1 for r in v if r["passed"]), len(v)),
                     sum(1 for r in v if r.get("request") is not None), sum(1 for r in v if r["replies"]),
                     sum(1 for r in v if r["replies"] and all(x == "refuse" for x in r["replies"])),
                     sum(int(r.get("overridden") or 0) for r in v)])
    tables = [table("裁决与答复", ["哪一遍", "通过", "解析成了对他的请求", "两回合内有答复", "答复全是拒绝", "内核记下的违规（改判）"], rows)]
    for k, v in passes.items():
        stats = by_category(v)
        tables.append(table(f"按依据分组（{PASS_LABEL[k]}）", ["依据", "通过"], [[c, frac(*s)] for c, s in stats.items()]))
        fr = _failure_rows(v)
        if fr:
            tables.append(table(f"没通过的条目（{PASS_LABEL[k]}）", ["条目", "依据", "不成立的断言", "解析"], fr))
    mock_ok = sum(1 for r in passes["mock_labeled"] if r["passed"])
    rules_ok = sum(1 for r in passes["mock_rules"] if r["passed"])
    gates = [gate("拒绝集（M3 的闸，Mock 模型）", mock_ok == len(items) if items else None,
                  f"归类照标注时 {mock_ok}/{len(items)} 条通过（验的是 6.6 的裁决与答复）；"
                  f"归类只靠规则猜测时 {rules_ok}/{len(items)}（差的都是没把话读成对那个人的请求）。要求全部通过。")]
    notes = ["「归类照条目的标注」一遍：Mock 的 J1 按条目里标的 to、asks、cls 作答（README 2.7 的做法），其余全是真的回合流程；"
             "归类本身对不对由归类集去评。"]
    summary = {k: {"passed": sum(1 for r in v if r["passed"]), "n": len(v),
                   "requests": sum(1 for r in v if r.get("request") is not None),
                   "replied": sum(1 for r in v if r["replies"]),
                   "overridden": sum(int(r.get("overridden") or 0) for r in v)} for k, v in passes.items()}
    return make_result(cfg, "refusals", n=len(items), summary=summary, tables=tables, gates=gates, skipped=skipped,
                       notes=notes, items=passes)
