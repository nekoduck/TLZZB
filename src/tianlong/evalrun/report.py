"""evals/report.md：各套件的表、7.12 的路由结论与建议的门槛、因为缺钥匙没跑的部分。

报告只从各套件的结果（evals/results/<套件>.json 的内容）生成，所以可以不重跑、单独重出。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tianlong.evalrun.common import SKIP, EvalConfig, gate_text, pct, read_result, render_table

ORDER = ("classify", "decisions", "checks", "zh_en", "anticheat", "refusals", "knowledge", "perspective",
         "latency_cost")
TITLES = {
    "classify": "归类集（11.6，J1）",
    "decisions": "抉择集（11.6，J2）",
    "checks": "核对集（11.6，J5）",
    "zh_en": "中英对比与稳定性（11.6）",
    "anticheat": "防作弊集（11.5）",
    "refusals": "拒绝集（11.5）",
    "knowledge": "知识边界（11.5）",
    "perspective": "视角（11.5）",
    "latency_cost": "时延与成本（11.7）",
}
MARK = {True: "通过", False: "未过", None: "未判"}
FINDINGS = "findings.md"  # 结果目录里手写的一份记录（有就原样并进报告）：读结果后发现的问题


def _gate(res: Mapping[str, Any] | None, i: int = 0) -> Mapping[str, Any] | None:
    gs = (res or {}).get("gates") or []
    return gs[i] if len(gs) > i else None


def verdicts(results: Mapping[str, Mapping[str, Any]], thresholds: Mapping[str, float]) -> dict[str, Any]:
    """7.12：每类判断走哪条路、建议的门槛。没跑的套件维持 config.toml 的现值，并说明。"""
    route = {"classify": "jev_first", "decide_soul": "jev_first", "decide_named": "jev_first", "check": "jev_first"}
    th = dict(thresholds)
    rows: list[list[str]] = []
    why: list[str] = []

    cl = results.get("classify")
    g = _gate(cl)
    if g is None or g.get("passed") is None:
        rows.append(["J1 归类", "未判", "没有跑出 J1 的结果", "维持 classify = 'jev_first'"])
    else:
        st = (cl or {})["summary"].get("jev_gate", {})
        core, best = st.get("core", {}), st.get("recommended")
        seen = f"过门槛 {pct(core.get('coverage'))}，其上一致 {pct(core.get('accuracy'))}"
        if g["passed"]:
            rows.append(["J1 归类", "通过", seen, "留在 Jev：classify = 'jev_first'"])
        elif best is not None:
            th["choice_confidence"] = best["conf"]
            rows.append(["J1 归类", "换门槛后通过", seen + f"；choice_confidence 取 {best['conf']:.2f} 时覆盖 "
                         f"{pct(best['coverage'])}、一致 {pct(best['accuracy'])}",
                         f"留在 Jev：classify = 'jev_first'，choice_confidence = {best['conf']:.2f}"])
        else:
            route["classify"] = "claude_first"
            rows.append(["J1 归类", "未过", seen, "默认走 C1：classify = 'claude_first'"])
            why.append("归类没过闸：按 7.12 该项默认走 C1。没有 Claude 的钥匙时解析器仍会退回 J1（4.3 的退让），游戏照常能玩。")
            summary = (cl or {})["summary"]
            routes = summary.get("routes", {})
            ra = (routes.get("rules_only") or {}).get("core", {}).get("accuracy")
            ja = (routes.get("jev") or {}).get("core", {}).get("accuracy")
            if ra is not None and ja is not None and ra > ja:
                why.append(f"只用规则的归类（六项全对 {pct(ra)}）比 J1（{pct(ja)}）还准：没有 Claude 的钥匙时，"
                           "把 classify 设成 'rules_only' 比退回 J1 好。")
            elif ra is not None and ja is not None:
                why.append(f"没有 Claude 的钥匙时退回 J1（六项全对 {pct(ja)}）比只用规则（{pct(ra)}）准："
                           "classify 不必为此改成 'rules_only'。")
            raw = summary.get("jev_raw") or {}
            landing = summary.get("jev_landing") or summary.get("jev_repaired")  # 旧结果文件里叫 jev_repaired
            if raw and landing is not None:
                seen_raw = "、".join(f"{k} {pct((raw.get(k) or {}).get('accuracy'))}"
                                    for k in ("kind", "verb", "target", "act", "to", "asks") if k in raw)
                why.append(f"直接看 J1 各题的答案：{seen_raw}。kind、verb 两题都答对、整条解析链却没落成标注那类动作的 "
                           f"{landing.get('lost_after_j1')} 条（共 {core.get('n', '—')} 条）。")

    de = results.get("decisions")
    g = _gate(de)
    if g is None or g.get("passed") is None:
        rows.append(["J2 抉择", "未判", "没有跑出 J2 的结果", "维持 decide_soul = 'jev_first'"])
    else:
        js = (de or {})["summary"]["jev"]
        st, best = js.get("gate", {}), js.get("recommended")
        seen = f"过门槛 {pct(st.get('coverage'))}，其上与原著做法一致 {pct(st.get('accuracy'))}"
        if g["passed"]:
            rows.append(["J2 抉择", "通过", seen, "有魂、有名角色都留在 Jev：decide_soul = decide_named = 'jev_first'"])
        elif best is not None and float(th["choice_confidence"]) == float(thresholds["choice_confidence"]):
            # choice_confidence 是 J1、J2 共用的：归类那边没动它，才按抉择集的扫描改
            th["choice_confidence"] = best["conf"]
            rows.append(["J2 抉择", "换门槛后通过", seen + f"；choice_confidence 取 {best['conf']:.2f} 时覆盖 "
                         f"{pct(best['coverage'])}、一致 {pct(best['accuracy'])}",
                         f"留在 Jev：decide_soul = 'jev_first'，choice_confidence = {best['conf']:.2f}"])
        else:
            route["decide_soul"] = "claude_first"
            rows.append(["J2 抉择", "未过", seen, "有魂角色改为 decide_soul = 'claude_first'；有名角色仍用 Jev"])

    ck = results.get("checks")
    g = _gate(ck)
    if g is None or g.get("passed") is None:
        rows.append(["J5 核对", "未判", "没有跑出 J5 的结果", "维持 check = 'jev_first'"])
    else:
        cs = (ck or {})["summary"]
        st, best = cs.get("at_threshold", {}), cs.get("recommended")
        seen = f"门槛 {float(thresholds.get('narration_flag', 0.7)):.2f}：检出 {pct(st.get('detection'))}，误杀 {pct(st.get('false_kill'))}"
        if g["passed"]:
            rows.append(["J5 核对", "通过", seen, "留在 Jev：check = 'jev_first'"])
        elif best is not None:
            th["narration_flag"] = best["threshold"]
            rows.append(["J5 核对", "换门槛后通过", seen + f"；门槛取 {best['threshold']:.2f} 时检出 {pct(best['detection'])}、"
                         f"误杀 {pct(best['false_kill'])}",
                         f"留在 Jev：check = 'jev_first'，narration_flag = {best['threshold']:.2f}"])
        else:
            route["check"] = "rules_only"
            rows.append(["J5 核对", "未过", seen, "只留字符串检查：check = 'rules_only'，并把 narration.max_len 调低"])
    rows.append(["J3、J4、J6", "不设硬门槛", "试玩时看日志", "维持 jev_first"])
    lang = None
    ze = results.get("zh_en")
    if ze is not None:
        zs = ze["summary"]
        zh, en = zs.get("total_right", {}).get("zh"), zs.get("total_right", {}).get("en")
        stab = (zs.get("stability") or {}).get("all") or {}
        if zh and en and zh[1] and en[1]:
            a, b = zh[0] / zh[1], en[0] / en[1]
            lang = "en" if b - a >= 0.03 else "zh"
            verdict = "英文高出 3 个点以上：改 jev.lang = 'en'" if lang == "en" else \
                "相差不到 3 个点，算打平：维持 jev.lang = 'zh'"
            rows.append(["问法语言", "—", f"三类合计答对：中文 {pct(a)}，英文 {pct(b)}；"
                         f"重复 5 次判定全同 {pct(stab.get('rate'))}", verdict])
    return {"route": route, "thresholds": th, "rows": rows, "notes": why, "lang": lang}


def config_block(v: Mapping[str, Any]) -> str:
    r, t = v["route"], v["thresholds"]
    lines = ["```toml"]
    if v.get("lang"):
        lines += ["[jev]", f"lang = '{v['lang']}'", ""]
    lines.append("[jev.thresholds]")
    for k in ("choice_confidence", "choice_margin", "needs_thought", "noul_yes", "sample_floor", "narration_flag"):
        if k in t:
            lines.append(f"{k} = {float(t[k]):.2f}")
    lines += ["", "[route]"]
    for k in ("classify", "decide_soul", "decide_named", "check"):
        lines.append(f"{k} = '{r[k]}'")
    lines.append("```")
    return "\n".join(lines)


def section(suite: str, res: Mapping[str, Any]) -> str:
    m = res["meta"]
    mode = "Mock 模型" if m.get("mock") else str(m.get("jev_model"))
    out = [f"## {TITLES.get(suite, suite)}", "",
           f"- 命令：`python -m tianlong eval {suite}`；{mode}；{m.get('n')} 条；生成于 {m.get('generated')}"
           + (f"；只跑了前 {m['limit']} 条（--limit）" if m.get("limit") else "") + "。"]
    for g in res.get("gates") or []:
        out.append(f"- {gate_text(g)}" + (f" 达不到时：{g['fallback']}。" if g.get("passed") is False and g.get("fallback") else ""))
    for s in res.get("skipped") or []:
        out.append(f"- {SKIP}：{s}")
    for n in res.get("notes") or []:
        out.append(f"- {n}")
    for t in res.get("tables") or []:
        out += ["", render_table(t)]
    return "\n".join(out)


def build(cfg: EvalConfig, results: Mapping[str, Mapping[str, Any]]) -> str:
    th = cfg.settings.jev.thresholds.model_dump()
    v = verdicts(results, th)
    any_res = next(iter(results.values()), None)
    mock = bool(any_res and any_res["meta"].get("mock"))
    out = ["# 评测报告（方案 11.5、11.6、11.7）", ""]
    out.append(f"- 由 `python -m tianlong eval all` 生成；各套件的逐条结果在 `{_rel(cfg)}/<套件>.json`。")
    out.append(f"- 模型：{'Mock（替身，不联网；数字只说明管道是通的）' if mock else cfg.jev_model}；问法语言：{cfg.lang}；"
               f"门槛（config.toml 现值）：" + "、".join(f"{k} {float(x):.2f}" for k, x in th.items()) + "。")
    out.append(f"- Claude：{'有钥匙' if cfg.settings.claude_api_key() else '没有 ANTHROPIC_API_KEY，凡要 C 模型的栏目都记 ' + SKIP}。")
    missing = [s for s in ORDER if s not in results]
    if missing:
        out.append("- 这次没有结果的套件：" + "、".join(missing) + "。")
    out += ["", "## 结论：7.12 的路由与门槛", "",
            render_table({"title": "", "headers": ["判断", "闸", "实测", "路由"], "rows": v["rows"]}), ""]
    for n in v["notes"]:
        out.append(f"- {n}")
    out += ["", "建议写进 `config.toml` 的取值（本报告不改配置；结论另记进 `DECISIONS.md`）：", "", config_block(v), ""]
    other = []
    for s in ("anticheat", "refusals", "knowledge", "perspective", "latency_cost"):
        for g in (results.get(s) or {}).get("gates") or []:
            other.append([g["name"], MARK[g.get("passed")], g["detail"]])
    if other:
        out += [render_table({"title": "其余各道闸", "headers": ["闸", "结果", "实测"], "rows": other}), ""]
    skipped = [(s, x) for s in ORDER for x in (results.get(s) or {}).get("skipped") or []]
    out += ["## 没跑的部分", ""]
    if skipped:
        for s, x in skipped:
            out.append(f"- `{s}`：{x}")
        out.append("- 有了钥匙之后整套重跑：`python -m tianlong eval all`（单跑一个套件把 all 换成套件名）。")
    else:
        out.append("- 没有。")
    out.append("")
    notes = cfg.out / FINDINGS
    if notes.is_file():
        out += ["## 读结果后的记录", "", notes.read_text(encoding="utf-8").strip(), ""]
    for s in ORDER:
        if s in results:
            out += [section(s, results[s]), ""]
    return "\n".join(out).rstrip() + "\n"


def _rel(cfg: EvalConfig) -> str:
    try:
        return str(cfg.out.relative_to(cfg.root))
    except ValueError:
        return str(cfg.out)


def load_results(cfg: EvalConfig) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in ORDER:
        r = read_result(cfg, s)
        if r is not None:
            out[s] = r
    return out


def write(cfg: EvalConfig, results: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """写 evals/report.md（Mock 或另指了结果目录时写在结果目录里）；返回路径。"""
    res = dict(results) if results is not None else load_results(cfg)
    path = cfg.report_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(cfg, res), encoding="utf-8")
    return str(path)
