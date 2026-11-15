"""归类集（方案 11.6、evals/README.md 第 2 节）：真的解析器 classify() + bind()，逐条与标注比对。

三条路：``rules_only``（只用规则）、``jev``（真 J1；C1 不可用，J1 答了就留着）、``c1``（只用 C1，要 Claude 的钥匙）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tianlong import parser
from tianlong.evalrun import scoring
from tianlong.evalrun.common import (
    SKIP,
    EvalConfig,
    frac,
    gate,
    gather_limited,
    lang_prompts,
    make_result,
    median,
    pct,
    percentile,
    secs,
    table,
    tagged,
)
from tianlong.llm import route as llm_route
from tianlong.parser import intake
from tianlong.parser import rules as parser_rules

ROUTES = ("rules_only", "jev", "c1")
ROUTE_LABEL = {"rules_only": "只用规则", "jev": "Jev（J1）", "c1": "Claude（C1）"}
ROUTE_MODE = {"rules_only": "rules_only", "jev": "jev_first", "c1": "claude_only"}
SWEEP = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
GATE_KEYS = ("kind", "verb", "target", "act")


def resolve_claims(claims: Sequence[str] | None, setup_events: Sequence[Any], player: str) -> list[str]:
    """``self:<动词>`` 换成 setup 里玩家做的那件事的事件引用（README 2.5）。"""
    out: list[str] = []
    for c in claims or []:
        if isinstance(c, str) and c.startswith("self:"):
            verb = c.split(":", 1)[1]
            ev = next((e for e in reversed(list(setup_events)) if e.actor == player and e.verb == verb), None)
            out.append(f"event:{ev.id}" if ev is not None else c)
        else:
            out.append(c)
    return out


def resolved_item(item: Mapping[str, Any], setup_events: Sequence[Any], player: str) -> dict[str, Any]:
    it = dict(item)
    gold = dict(item["gold"])
    if gold.get("claims"):
        gold["claims"] = resolve_claims(gold["claims"], setup_events, player)
    it["gold"] = gold
    alts = []
    for a in item.get("alt") or []:
        a = dict(a)
        if a.get("claims"):
            a["claims"] = resolve_claims(a["claims"], setup_events, player)
        alts.append(a)
    if alts:
        it["alt"] = alts
    return it


def pred_of(state: Any, result: Any, bound: Any) -> dict[str, Any]:
    """解析链的结论 → 计分用的字典（scoring.score_fields 的 pred）。"""
    it = result.intent
    loc = state.chars[state.player].loc
    menu = [{"type": a.type, **a.slots} for a in result.menu.asks]
    blank = {"kind": bound.kind, "verb": None, "action": None, "loc": loc, "act": None, "to": [], "asks": None,
             "ask_menu": menu, "claims": [], "cls": None, "urgent": False}
    if bound.kind != "action" or bound.action is None:
        return blank
    kind = it.kind if it.kind in ("act", "say", "both") else "say"
    out = dict(blank)
    out["kind"] = kind
    if kind in ("act", "both"):
        out["verb"] = bound.action.verb
        out["action"] = {"verb": bound.action.verb, "params": dict(bound.action.params)}
    if kind in ("say", "both"):
        out["act"] = it.act
        out["to"] = list(it.to or [])
        cand = result.menu.ask(it.asks)
        out["asks"] = {"type": cand.type, **cand.slots} if cand is not None else None
        out["claims"] = list(it.claim_refs or [])
        out["cls"] = it.cls
        out["urgent"] = bool(it.urgent)
    return out


def confidence_of(answers: Mapping[str, Any], intent: Any, th: Any) -> dict[str, Any]:
    """J1 这一条的把握：解析器升级时看的那几道 choice 题里最低的 confidence 与最小的头两名差（4.3）。"""
    keys = ["kind"]
    if intent.kind in ("act", "both"):
        keys.append("verb")
        if intent.verb in parser_rules.TARGET_KINDS:
            keys.append("target")
    if intent.kind in ("say", "both") and "act" in answers:
        keys.append("act")
    present = [k for k in keys if k in answers]
    confs = {k: float(answers[k].get("confidence", 0.0)) for k in GATE_KEYS + ("to",) if k in answers}
    mid = False
    for k in ("has_speech", "meaningful"):
        a = answers.get(k)
        if a is not None and intake.NOUL_FLOOR < float(a.get("noul", 0.0)) < float(th.noul_yes):
            mid = True
    return {
        "conf": min((confs[k] for k in present), default=0.0),
        "margin": min((llm_route.choice_margin(answers[k]) for k in present), default=0.0),
        "noul_mid": mid,
        "confs": confs,
        "unsure": bool(intake.jev_unsure(dict(answers), intent, th)),
    }


def _top(answer: Any) -> str | None:
    pick = scoring.answer_decision(answer)
    return str(pick) if pick is not None else None


def raw_of(answers: Mapping[str, Any], menu: Any, th: Any) -> dict[str, Any]:
    """J1 的原始答案 → scoring.score_raw 的 raw（选项编号换回实体 id，是非题按 noul_yes 判）。"""
    yes = float(th.noul_yes)

    def p(key: str) -> float:
        return float((answers.get(key) or {}).get("noul", 0.0))

    vopt = menu.verb(_top(answers.get("verb")))
    verb = name = None
    if vopt is not None:
        verb = "setpiece" if vopt.kind == "setpiece" else vopt.ref
        name = vopt.ref.split(":", 1)[-1] if vopt.kind == "setpiece" else None
    targets = [o.ref for o in (menu.obj(_top(answers.get(k))) for k in ("target", "target2")) if o is not None]
    to_key = _top(answers.get("to"))
    person = menu.person(to_key) if to_key and to_key != "all" else None
    asks = [(a, p(f"ask_{a.key}")) for a in menu.asks if f"ask_{a.key}" in answers]
    asks = [ap for ap in asks if ap[1] > yes]
    best = max(asks, key=lambda ap: ap[1])[0] if asks else None
    claims = [c.ref for c in menu.claims if p(f"claim_{c.key}") > yes]
    selfs = [x.ref for x in menu.selfs if p(f"self_{x.key}") > yes]
    if not selfs and p("self_unlisted") > yes:
        selfs = ["self_unlisted"]
    return {
        "kind": _top(answers.get("kind")), "has_speech": p("has_speech") > yes, "meaningful": p("meaningful") > yes,
        "verb": verb, "name": name, "targets": targets, "act": _top(answers.get("act")),
        "to": [person.ref] if person is not None else [],
        "asks": {"type": best.type, **best.slots} if best is not None else None,
        "ask_menu": [{"type": a.type, **a.slots} for a in menu.asks],
        "claims": claims + selfs, "cls": _top(answers.get("cls")), "urgent": p("urgent") > yes,
    }


def repaired(result: Any, answers: Mapping[str, Any], th: Any) -> Any:
    """「修补后的映射」（只作参考，量解析链在 J1 答案之后丢了多少）：同一份 J1 答案，换三条落地规则——
    kind 一题很有把握地说只是动作，就不因是非题而算成「又做又说」；不带对象的动词不取 target、target2；
    交接类的动词（give、show、use、steal）在两题的概率里各取最可能的一样东西与一个人。
    """
    it = result.intent
    if it.kind not in ("act", "both") or not it.verb:
        return result
    menu = result.menu
    upd: dict[str, Any] = {}
    kind_ans = answers.get("kind") or {}
    if it.kind == "both" and _top(kind_ans) == "act" and float(kind_ans.get("confidence", 0.0)) >= th.choice_confidence:
        upd.update(kind="act", act=None, to=[], asks=None, cls=None, urgent=False, claim_refs=[])
    if it.verb != "setpiece":
        kinds = parser_rules.TARGET_KINDS.get(it.verb, ())

        def best(kind: str) -> str | None:
            score: dict[str, float] = {}
            for q in ("target", "target2"):
                for key, pr in ((answers.get(q) or {}).get("probabilities") or {}).items():
                    o = menu.obj(key)
                    if o is not None and o.kind == kind:
                        score[o.ref] = max(score.get(o.ref, 0.0), float(pr))
            top = max(score, key=lambda k: score[k]) if score else None
            return top if top is not None and score[top] >= float(th.sample_floor) else None

        if not kinds:
            upd.update(target=None, target2=None)
        elif it.verb in ("give", "show", "use", "steal"):
            upd.update(target=best("item"), target2=best("person"))
        elif it.verb not in ("study", "teach", "push", "examine"):
            upd.update(target2=None)
    if not upd:
        return result
    return result.model_copy(update={"intent": it.model_copy(update=upd)})


async def evaluate(cfg: EvalConfig, items: Sequence[Mapping[str, Any]], route: str, *, lang: str | None = None
                   ) -> list[dict[str, Any]]:
    """一条路上逐条跑解析器并计分。返回每条的记录（scoring 里各函数的 records）。"""
    settings = cfg.eval_settings(classify=ROUTE_MODE[route])
    llm = cfg.open_llm(settings, claude=(route != "jev"))
    th = settings.jev.thresholds
    scenes: dict[str, Any] = {}

    def scene(item: Mapping[str, Any]) -> Any:
        key = item["scene"] if isinstance(item["scene"], str) else f"#{item['id']}"
        if key not in scenes:
            scenes[key] = cfg.build(cfg.scene_setup(item["scene"]))
        return scenes[key]

    async def one(item: Mapping[str, Any]) -> dict[str, Any]:
        defs, st, evs = scene(item)
        with tagged(str(item["id"])):
            result = await parser.classify(llm, st, defs, str(item["input"]))
        bound = parser.bind(st, defs, result)
        pred = pred_of(st, result, bound)
        gold_item = resolved_item(item, evs, st.player)
        fields = scoring.score_classify(gold_item, pred)
        rec: dict[str, Any] = {
            "id": item["id"], "tags": list(item.get("tags") or []), "via": result.via, "fields": fields,
            "core": scoring.all_right(fields, scoring.CORE), "all": scoring.all_right(fields),
            "pred": {k: v for k, v in pred.items() if k not in ("ask_menu", "loc")},
            "gold_kind": item["gold"]["kind"],
            "attempts_ok": list(result.attempts) == list(item.get("attempts") or []),
            "errors": list(result.errors), "failed": False, "conf": None,
        }
        if route == "jev":
            call = llm.jev.last(str(item["id"]), "J1")
            if call is not None and call.answers is not None:
                rec.update(confidence_of(call.answers, result.intent, th))
                rec["seconds"] = call.seconds
                rec["raw"] = scoring.score_raw_item(gold_item, raw_of(call.answers, result.menu, th))
                fixed = repaired(result, call.answers, th)
                if fixed is not result:
                    f2 = scoring.score_classify(gold_item, pred_of(st, fixed, parser.bind(st, defs, fixed)))
                    rec["fixed"] = {"fields": f2, "core": scoring.all_right(f2, scoring.CORE), "all": scoring.all_right(f2)}
            elif call is None:
                rec.update({"conf": 1.0, "margin": 1.0, "noul_mid": False, "by_rule": True})  # 规则就定了，没问 J1
            else:
                rec["failed"] = True  # J1 没答上来（超时、报错）：解析器退到「看不懂」
        if route == "c1" and result.via != "claude":
            rec["failed"] = bool(result.errors)
        return rec

    try:
        with lang_prompts(lang or cfg.lang):
            return await gather_limited(cfg.parallel, [lambda it=it: one(it) for it in items])
    finally:
        await llm.aclose()


# ---------------------------------------------------------------- 报告


def fixed_records(jev: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """J1 一路的记录，凡有「修补后的映射」的换成修补后的计分。"""
    return [{**r, **r["fixed"]} if r.get("fixed") else dict(r) for r in jev]


def field_rows(by_route: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[list[str], list[list[Any]]]:
    cols = {ROUTE_LABEL[r]: scoring.field_accuracy(recs) for r, recs in by_route.items()}
    if by_route.get("jev"):
        cols["J1 + 修补后的映射（参考）"] = scoring.field_accuracy(fixed_records(by_route["jev"]))
    names = {**{k: k for k in scoring.FIELDS}, "core": "六项全对（kind、verb、target、act、to、asks）", "all": "全部字段全对"}
    rows = []
    for k in (*scoring.FIELDS, "core", "all"):
        rows.append([names[k], *[frac(*acc[k]) for acc in cols.values()]])
    return list(cols), rows


def summarize(cfg: EvalConfig, by_route: Mapping[str, Sequence[Mapping[str, Any]]], n: int,
              skipped: Sequence[str] = ()) -> dict[str, Any]:
    th = cfg.settings.jev.thresholds
    cols, rows = field_rows(by_route)
    tables = [table("各字段准确率（整条解析链：classify 加 bind 之后的结论）", ["字段", *cols], rows,
                    note="target 一栏：解析出的合法动作与标注的动词、对象、第二对象、本事、情境动作名全部对上才算对。"
                         "「修补后的映射」用的是同一份 J1 答案，只换了答案落成意图的三条规则（见说明），不算一条正式的路。")]
    summary: dict[str, Any] = {"routes": {}}
    for r, recs in by_route.items():
        acc = scoring.field_accuracy(recs)
        summary["routes"][r] = {k: {"hits": h, "n": t, "accuracy": h / t if t else None} for k, (h, t) in acc.items()}
        summary["routes"][r]["via"] = {v: sum(1 for x in recs if x["via"] == v) for v in ("jev", "claude", "rule")}
        summary["routes"][r]["attempts_ok"] = sum(1 for x in recs if x["attempts_ok"])
    gates: list[dict[str, Any]] = []
    notes: list[str] = []
    jev = list(by_route.get("jev") or [])
    if jev:
        g_core = scoring.gate_stats(jev, th.choice_confidence, th.choice_margin, "core")
        g_all = scoring.gate_stats(jev, th.choice_confidence, th.choice_margin, "all")
        failed = sum(1 for r in jev if r.get("failed"))
        lat = [r["seconds"] for r in jev if r.get("seconds") is not None]
        tables.append(table(
            "J1：按置信度分桶（取该看的 choice 题里最低的 confidence）", ["置信度", "条数", "六项全对", "全部字段全对"],
            [[f"{b['lo']:.2f}–{b['hi']:.2f}", b["n"], frac(b["hits"], b["n"]),
              frac(a["hits"], a["n"])]
             for b, a in zip(scoring.bucket_stats(jev, "core"), scoring.bucket_stats(jev, "all"), strict=True)]))
        qrows = []
        for q in ("kind", "verb", "target", "act", "to"):
            rs = [{"conf": r["confs"][q], "ok": r["fields"].get(q)} for r in jev
                  if q in (r.get("confs") or {}) and r["fields"].get(q) is not None]
            qrows.append([q, *[frac(b["hits"], b["n"]) for b in scoring.bucket_stats(rs, "ok")]])
        tables.append(table("J1：各题按自己的 confidence 分桶的准确率",
                            ["题", *[f"{lo:.2f}–{min(hi, 1.0):.2f}" for lo, hi in scoring.BUCKETS]], qrows))
        tables.append(table(
            f"J1：过门槛的覆盖率与准确率（门槛扫描，choice_margin 固定为 {th.choice_margin:.2f}）",
            ["choice_confidence", "过门槛", "覆盖率", "六项全对", "全部字段全对"],
            [[f"{s['conf']:.2f}" + ("（现用）" if abs(s["conf"] - th.choice_confidence) < 1e-9 else ""), s["above"],
              pct(s["coverage"]), frac(s["hits"], s["above"]), frac(a["hits"], a["above"])]
             for s, a in zip(scoring.sweep(jev, SWEEP, th.choice_margin, "core"),
                             scoring.sweep(jev, SWEEP, th.choice_margin, "all"), strict=True)]))
        raw = scoring.raw_accuracy(jev)
        lost = [r for r in jev if r.get("raw") and r["raw"].get("verb") and r["raw"].get("kind") is not False
                and r["fields"].get("kind") is False]
        tables.insert(1, table(
            "J1 各题的准确率（直接看 J1 的答案，README 2.7 的投影，不经 bind）", ["题", "准确率"],
            [[k, frac(*raw[k])] for k in scoring.RAW],
            note=f"target：target、target2 两题选中的对象合起来与标注相符（不分先后）；没有对象的动作两题都该答「没有」。"
                 f"kind、verb 两题 J1 都答对、整条解析链却没落成标注的那类动作的条目：{len(lost)} 条。"))
        fx = fixed_records(jev)
        g_fix = scoring.gate_stats(fx, th.choice_confidence, th.choice_margin, "core")
        summary["jev_raw"] = {k: {"hits": h, "n": t, "accuracy": h / t if t else None} for k, (h, t) in raw.items()}
        summary["jev_repaired"] = {"core": scoring.field_accuracy(fx)["core"], "all": scoring.field_accuracy(fx)["all"],
                                   "gate": g_fix, "lost_after_j1": len(lost)}
        tags: dict[str, list[dict[str, Any]]] = {}
        for r in jev:
            for t in r["tags"] or ["（无标签）"]:
                tags.setdefault(t, []).append(r)
        rules_by_id = {r["id"]: r for r in by_route.get("rules_only") or []}
        tables.append(table(
            "按标签分组（六项全对）", ["标签", "条数", "J1", "只用规则"],
            [[t, len(rs), frac(sum(1 for r in rs if r["core"]), len(rs)),
              frac(sum(1 for r in rs if rules_by_id.get(r["id"], {}).get("core")),
                   sum(1 for r in rs if r["id"] in rules_by_id))]
             for t, rs in sorted(tags.items(), key=lambda kv: -len(kv[1]))]))
        wrong = [r for r in jev if not r["core"]]
        tables.append(table(
            "J1 六项里答错的条目（至多列 40 条，全部在结果文件里）", ["条目", "标注 kind", "错的字段", "置信度", "过门槛"],
            [[r["id"], r["gold_kind"], "、".join(k for k in scoring.CORE if r["fields"].get(k) is False),
              "—" if r.get("conf") is None else f"{r['conf']:.2f}",
              scoring.passes(r, th.choice_confidence, th.choice_margin)] for r in wrong[:40]]))
        best = scoring.recommend(jev, SWEEP, th.choice_margin, min_acc=0.95, min_cov=0.70, key="core")
        passed = scoring.classify_gate(g_core)
        detail = (f"门槛 choice_confidence {th.choice_confidence:.2f}、choice_margin {th.choice_margin:.2f} 之上："
                  f"覆盖率 {pct(g_core['coverage'])}（{g_core['above']}/{g_core['n']}），六项全对 {pct(g_core['accuracy'])}，"
                  f"全部字段全对 {pct(g_all['accuracy'])}；要求一致不低于 95%、覆盖不少于 70%。")
        if best is not None:
            detail += f" 扫描里过闸且覆盖最高的门槛：choice_confidence {best['conf']:.2f}（覆盖 {pct(best['coverage'])}，" \
                      f"一致 {pct(best['accuracy'])}）。"
        else:
            detail += " 扫描的门槛里没有一个同时满足两项。"
        gates.append(gate("J1 归类（7.12）", passed, detail, "该项默认走 C1：route.classify = 'claude_first'"))
        summary["jev_gate"] = {"core": g_core, "all": g_all, "recommended": best, "failed_requests": failed}
        summary["jev_latency"] = {"median_s": median(lat), "p95_s": percentile(lat, 95)}
        notes.append(f"J1 请求 {len(lat)} 个，时延中位 {secs(median(lat))}、P95 {secs(percentile(lat, 95))}"
                     f"（评测里超时放宽到 {cfg.timeout_s:g} 秒）；没答上来 {failed} 条；"
                     f"规则先定、没问 J1 的 {sum(1 for r in jev if r.get('by_rule'))} 条。")
        notes.append("「六项全对」是这道闸用的「与人工标注一致」：kind、verb、target、act、to、asks 都对；"
                     "claims、cls、urgent 是另问的是非题与类别，单列在「全部字段全对」里。")
        notes.append("「修补后的映射」（参考，不是正式的路）：kind 一题有把握地答「只是动作」时不因 has_speech 过线而算成又做又说；"
                     "不带对象的动词不取 target、target2；give、show、use、steal 在两题的概率里各取最可能的物与人。"
                     f"这样落地后六项全对 {frac(*scoring.field_accuracy(fx)['core'])}，门槛之上覆盖 {pct(g_fix['coverage'])}、"
                     f"一致 {pct(g_fix['accuracy'])}。")
    return make_result(cfg, "classify", n=n, summary=summary, tables=tables, gates=gates, skipped=list(skipped),
                       notes=notes, items={r: list(recs) for r, recs in by_route.items()})


async def run(cfg: EvalConfig) -> dict[str, Any]:
    items = cfg.take(cfg.dataset("classify")["items"])
    by_route: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    by_route["rules_only"] = await evaluate(cfg, items, "rules_only")
    if cfg.mock or cfg.settings.jev_api_key() is not None:
        by_route["jev"] = await evaluate(cfg, items, "jev")
    else:
        skipped.append("J1（没有 Jev 的钥匙）")
    if cfg.mock or cfg.settings.claude_api_key() is not None:
        by_route["c1"] = await evaluate(cfg, items, "c1")
    else:
        skipped.append(f"C1 一栏（{SKIP}：没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval classify")
    return summarize(cfg, by_route, len(items), skipped)
