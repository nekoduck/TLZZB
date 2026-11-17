"""抉择集（方案 11.6、evals/README.md 第 3 节）：每条改写成 J2a 式的请求，比较与原著做法的一致率。

Jev 一栏真问；Haiku、Sonnet 两栏经 Claude 客户端问 C2（没有钥匙时记 SKIPPED）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from tianlong import prompting
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
    question_words,
    secs,
    table,
    tagged,
)
from tianlong.llm import LLMError
from tianlong.llm import route as llm_route
from tianlong.llm.schemas import Decision
from tianlong.minds import render

NONE = "none"
SWEEP = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
CLAUDE_COLUMNS = {"haiku": "C2_named", "sonnet": "C2_soul"}  # 列名 → 调用代号（[models.*] 里各自的模型）
_RECENT_RE = re.compile(r"^【刚才】.*$", re.M)


def state_text(state: Any, defs: Any, char_id: str, situation: str) -> str:
    """7.3 的 J2 状态：各节从搭好的状态取，``situation`` 放进【刚才】。"""
    text = render.state_text(state, defs, char_id, ())
    line = f"【刚才】{situation.strip()}"
    if _RECENT_RE.search(text):
        return _RECENT_RE.sub(lambda _m: line, text, count=1)
    return text + "\n" + line


def questions(item: Mapping[str, Any]) -> dict[str, Any]:
    """J2a 的三道题；选项就是各候选的 desc（不加 ★ 前缀），外加「什么也不做」。"""
    q = question_words("j2a_questions")
    criteria = {str(c["id"]): str(c["desc"]) for c in item["candidates"]}
    criteria[NONE] = str(q["none"])
    return {
        "action": {"type": "choice", "instructions": str(q["action"]), "criteria": criteria},
        "speak": {"type": "noul", "instructions": str(q["speak"])},
        "needs_thought": {"type": "noul", "instructions": str(q["needs_thought"])},
    }


def record_of(item: Mapping[str, Any], tier: str, answers: Mapping[str, Any] | None, th: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": item["id"], "char": item["char"], "tier": tier, "confidence": item.get("confidence", "high"),
        "canon": item["canon"], "pick": None, "agree": False, "failed": answers is None, "conf": None,
    }
    if answers is None:
        return rec
    a = answers["action"]
    probs = {str(k): float(v) for k, v in (a.get("probabilities") or {}).items()}
    pick = scoring.answer_decision(a)
    nt = float((answers.get("needs_thought") or {}).get("noul", 0.0))
    rec.update({
        "pick": pick, "agree": scoring.decision_agrees(item, pick), "conf": float(a.get("confidence", 0.0)),
        "margin": llm_route.choice_margin(a), "needs_thought": nt > float(th.needs_thought), "needs_thought_p": nt,
        "p_canon": scoring.canon_mass(item, probs), "probabilities": probs,
        "speak_p": float((answers.get("speak") or {}).get("noul", 0.0)),
        "unsure": bool(llm_route.should_escalate(dict(answers), th, keys=["action"])),
    })
    return rec


async def evaluate(cfg: EvalConfig, items: Sequence[Mapping[str, Any]], *, lang: str | None = None
                   ) -> list[dict[str, Any]]:
    """真问 Jev（或 Mock）。"""
    settings = cfg.eval_settings()
    llm = cfg.open_llm(settings, claude=False)
    th = settings.jev.thresholds

    async def one(item: Mapping[str, Any]) -> dict[str, Any]:
        defs, st, _ = cfg.build(item.get("setup"))
        cid = str(item["char"])
        text = state_text(st, defs, cid, str(item["situation"]))
        qs = questions(item)
        answers: Any = None
        err = None
        with tagged(str(item["id"])):
            try:
                answers = await llm.jev.ask("J2a", text, qs)
            except LLMError as e:
                err = f"{type(e).__name__}: {e}"
        rec = record_of(item, st.chars[cid].tier, answers, th)
        call = llm.jev.last(str(item["id"]), "J2a")
        rec["seconds"] = call.seconds if call is not None else None
        if err:
            rec["error"] = err
        return rec

    try:
        with lang_prompts(lang or cfg.lang):
            return await gather_limited(cfg.parallel, [lambda it=it: one(it) for it in items])
    finally:
        await llm.aclose()


async def evaluate_claude(cfg: EvalConfig, items: Sequence[Mapping[str, Any]], column: str) -> list[dict[str, Any]]:
    """同一份状态问 C2（7.6）：``<你能做的事>`` 列这些候选。column 取 haiku / sonnet。"""
    kind = CLAUDE_COLUMNS[column]
    settings = cfg.eval_settings()
    llm = cfg.open_llm(settings)

    async def one(item: Mapping[str, Any]) -> dict[str, Any]:
        defs, st, _ = cfg.build(item.get("setup"))
        cid = str(item["char"])
        first = str(item["candidates"][0]["id"])
        system = [prompting.load("c2_system"), render.card_block(defs, cid, state=st)]
        user = prompting.render(
            "c2_user", state=state_text(st, defs, cid, str(item["situation"])),
            actions=[{"id": str(c["id"]), "star": False, "text": str(c["desc"])} for c in item["candidates"]],
            acts=[], people=[], stances=[], facts=[], requests=[])
        rec: dict[str, Any] = {"id": item["id"], "char": item["char"], "tier": st.chars[cid].tier,
                               "confidence": item.get("confidence", "high"), "canon": item["canon"], "pick": None,
                               "agree": False, "failed": False}
        try:
            obj, _meta = await llm.claude.call_json(
                kind, system, user, Decision,
                mock_default=lambda: Decision(inner="", action=first, speech=None, display=""))
        except LLMError as e:
            rec.update({"failed": True, "error": f"{type(e).__name__}: {e}"})
            return rec
        rec["pick"] = obj.action
        rec["agree"] = scoring.decision_agrees(item, obj.action)
        return rec

    try:
        return await gather_limited(min(cfg.parallel, 4), [lambda it=it: one(it) for it in items])
    finally:
        await llm.aclose()


# ---------------------------------------------------------------- 报告


def summarize(cfg: EvalConfig, jev: Sequence[Mapping[str, Any]], claude: Mapping[str, Sequence[Mapping[str, Any]]],
              n: int, skipped: Sequence[str] = ()) -> dict[str, Any]:
    th = cfg.settings.jev.thresholds
    cols: dict[str, Sequence[Mapping[str, Any]] | None] = {"Jev": jev, "Haiku": claude.get("haiku"),
                                                          "Sonnet": claude.get("sonnet")}

    def cell(recs: Sequence[Mapping[str, Any]] | None, pred: Any = None) -> str:
        if recs is None:
            return SKIP
        rs = [r for r in recs if pred is None or pred(r)]
        return frac(*scoring.agreement(rs))

    rows = [["全部", *[cell(v) for v in cols.values()]]]
    for label, name in (("high", "confidence: high"), ("low", "confidence: low（原著做法待核对）")):
        rows.append([name, *[cell(v, lambda r, label=label: r["confidence"] == label) for v in cols.values()]])
    for tier, name in (("soul", "有魂角色"), ("named", "有名角色")):
        rows.append([name, *[cell(v, lambda r, tier=tier: r["tier"] == tier) for v in cols.values()]])
    tables = [table("与原著做法的一致率（选中 canon 或 accept 算一致）", ["样本", *cols], rows)]
    g = scoring.gate_stats(jev, th.choice_confidence, th.choice_margin, "agree")
    soul = [r for r in jev if r["tier"] == "soul"]
    named = [r for r in jev if r["tier"] != "soul"]
    g_soul = scoring.gate_stats(soul, th.choice_confidence, th.choice_margin, "agree")
    g_named = scoring.gate_stats(named, th.choice_confidence, th.choice_margin, "agree")
    ok = [r for r in jev if not r.get("failed")]
    mass = sum(r.get("p_canon", 0.0) for r in ok) / len(ok) if ok else None
    tables.append(table(
        "Jev：过门槛的样本（confidence、头两名之差都够，且 needs_thought 不过线）",
        ["样本", "条数", "过门槛", "覆盖率", "过门槛样本的一致率"],
        [[name, s["n"], s["above"], pct(s["coverage"]), frac(s["hits"], s["above"])]
         for name, s in (("全部", g), ("有魂角色", g_soul), ("有名角色", g_named))]))
    tables.append(table(
        f"Jev：门槛扫描（choice_margin 固定为 {th.choice_margin:.2f}）",
        ["choice_confidence", "过门槛", "覆盖率", "一致率"],
        [[f"{s['conf']:.2f}" + ("（现用）" if abs(s["conf"] - th.choice_confidence) < 1e-9 else ""), s["above"],
          pct(s["coverage"]), frac(s["hits"], s["above"])] for s in scoring.sweep(jev, SWEEP, th.choice_margin, "agree")]))
    tables.append(table(
        "Jev：按置信度分桶", ["confidence", "条数", "一致率"],
        [[f"{b['lo']:.2f}–{b['hi']:.2f}", b["n"], frac(b["hits"], b["n"])] for b in scoring.bucket_stats(jev, "agree")]))
    by_char = scoring.group_agreement(jev, "char")
    tables.append(table("Jev：按人分组", ["人物", "一致率"],
                        [[c, frac(*v)] for c, v in sorted(by_char.items(), key=lambda kv: -kv[1][1])]))
    wrong = [r for r in ok if not r["agree"]]
    tables.append(table(
        "Jev 与原著做法不一致的条目", ["条目", "人物", "标注把握", "原著", "Jev 选的", "confidence", "给原著做法的概率", "过门槛"],
        [[r["id"], r["char"], r["confidence"], r["canon"], r["pick"], f"{r['conf']:.2f}", f"{r.get('p_canon', 0.0):.2f}",
          scoring.passes(r, th.choice_confidence, th.choice_margin)] for r in wrong]))
    lat = [r["seconds"] for r in jev if r.get("seconds") is not None]
    passed = scoring.decisions_gate(g)
    best = scoring.recommend(jev, SWEEP, th.choice_margin, min_acc=0.70, min_cov=0.50, key="agree")
    detail = (f"门槛之上：覆盖率 {pct(g['coverage'])}（{g['above']}/{g['n']}），一致率 {pct(g['accuracy'])}；"
              f"要求一致不低于 70%、覆盖不少于 50%。全部样本的一致率 {frac(*scoring.agreement(jev))}，"
              f"按概率取样的期望一致率 {pct(mass)}。")
    if best is not None:
        detail += f" 扫描里过闸且覆盖最高的门槛：choice_confidence {best['conf']:.2f}（覆盖 {pct(best['coverage'])}，" \
                  f"一致 {pct(best['accuracy'])}）。"
    gates = [gate("J2 抉择（7.12）", passed, detail, "有魂角色改为 claude_first：route.decide_soul = 'claude_first'；有名角色仍用 Jev")]
    summary = {
        "jev": {"agree": scoring.agreement(jev)[0], "n": scoring.agreement(jev)[1], "gate": g, "gate_soul": g_soul,
                "gate_named": g_named, "expected_agreement_when_sampling": mass, "recommended": best,
                "failed": sum(1 for r in jev if r.get("failed")),
                "latency": {"median_s": median(lat), "p95_s": percentile(lat, 95)}},
        "claude": {k: {"agree": scoring.agreement(v)[0], "n": scoring.agreement(v)[1],
                       "failed": sum(1 for r in v if r.get("failed"))} for k, v in claude.items()},
    }
    notes = [f"J2a 请求时延中位 {secs(median(lat))}、P95 {secs(percentile(lat, 95))}；没答上来 {summary['jev']['failed']} 条。",
             "选项只给各候选的一句描述，不加「（你本来要做的）」前缀；测的是模型像不像这个人，不是要游戏照原著走。"]
    return make_result(cfg, "decisions", n=n, summary=summary, tables=tables, gates=gates, skipped=list(skipped),
                       notes=notes, items={"jev": list(jev), **{k: list(v) for k, v in claude.items()}})


async def run(cfg: EvalConfig) -> dict[str, Any]:
    items = cfg.take(cfg.dataset("decisions")["items"])
    jev = await evaluate(cfg, items)
    claude: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    if cfg.mock or cfg.settings.claude_api_key() is not None:
        for col in CLAUDE_COLUMNS:
            claude[col] = await evaluate_claude(cfg, items, col)
    else:
        skipped.append("Haiku、Sonnet 两栏（没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval decisions")
    return summarize(cfg, jev, claude, len(items), skipped)
