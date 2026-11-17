"""每个套件用 Mock、--limit 5 跑一遍：不联网，写出结果文件；报告能从结果文件重出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tianlong import evalrun, prompting
from tianlong.config import Settings
from tianlong.evalrun import checks, classify, decisions, gameplay, report
from tianlong.evalrun.common import EN_DIR, EvalConfig, JevTap, lang_prompts, question_words, tagged
from tianlong.parser import intake


@pytest.mark.parametrize("suite", list(evalrun.SUITES))
def test_suite_runs_in_mock_and_writes_a_result(suite: str, settings: Settings, tmp_path: Path) -> None:
    lines: list[str] = []
    code = evalrun.main(settings, suite, mock=True, limit=5, out=tmp_path, repeat=2, echo=lines.append)
    assert code == 0, "\n".join(lines)
    path = tmp_path / f"{suite}.json"
    assert path.is_file()
    res = json.loads(path.read_text(encoding="utf-8"))
    assert res["suite"] == suite and res["meta"]["mock"] is True and res["meta"]["limit"] == 5
    assert res["meta"]["n"] > 0 and res["tables"], suite
    assert all(t["headers"] and all(len(r) == len(t["headers"]) for r in t["rows"]) for t in res["tables"])
    text = "\n".join(lines)
    assert f"== eval {suite}" in text and "| --- |" in text and str(path) in text
    assert not (tmp_path / "report.md").exists()  # 单跑一个套件不动报告


def test_unknown_suite_lists_the_suites(settings: Settings, tmp_path: Path) -> None:
    lines: list[str] = []
    assert evalrun.main(settings, "nope", mock=True, out=tmp_path, echo=lines.append) == 2
    text = "\n".join(lines)
    assert "nope" in text and all(s in text for s in evalrun.SUITES) and "all" in text
    assert evalrun.main(settings, None, echo=lines.append) == 2
    assert evalrun.resolve("narration") == "checks" and evalrun.resolve("latency") == "latency_cost"
    assert not list(tmp_path.iterdir())


async def test_mock_gates_hold_on_whole_sets(cfg: EvalConfig) -> None:
    """Mock 下的两道闸（M3）在前几条上成立；归类照标注的一遍里请求都建了出来、答复都是拒绝。"""
    cfg.limit = 4
    res = await gameplay.run_anticheat(cfg)
    assert res["summary"]["mock"]["passed"] == 4 and res["summary"]["mock"]["n"] == 4
    assert res["gates"][0]["passed"] is True
    res = await gameplay.run_refusals(cfg)
    lab = res["summary"]["mock_labeled"]
    assert lab["passed"] == 4 and lab["requests"] == 4 and res["gates"][0]["passed"] is True
    assert all(r["replies"] and set(r["replies"]) == {"refuse"} for r in res["items"]["mock_labeled"])
    assert "jev" not in res["items"]  # Mock 下不跑真模型的一遍


async def test_classify_without_keys_only_runs_the_rules(settings: Settings, tmp_path: Path) -> None:
    """没有钥匙、又不是 Mock：J1 与 C1 两栏跳过并说明，规则一栏照跑；不发任何请求。"""
    cfg = EvalConfig(settings=settings, mock=False, limit=5, out_dir=tmp_path, echo=lambda _s: None)
    res = await classify.run(cfg)
    assert list(res["items"]) == ["rules_only"] and len(res["items"]["rules_only"]) == 5
    assert len(res["skipped"]) == 2 and "ANTHROPIC_API_KEY" in res["skipped"][1]
    assert res["gates"] == []
    dec = await decisions.run(cfg)  # Jev 没有钥匙：每条都记成没答上来，不抛异常
    assert dec["summary"]["jev"]["failed"] == 5 and dec["gates"][0]["passed"] in (None, False)
    assert any("ANTHROPIC_API_KEY" in s for s in dec["skipped"])


async def test_classify_records_confidence_from_the_j1_answers(cfg: EvalConfig) -> None:
    items = cfg.dataset("classify")["items"][:8]
    recs = await classify.evaluate(cfg, items, "jev")
    assert [r["id"] for r in recs] == [i["id"] for i in items]
    for r in recs:
        assert r["via"] in ("jev", "rule") and set(r["fields"]) == set(classify.scoring.FIELDS)
        assert r["conf"] == 1.0 and r["failed"] is False  # Mock 的答案概率都是 1
        if not r.get("by_rule"):
            assert r["unsure"] is False and r["margin"] == 1.0 and "kind" in r["confs"]
    rules = await classify.evaluate(cfg, items, "rules_only")
    assert all(r["via"] == "rule" and r["conf"] is None for r in rules)
    # Mock 的 J1 照规则猜测作答：两条路的结论一样
    assert [r["pred"] for r in recs] == [r["pred"] for r in rules]


async def test_decision_and_check_requests_follow_the_templates(cfg: EvalConfig) -> None:
    item = cfg.dataset("decisions")["items"][0]
    defs, st, _ = cfg.build(item["setup"])
    text = decisions.state_text(st, defs, item["char"], item["situation"])
    assert f"【刚才】{item['situation']}" in text and text.count("【刚才】") == 1
    for head in ("【你是谁】", "【此刻】", "【在场】", "【你记得】", "【你的状态】", "【你的打算】"):
        assert head in text
    qs = decisions.questions(item)
    crit = qs["action"]["criteria"]
    assert list(crit) == [c["id"] for c in item["candidates"]] + ["none"]
    assert not any("你本来要做的" in v for v in crit.values())  # 不给任何候选加 ★ 前缀
    assert qs["speak"]["type"] == qs["needs_thought"]["type"] == "noul"
    data = cfg.dataset("narration_checks")
    ctx = next(c for c in data["contexts"] if c.get("spoken"))
    state = checks.j5_state(ctx, "一句要核对的话。", set(cfg.world()[1].chars))
    assert "【要核对的句子】一句要核对的话。" in state and ctx["facts"][0] in state
    assert set(checks.j5_questions()) == {"extra", "mind", "puppet"}
    recs = await checks.evaluate(cfg, data, data["items"][:6])
    assert all(set(r["p"]) == {"extra", "mind", "puppet"} and not r["failed"] for r in recs)
    # 玩家说没说话各记一栏：【玩家的话】一节碍不碍事，从结果里直接看得出
    by_ctx = {c["id"]: bool(c.get("spoken")) for c in data["contexts"]}
    recs = await checks.evaluate(cfg, data, data["items"][:30])
    assert [r["spoken"] for r in recs] == [by_ctx[r["context"]] for r in recs] and {r["spoken"] for r in recs} == {True, False}
    res = checks.summarize(cfg, recs, len(recs))
    split = res["summary"]["by_spoken"]
    assert split["spoken"]["n"] == sum(r["spoken"] for r in recs) and split["spoken"]["n"] + split["silent"]["n"] == 30
    assert split["spoken"]["detected"] + split["silent"]["detected"] == res["summary"]["at_threshold"]["detected"]
    tab = next(t for t in res["tables"] if "有没有说话" in t["title"])
    assert [row[1] for row in tab["rows"]] == [str(split["spoken"]["n"]), str(split["silent"]["n"])]


def test_check_state_puts_the_player_words_where_the_narrator_does(cfg: EvalConfig) -> None:
    """核对集的 J5 状态与叙述模块给 J5 的一样（D-356）：原话单放在标明的【玩家的话】一节，【你做的事】里不夹。

    这一节排在模板的哪里由 prompts/jev/j5_state.md 定，这里不钉死先后，只钉与模板直接渲染的结果相同。
    """
    data = cfg.dataset("narration_checks")
    people = set(cfg.world()[1].chars)
    said = [c for c in data["contexts"] if c.get("spoken")]
    silent = [c for c in data["contexts"] if not c.get("spoken")]
    assert said and silent
    for ctx in said:
        state = checks.j5_state(ctx, "一句要核对的话。", people)
        head, sep, tail = state.partition("【玩家的话】")
        assert sep and state.count("【玩家的话】") == 1, ctx["id"]
        note, words, *_rest = tail.split("\n")
        assert "原话" in note and words == ctx["spoken"], ctx["id"]  # 这一节自带说明，下一行是一字不改的原话
        lines = state.split("\n")
        assert f"【你做的事】{ctx['action']}" in lines and "【要核对的句子】一句要核对的话。" in lines, ctx["id"]
        assert state.count(ctx["spoken"]) == 1 + sum(t.count(ctx["spoken"]) for t in (*ctx["facts"], ctx["action"])), \
            ctx["id"]  # 原话只在它自己那一节出现
        # 与模板直接渲染的结果一字不差：评测量的就是产品里那份提示
        names = [v for v in ctx["names"].values() if v != "你"]
        there = [v for k, v in ctx["names"].items() if v != "你" and k in people]
        assert state == prompting.render(
            "jev/j5_state", facts=ctx["facts"], scene=ctx["scene"], present="、".join(dict.fromkeys(there)) or "无",
            names="、".join(dict.fromkeys(names)) or "无", player_action=ctx["action"], spoken=ctx["spoken"],
            sentence="一句要核对的话。", paragraph=""), ctx["id"]
    for ctx in silent:
        state = checks.j5_state(ctx, "一句要核对的话。", people)
        assert "【玩家的话】" not in state and "原话" not in state, ctx["id"]  # 没说话就没有这一节
        assert "【要核对的句子】一句要核对的话。" in state.split("\n"), ctx["id"]


def test_english_templates_have_the_same_keys_and_swap_cleanly() -> None:
    root = prompting.root()
    zh_words = intake.words()
    zh_j2 = question_words("j2a_questions")
    zh_j5 = question_words("j5_questions")
    with lang_prompts("en"):
        assert prompting.root() != root
        en_words = intake.words()
        assert set(en_words) == set(zh_words)
        assert en_words["kind"] != zh_words["kind"] and en_words["kind"].isascii() is False  # 节名仍是中文
        assert all(("{事}" in en_words[k]) == ("{事}" in zh_words[k]) for k in zh_words)
        assert set(question_words("j2a_questions")) == set(zh_j2)
        assert set(question_words("j5_questions")) == set(zh_j5)
        assert question_words("j2a_questions")["none"] == "Do nothing"
        assert checks.j5_questions()["mind"]["instructions"].startswith("This sentence")
    assert prompting.root() == root and intake.words() == zh_words
    assert checks.j5_questions()["mind"]["instructions"] == zh_j5["mind"]
    with lang_prompts("zh"):
        assert prompting.root() == root
    for name in ("j2a_questions.md", "j5_questions.md"):
        assert isinstance(yaml.safe_load((EN_DIR / name).read_text(encoding="utf-8")), dict)


async def test_jev_tap_files_calls_under_the_current_item(cfg: EvalConfig) -> None:
    llm = cfg.open_llm(claude=False)
    assert isinstance(llm.jev, JevTap)
    q = {"a": {"type": "noul", "instructions": "x"}}
    with tagged("one"):
        await llm.jev.ask("J5", "s1", q, hints={"a": 0.9})
    with tagged("two"):
        await llm.jev.ask("J5", "s2", q)
    assert llm.jev.last("one").answers["a"]["noul"] == 0.9 and llm.jev.last("two", "J5").state == "s2"
    assert llm.jev.last("one", "J1") is None and llm.jev.last("three") is None
    with pytest.raises(Exception, match="switched off"):
        await llm.claude.call_json("C1", [], "", dict)
    await llm.aclose()


def test_report_is_rebuilt_from_result_files(cfg: EvalConfig) -> None:
    th = cfg.settings.jev.thresholds.model_dump()
    meta = {"generated": "now", "mock": True, "jev_model": "m", "lang": "zh", "limit": None, "n": 10}

    def res(suite: str, gates: list, summary: dict, skipped: list | None = None) -> dict:
        return {"suite": suite, "meta": meta, "summary": summary, "gates": gates, "skipped": skipped or [],
                "notes": [], "tables": [{"title": "t", "headers": ["a", "b"], "rows": [["1", "2"]], "note": ""}]}

    g = {"name": "g", "detail": "d", "fallback": "f"}
    results = {
        "classify": res("classify", [{**g, "passed": False}], {
            "jev_gate": {"core": {"coverage": 0.8, "accuracy": 0.9, "n": 10}, "recommended": None},
            "routes": {"rules_only": {"core": {"accuracy": 0.6}}, "jev": {"core": {"accuracy": 0.78}}},
            "jev_raw": {"kind": {"accuracy": 0.96}, "verb": {"accuracy": 0.84}},
            "jev_landing": {"lost_after_j1": 3}}, ["C1 一栏"]),
        "decisions": res("decisions", [{**g, "passed": True}], {"jev": {"gate": {"coverage": 0.6, "accuracy": 0.8},
                                                                        "recommended": None}}),
        "checks": res("checks", [{**g, "passed": False}], {"at_threshold": {"detection": 1.0, "false_kill": 0.2},
                                                           "recommended": {"threshold": 0.8, "detection": 0.96,
                                                                           "false_kill": 0.06}}),
    }
    v = report.verdicts(results, th)
    assert v["route"] == {"classify": "claude_first", "decide_soul": "jev_first", "decide_named": "jev_first",
                          "check": "jev_first"}
    assert v["thresholds"]["narration_flag"] == 0.8 and v["thresholds"]["choice_confidence"] == th["choice_confidence"]
    # 归类没过闸时的几句说明都从这次的数字里来：退回 J1 与只用规则谁准、J1 各题的准确率、落地时丢了几条
    notes = "\n".join(v["notes"])
    assert "退回 J1（六项全对 78.0%）比只用规则（60.0%）准" in notes and "rules_only' 比退回 J1 好" not in notes
    assert "kind 96.0%、verb 84.0%" in notes and "没落成标注那类动作的 3 条（共 10 条）" in notes
    assert "丢了不少" not in notes
    # 抉择没过、扫描里也没有过闸的门槛：有魂角色改 claude_first；核对没过又没有可用的门槛：只留字符串检查
    results["decisions"]["gates"][0]["passed"] = False
    results["checks"]["summary"]["recommended"] = None
    v = report.verdicts(results, th)
    assert v["route"]["decide_soul"] == "claude_first" and v["route"]["decide_named"] == "jev_first"
    assert v["route"]["check"] == "rules_only" and v["thresholds"] == th
    # 归类换门槛后过闸：留在 Jev，并给出新的 choice_confidence
    results["classify"]["summary"]["jev_gate"]["recommended"] = {"conf": 0.7, "coverage": 0.72, "accuracy": 0.96}
    v = report.verdicts(results, th)
    assert v["route"]["classify"] == "jev_first" and v["thresholds"]["choice_confidence"] == 0.7
    assert report.verdicts({}, th)["route"]["classify"] == "jev_first"  # 没跑：维持现值
    cfg.out.mkdir(parents=True)
    for name, r in results.items():
        (cfg.out / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    path = Path(report.write(cfg))
    assert path == cfg.out / "report.md"
    text = path.read_text(encoding="utf-8")
    for want in ("# 评测报告", "## 结论：7.12 的路由与门槛", "[route]", "classify = 'jev_first'", "choice_confidence = 0.70",
                 "decide_soul = 'claude_first'", "check = 'rules_only'", "## 没跑的部分", "C1 一栏", "## 归类集", "| a | b |"):
        assert want in text, want
    assert "## 时延与成本" not in text and "latency_cost" in text  # 没有结果的套件在开头点名


async def test_raw_answers_and_the_gate_mirror_the_parser(cfg: EvalConfig) -> None:
    """J1 的答案换回实体 id（raw_of）；7.12 的「过门槛」照解析器自己的升级条件重算（confidence_of）。"""
    from tianlong import parser

    th = cfg.settings.jev.thresholds
    items = {i["id"]: i for i in cfg.dataset("classify")["items"]}
    llm = cfg.open_llm(cfg.eval_settings(classify="rules_only"))
    try:
        # 一件交接的事：物、人两题都答成了那个人，物件只在概率里排第二
        it = items["c015"]
        defs, st, _ = cfg.build(cfg.scene_setup(it["scene"]))
        base = await parser.classify(llm, st, defs, it["input"])
        menu = base.menu
        person, thing = menu.obj(it["gold"]["target2"]), menu.obj(it["gold"]["target"])
        assert person is not None and thing is not None
        ans = {
            "kind": {"type": "choice", "choice": "act", "confidence": 0.99, "probabilities": {"act": 0.99, "both": 0.01}},
            "has_speech": {"type": "noul", "noul": 0.66}, "meaningful": {"type": "noul", "noul": 0.95},
            "verb": {"type": "choice", "choice": "give", "confidence": 1.0, "probabilities": {"give": 1.0}},
            "target": {"type": "choice", "choice": person.key, "confidence": 0.8,
                       "probabilities": {person.key: 0.8, thing.key: 0.2}},
            "target2": {"type": "choice", "choice": person.key, "confidence": 0.9,
                        "probabilities": {person.key: 0.9, thing.key: 0.1}},
            "act": {"type": "choice", "choice": "request", "confidence": 0.7, "probabilities": {"request": 0.7}},
            "to": {"type": "choice", "choice": "all", "confidence": 0.8, "probabilities": {"all": 0.8}},
        }
        raw = classify.raw_of(ans, menu, th)
        assert raw["kind"] == "act" and raw["has_speech"] is True and raw["verb"] == "give"
        assert raw["targets"] == [person.ref, person.ref] and raw["to"] == [] and raw["asks"] is None
        gold_item = classify.resolved_item(it, [], st.player)
        assert classify.scoring.score_raw_item(gold_item, raw)["target"] is False
        # 解析器照自己的规则落地：kind 很有把握地说只是动作，has_speech 刚过线不算另有一句话；
        # 可物件没被选中，只有一个人 → 对不上合法动作
        intent = intake.intent_from_jev(ans, menu, th, [])
        result = base.model_copy(update={"intent": intent, "via": "jev"})
        assert intent.kind == "act" and intent.act is None and (intent.target, intent.target2) == (person.ref, None)
        bound = parser.bind(st, defs, result)
        assert bound.kind != "action"
        fields = classify.scoring.score_classify(gold_item, classify.pred_of(st, result, bound))
        assert fields["kind"] is False
        # 闸：give 看 kind、verb 与「物」那道题；act 一题（0.70）没有话就不看
        c = classify.confidence_of(ans, intent, th)
        assert c["gate_keys"] == intake.gate_keys(ans, intent) == ["kind", "verb", "target"]
        assert c["conf"] == 0.8 and round(c["margin"], 6) == 0.6 and c["noul_mid"] is False and c["unsure"] is False
        assert set(c["confs"]) == {"kind", "verb", "target", "target2", "act", "to"}
        assert classify._question_conf({**c}, "target") == 0.8 and classify._question_conf({**c}, "to") == 0.8
        assert classify._question_conf({"confs": {"target": 0.3}, "gate_keys": ["kind", "verb"]}, "target") is None
        # 不带对象的动词：对象两题答得再没把握也不算
        low = {**ans, "verb": {"type": "choice", "choice": "look", "confidence": 0.95, "probabilities": {"look": 0.95}},
               "target": {"type": "choice", "choice": "none", "confidence": 0.3, "probabilities": {"none": 0.3}}}
        look = intake.intent_from_jev(low, menu, th, [])
        c = classify.confidence_of(low, look, th)
        assert look.verb == "look" and c["gate_keys"] == ["kind", "verb"] and c["conf"] == 0.95
        assert parser.bind(st, defs, base.model_copy(update={"intent": look, "via": "jev"})).action.verb == "look"
        # has_speech 落在中间：kind 很有把握时不看；kind 把握不到 0.9 时算没把握（与 jev_unsure 同）
        mid = {**ans, "has_speech": {"type": "noul", "noul": 0.5}}
        c = classify.confidence_of(mid, intake.intent_from_jev(mid, menu, th, []), th)
        assert c["noul_mid"] is False and c["unsure"] is False
        soft = {**mid, "kind": {"type": "choice", "choice": "act", "confidence": 0.8,
                                "probabilities": {"act": 0.8, "both": 0.2}}}
        c = classify.confidence_of(soft, intake.intent_from_jev(soft, menu, th, []), th)
        assert c["noul_mid"] is True and c["unsure"] is True
        # 在 config 的门槛上，scoring.passes 与解析器的 jev_unsure 是一回事
        for a in (ans, low, mid, soft):
            c = classify.confidence_of(a, intake.intent_from_jev(a, menu, th, []), th)
            assert classify.scoring.passes(c, th.choice_confidence, th.choice_margin) is (not c["unsure"])
        assert not hasattr(classify, "repaired")
    finally:
        await llm.aclose()
