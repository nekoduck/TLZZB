"""中英对比与稳定性（方案 11.6）。

同一批样例（归类集抽 60 条、抉择集与核对集全部），问题与选项说明用中文和用英文各跑一次，状态一律中文；
另取 30 个请求（三类各 10 个）各重复 5 次，看判定是否一致。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tianlong.evalrun import checks, classify, decisions, scoring
from tianlong.evalrun.common import EvalConfig, frac, make_result, pct, stdev, table

CLASSIFY_SUBSET = 60
STABILITY_EACH = 10
LANGS = ("zh", "en")
CORE_PRED = ("kind", "verb", "action", "act", "to", "asks")


def subset(items: Sequence[Any], n: int) -> list[Any]:
    """均匀抽 n 条（按原来的顺序隔着取）。"""
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def classify_sig(rec: Mapping[str, Any]) -> str:
    return repr([rec["pred"].get(k) for k in CORE_PRED])


def checks_sig(rec: Mapping[str, Any], th: float) -> str:
    return repr(sorted(k for k, v in scoring.flags(rec.get("p") or {}, th).items() if v))


def sigs(family: str, recs: Sequence[Mapping[str, Any]], th: float) -> dict[str, Any]:
    if family == "classify":
        return {str(r["id"]): classify_sig(r) for r in recs if not r.get("failed")}
    if family == "decisions":
        return {str(r["id"]): r.get("pick") for r in recs if not r.get("failed")}
    return {str(r["id"]): checks_sig(r, th) for r in recs if not r.get("failed")}


def jitter(family: str, runs: Sequence[Sequence[Mapping[str, Any]]]) -> list[float]:
    """同一条在几次重复里概率的标准差（归类、抉择看 confidence，核对看三道题的 p）。"""
    out: list[float] = []
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for recs in runs:
        for r in recs:
            if not r.get("failed"):
                by_id.setdefault(str(r["id"]), []).append(r)
    for rs in by_id.values():
        if len(rs) < 2:
            continue
        if family == "checks":
            for k in scoring.CHECK_LABELS:
                out.append(stdev([float(r["p"].get(k, 0.0)) for r in rs]))
        else:
            vals = [float(r["conf"]) for r in rs if r.get("conf") is not None]
            if len(vals) > 1:
                out.append(stdev(vals))
    return out


async def run(cfg: EvalConfig) -> dict[str, Any]:
    th = cfg.settings.jev.thresholds
    flag = float(th.narration_flag)
    ck_data = cfg.dataset("narration_checks")
    sets = {
        "classify": cfg.take(subset(cfg.dataset("classify")["items"], CLASSIFY_SUBSET)),
        "decisions": cfg.take(cfg.dataset("decisions")["items"]),
        "checks": cfg.take(ck_data["items"]),
    }

    async def once(family: str, items: Sequence[Any], lang: str) -> list[dict[str, Any]]:
        if family == "classify":
            return await classify.evaluate(cfg, items, "jev", lang=lang)
        if family == "decisions":
            return await decisions.evaluate(cfg, items, lang=lang)
        return await checks.evaluate(cfg, ck_data, items, lang=lang)

    runs: dict[str, dict[str, list[dict[str, Any]]]] = {f: {} for f in sets}
    for lang in LANGS:
        for family, items in sets.items():
            runs[family][lang] = await once(family, items, lang)

    def cl_row(lang: str) -> list[Any]:
        recs = runs["classify"][lang]
        acc = scoring.field_accuracy(recs)
        g = scoring.gate_stats(recs, th.choice_confidence, th.choice_margin, "core")
        return [frac(*acc["core"]), frac(*acc["all"]), pct(g["coverage"]), frac(g["hits"], g["above"])]

    def dec_row(lang: str) -> list[Any]:
        recs = runs["decisions"][lang]
        g = scoring.gate_stats(recs, th.choice_confidence, th.choice_margin, "agree")
        return [frac(*scoring.agreement(recs)), pct(g["coverage"]), frac(g["hits"], g["above"])]

    def ck_row(lang: str) -> list[Any]:
        st = scoring.checks_stats(runs["checks"][lang], flag)
        return [frac(st["detected"], st["violating"]), frac(st["false_kills"], st["clean"])]

    same = {f: scoring.same_rate(sigs(f, runs[f]["zh"], flag), sigs(f, runs[f]["en"], flag)) for f in sets}
    tables = [
        table(f"归类（J1，{len(sets['classify'])} 条）", ["问法", "六项全对", "全部字段全对", "过门槛覆盖率", "过门槛样本六项全对"],
              [[lang, *cl_row(lang)] for lang in LANGS],
              note=f"中英两次判定相同（kind、动作、言语行为、对象、请求都一样）的条目：{frac(*same['classify'])}。"),
        table(f"抉择（J2a，{len(sets['decisions'])} 条）", ["问法", "与原著做法一致", "过门槛覆盖率", "过门槛样本一致率"],
              [[lang, *dec_row(lang)] for lang in LANGS],
              note=f"中英两次选了同一个候选的条目：{frac(*same['decisions'])}。"),
        table(f"核对（J5，{len(sets['checks'])} 句，门槛 {flag:.2f}）", ["问法", "违规句检出率", "干净句误杀率"],
              [[lang, *ck_row(lang)] for lang in LANGS],
              note=f"中英两次过门槛的题完全相同的句子：{frac(*same['checks'])}。"),
    ]
    # 稳定性：三类各取前 10 个请求，重复 repeat 次（中文问法）
    rep = max(2, int(cfg.repeat))
    stab_rows = []
    stab: dict[str, Any] = {}
    total_q = total_same = 0
    for family, items in sets.items():
        few = list(items[:STABILITY_EACH])
        reps = [await once(family, few, "zh") for _ in range(rep)]
        st = scoring.stability([sigs(family, r, flag) for r in reps])
        jit = jitter(family, reps)
        stab[family] = {**st, "requests": len(few), "repeats": rep,
                        "p_std_mean": sum(jit) / len(jit) if jit else None, "p_std_max": max(jit) if jit else None}
        total_q += st["questions"]
        total_same += st["same"]
        stab_rows.append([family, len(few), rep, frac(st["same"], st["questions"]),
                          "—" if not jit else f"{sum(jit) / len(jit):.3f} / {max(jit):.3f}"])
    stab_rows.append(["合计", sum(s["requests"] for s in stab.values()), rep, frac(total_same, total_q), ""])
    tables.append(table(f"稳定性：同一请求重复 {rep} 次（中文问法）", ["类别", "请求数", "重复", "判定全同", "概率的标准差（均值 / 最大）"],
                        stab_rows))

    def score(lang: str) -> tuple[int, int]:
        c = scoring.field_accuracy(runs["classify"][lang])["core"]
        d = scoring.agreement(runs["decisions"][lang])
        k = scoring.checks_stats(runs["checks"][lang], flag)
        right = c[0] + d[0] + k["detected"] + (k["clean"] - k["false_kills"])
        return right, c[1] + d[1] + k["n"]

    zh, en = score("zh"), score("en")
    notes = [f"三类合起来答对的条数：中文 {frac(*zh)}，英文 {frac(*en)}。"
             + ("两种问法打平或中文更好：维持 jev.lang = 'zh'。" if zh[0] >= en[0] else
                "英文问法多答对几条；要不要改 jev.lang 见 evals/report.md 的结论。")]
    summary = {
        "classify": {lang: {k: list(v) for k, v in scoring.field_accuracy(runs["classify"][lang]).items()} for lang in LANGS},
        "decisions": {lang: list(scoring.agreement(runs["decisions"][lang])) for lang in LANGS},
        "checks": {lang: {k: scoring.checks_stats(runs["checks"][lang], flag)[k] for k in ("detection", "false_kill")}
                   for lang in LANGS},
        "same_decision": {f: list(v) for f, v in same.items()},
        "stability": {**stab, "all": {"same": total_same, "questions": total_q,
                                      "rate": total_same / total_q if total_q else None}},
        "total_right": {"zh": list(zh), "en": list(en)},
    }
    return make_result(cfg, "zh_en", n=sum(len(v) for v in sets.values()), summary=summary, tables=tables, notes=notes,
                       items={f: {lang: runs[f][lang] for lang in LANGS} for f in sets})
