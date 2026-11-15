"""核对集（方案 11.6、evals/README.md 第 4 节）：每句一个 J5 请求，用叙述模块自己的状态模板与问法。"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from typing import Any

from tianlong import prompting
from tianlong.evalrun import scoring
from tianlong.evalrun.common import (
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
from tianlong.llm import LLMError

nar = importlib.import_module("tianlong.narrator.narrate")  # 包里同名的 narrate 是函数，这里要的是模块

SWEEP = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
LABEL_NAME = {"extra": "extra（多写）", "mind": "mind（读心）", "puppet": "puppet（替玩家做主）"}


def j5_state(ctx: Mapping[str, Any], sentence: str, people: set[str] | None = None) -> str:
    """7.3 的 J5 状态：该 context 的 facts、names、action（与 spoken）、scene，加上要核对的句子。"""
    names = dict(ctx.get("names") or {})
    shown = [v for v in names.values() if v and v != "你"]
    present = [v for k, v in names.items() if v and v != "你" and (people is None or k in people)]
    action = str(ctx.get("action") or "无")
    if ctx.get("spoken"):
        action += f"（原话：{ctx['spoken']}）"
    return prompting.render(
        "jev/j5_state", facts=list(ctx.get("facts") or []), scene=str(ctx.get("scene") or ""),
        present="、".join(dict.fromkeys(present)) or "无", names="、".join(dict.fromkeys(shown)) or "无",
        player_action=action, sentence=sentence.strip(), paragraph="")


def j5_questions() -> dict[str, Any]:
    q = nar.j5_questions()
    return {k: {"type": "noul", "instructions": q[k]} for k in nar.SENTENCE_QUESTIONS}


def hints_for(ctx: Mapping[str, Any], sentence: str) -> dict[str, float]:
    """Mock 的提示：叙述模块自己的字面启发（真 Jev 不看）。"""
    return {
        "extra": 0.0,
        "mind": 0.9 if nar.mind_heuristic(sentence) else 0.0,
        "puppet": 0.9 if nar.puppet_heuristic(sentence, player_spoke=bool(ctx.get("spoken"))) else 0.0,
    }


async def evaluate(cfg: EvalConfig, data: Mapping[str, Any], items: Sequence[Mapping[str, Any]], *,
                   lang: str | None = None) -> list[dict[str, Any]]:
    settings = cfg.eval_settings()
    llm = cfg.open_llm(settings, claude=False)
    contexts = {c["id"]: c for c in data["contexts"]}
    try:
        people: set[str] | None = set(cfg.world()[1].chars)
    except Exception:  # noqa: BLE001 — 没有内容库也能跑：称呼表里的都当作在场
        people = None

    async def one(item: Mapping[str, Any]) -> dict[str, Any]:
        ctx = contexts[item["context"]]
        rec: dict[str, Any] = {"id": item["id"], "context": item["context"],
                               "labels": {k: bool(item["labels"].get(k)) for k in scoring.CHECK_LABELS},
                               "p": {}, "failed": False}
        with tagged(str(item["id"])):
            try:
                ans = await llm.jev.ask("J5", j5_state(ctx, str(item["sentence"]), people), j5_questions(),
                                        hints=hints_for(ctx, str(item["sentence"])))
            except LLMError as e:
                rec.update({"failed": True, "error": f"{type(e).__name__}: {e}"})
                return rec
        rec["p"] = {k: float((ans.get(k) or {}).get("noul", 0.0)) for k in scoring.CHECK_LABELS}
        call = llm.jev.last(str(item["id"]), "J5")
        rec["seconds"] = call.seconds if call is not None else None
        return rec

    try:
        with lang_prompts(lang or cfg.lang):
            return await gather_limited(cfg.parallel, [lambda it=it: one(it) for it in items])
    finally:
        await llm.aclose()


def summarize(cfg: EvalConfig, recs: Sequence[Mapping[str, Any]], n: int) -> dict[str, Any]:
    th = float(cfg.settings.jev.thresholds.narration_flag)
    st = scoring.checks_stats(recs, th)
    tables = [table(
        f"门槛 narration_flag = {th:.2f}", ["指标", "值"],
        [["违规句检出率（至少一项过门槛）", frac(st["detected"], st["violating"])],
         ["干净句误杀率（任一项过门槛）", frac(st["false_kills"], st["clean"])]])]
    tables.append(table(
        "各标签（同一门槛）", ["标签", "精确率", "召回率", "准确率", "TP", "FP", "FN", "TN"],
        [[LABEL_NAME[k], pct(v["precision"]), pct(v["recall"]), pct(v["accuracy"]), v["tp"], v["fp"], v["fn"], v["tn"]]
         for k, v in st["labels"].items()]))
    sw = scoring.checks_sweep(recs, SWEEP)
    tables.append(table(
        "门槛扫描", ["narration_flag", "检出率", "误杀率", "过闸"],
        [[f"{s['threshold']:.2f}" + ("（现用）" if abs(s["threshold"] - th) < 1e-9 else ""),
          frac(s["detected"], s["violating"]), frac(s["false_kills"], s["clean"]), scoring.checks_gate(s)] for s in sw]))
    ok = [r for r in recs if not r.get("failed")]
    missed = [r for r in ok if any(r["labels"].values()) and not any(scoring.flags(r["p"], th).values())]
    killed = [r for r in ok if not any(r["labels"].values()) and any(scoring.flags(r["p"], th).values())]

    def ps(r: Mapping[str, Any]) -> str:
        return "、".join(f"{k} {r['p'].get(k, 0.0):.2f}" for k in scoring.CHECK_LABELS)

    tables.append(table("漏掉的违规句", ["条目", "标注", "各题的概率"],
                        [[r["id"], "、".join(k for k, v in r["labels"].items() if v), ps(r)] for r in missed]))
    tables.append(table("误杀的干净句", ["条目", "过门槛的题", "各题的概率"],
                        [[r["id"], "、".join(k for k, v in scoring.flags(r["p"], th).items() if v), ps(r)] for r in killed]))
    best = scoring.checks_recommend(recs, SWEEP)
    passed = scoring.checks_gate(st)
    detail = (f"门槛 {th:.2f}：检出率 {pct(st['detection'])}（{st['detected']}/{st['violating']}），"
              f"误杀率 {pct(st['false_kill'])}（{st['false_kills']}/{st['clean']}）；要求检出不低于 80%、误杀不高于 10%。")
    if best is not None:
        detail += f" 扫描里过闸且检出最高的门槛：{best['threshold']:.2f}（检出 {pct(best['detection'])}，" \
                  f"误杀 {pct(best['false_kill'])}）。"
    else:
        detail += " 扫描的门槛里没有一个同时满足两项。"
    lat = [r["seconds"] for r in recs if r.get("seconds") is not None]
    failed = sum(1 for r in recs if r.get("failed"))
    summary = {"at_threshold": st, "sweep": sw, "recommended": best, "failed": failed,
               "latency": {"median_s": median(lat), "p95_s": percentile(lat, 95)}}
    notes = [f"J5 请求时延中位 {secs(median(lat))}、P95 {secs(percentile(lat, 95))}；没答上来 {failed} 条。"]
    gates = [gate("J5 核对（7.12）", passed, detail, "只留字符串检查（route.check = 'rules_only'），并把 C3 的长度上限调低")]
    return make_result(cfg, "checks", n=n, summary=summary, tables=tables, gates=gates, notes=notes, items=list(recs))


async def run(cfg: EvalConfig) -> dict[str, Any]:
    data = cfg.dataset("narration_checks")
    items = cfg.take(data["items"])
    recs = await evaluate(cfg, data, items)
    return summarize(cfg, recs, len(items))
