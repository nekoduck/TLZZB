"""计分函数的单元测试：用手写的「答案」验各项指标与 7.12 的闸（不碰模型、不碰世界）。"""

from __future__ import annotations

import pytest

from tianlong.evalrun import scoring
from tianlong.evalrun.assertions import action_matches


def item(gold: dict, **kw: object) -> dict:
    return {"id": "c", "scene": "s", "input": "x", "gold": gold, **kw}


def pred(**kw: object) -> dict:
    base = {"kind": "say", "verb": None, "action": None, "loc": "hall", "act": None, "to": [], "asks": None,
            "ask_menu": None, "claims": [], "cls": None, "urgent": False}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 归类集


def test_action_matches_follows_the_readme_table() -> None:
    assert action_matches("go", {"exit": "gate"}, {"verb": "go", "target": "gate"}, "hall")
    assert not action_matches("go", {"exit": "back"}, {"verb": "go", "target": "gate"}, "hall")
    assert not action_matches("flee", {"exit": "gate"}, {"verb": "go", "target": "gate"}, "hall")
    assert action_matches("hide", {"feature": "beam"}, {"verb": "hide", "target": "hall#beam"}, "hall")
    assert action_matches("search", {}, {"verb": "search", "target": None}, "hall")
    assert not action_matches("search", {"feature": "beam"}, {"verb": "search", "target": None}, "hall")
    g = {"verb": "give", "target": "tea", "target2": "boss"}
    assert action_matches("give", {"item": "tea", "target": "boss"}, g, "hall")
    assert not action_matches("give", {"item": "tea", "target": "guest"}, g, "hall")
    assert action_matches("use", {"item": "pill"}, {"verb": "use", "target": "pill"}, "hall")  # 不写 target2：自己用
    assert action_matches("examine", {"feature": "beam"}, {"verb": "examine", "target": "hall#beam"}, "hall")
    assert action_matches("examine", {"item": "tea"}, {"verb": "examine", "target": "tea"}, "hall")
    assert action_matches("study", {"item": "book", "art": "sword"}, {"verb": "study", "target": "book", "art": "sword"},
                          "hall")
    assert not action_matches("study", {"item": "book", "art": "steps"}, {"verb": "study", "target": "book",
                                                                           "art": "sword"}, "hall")
    assert action_matches("setpiece", {"name": "laugh", "situation": "bout"}, {"verb": "setpiece", "name": "laugh"},
                          "hall")
    assert action_matches("wait", {"ticks": 1}, {"verb": "wait"}, "hall")


def test_to_and_asks_comparisons() -> None:
    assert scoring.to_equal([], ["*"]) and scoring.to_equal(["*"], []) and scoring.to_equal([], [])
    assert scoring.to_equal(["a"], ["a"]) and not scoring.to_equal(["b"], ["a"]) and not scoring.to_equal([], ["a"])
    assert scoring.to_equal(["a", "b"], ["b", "a"]) and not scoring.to_equal(["a"], [])
    rel = {"type": "release", "target": "girl"}
    assert scoring.asks_equal({"type": "release", "target": "girl"}, rel)
    assert not scoring.asks_equal({"type": "release", "target": "boy"}, rel)
    assert not scoring.asks_equal(None, rel)
    # other：候选里本来就没有，一条也不该选
    assert scoring.asks_equal(None, {"type": "other", "note": "…"})
    assert not scoring.asks_equal({"type": "come_along"}, {"type": "other"})
    # 候选里没有标注的那条：应当一条也不选
    menu = [{"type": "come_along"}, {"type": "release", "target": "boy"}]
    assert scoring.asks_equal(None, rel, menu) and not scoring.asks_equal({"type": "come_along"}, rel, menu)
    assert not scoring.asks_equal(None, rel, [*menu, {"type": "release", "target": "girl"}])
    # art 为 null 的求教：只比种类
    assert scoring.asks_equal({"type": "teach_art", "art": "x"}, {"type": "teach_art", "art": None})


def test_score_fields_perfect_and_partial() -> None:
    gold = {"kind": "both", "verb": "show", "target": "tea", "target2": "boss", "act": "request", "to": ["boss"],
            "asks": {"type": "give_item", "item": "coin"}, "claims": ["F1"], "cls": "costly", "urgent": True}
    good = pred(kind="both", verb="show", action={"verb": "show", "params": {"item": "tea", "target": "boss"}},
                act="request", to=["boss"], asks={"type": "give_item", "item": "coin"}, claims=["F1"], cls="costly",
                urgent=True)
    s = scoring.score_classify(item(gold), good)
    assert all(v is True for v in s.values()) and scoring.all_right(s) and scoring.all_right(s, scoring.CORE)
    bad = dict(good, to=[], cls="effort", claims=["F1", "F2"])
    s = scoring.score_classify(item(gold), bad)
    assert [k for k, v in s.items() if v is False] == ["to", "claims", "cls"]
    assert not scoring.all_right(s, scoring.CORE) and not scoring.all_right(s)
    # 只有 cls 错：六项仍全对
    s = scoring.score_classify(item(gold), dict(good, cls="effort"))
    assert scoring.all_right(s, scoring.CORE) and not scoring.all_right(s)
    # skip 里的字段不计分
    s = scoring.score_classify(item(gold, skip=["cls"]), dict(good, cls="effort"))
    assert s["cls"] is None and scoring.all_right(s)


def test_score_fields_only_kind_for_non_actions_and_alt() -> None:
    s = scoring.score_classify(item({"kind": "impossible"}), pred(kind="impossible"))
    assert s["kind"] is True and all(v is None for k, v in s.items() if k != "kind")
    s = scoring.score_classify(item({"kind": "unclear"}), pred(kind="say", act="tell"))
    assert s["kind"] is False and s["act"] is None
    # 纯动作：act、to、asks 的计分范围
    s = scoring.score_classify(item({"kind": "act", "verb": "wait"}), pred(kind="act", verb="wait",
                                                                             action={"verb": "wait", "params": {}}))
    assert s["verb"] and s["target"] and s["act"] and s["to"] is None and s["asks"] is None and s["cls"] is None
    # alt：任一种对上也算对
    it = item({"kind": "act", "verb": "examine", "target": "tea"}, alt=[{"verb": "search", "target": None}])
    p = pred(kind="act", verb="search", action={"verb": "search", "params": {}})
    assert scoring.all_right(scoring.score_classify(it, p))
    assert not scoring.all_right(scoring.score_classify(item(it["gold"]), p))


def records(spec: list[tuple[float, bool]]) -> list[dict]:
    return [{"conf": c, "margin": 0.5, "core": ok, "all": ok, "agree": ok, "fields": {"kind": ok}} for c, ok in spec]


def test_gate_buckets_sweep_and_recommendation() -> None:
    recs = records([(0.9, True)] * 6 + [(0.6, True)] * 2 + [(0.6, False)] + [(0.3, False)])
    g = scoring.gate_stats(recs, 0.55, 0.15)
    assert (g["above"], g["hits"], g["n"]) == (9, 8, 10)
    assert g["coverage"] == pytest.approx(0.9) and g["accuracy"] == pytest.approx(8 / 9)
    assert scoring.classify_gate(g) is False  # 88.9% < 95%
    g = scoring.gate_stats(recs, 0.7, 0.15)
    assert g["coverage"] == pytest.approx(0.6) and g["accuracy"] == 1.0
    assert scoring.classify_gate(g) is False  # 覆盖不到七成
    assert scoring.decisions_gate(scoring.gate_stats(recs, 0.55, 0.15, "agree")) is True
    assert scoring.classify_gate(scoring.gate_stats([], 0.55, 0.15)) is None
    # 头两名差不够、是非题落在中间、needs_thought、没答上来：都不算过门槛
    for extra in ({"margin": 0.1}, {"noul_mid": True}, {"needs_thought": True}, {"failed": True}, {"conf": None}):
        assert not scoring.passes({"conf": 0.9, "margin": 0.5, **extra}, 0.55, 0.15)
    b = scoring.bucket_stats(recs)
    assert [x["n"] for x in b] == [1, 3, 0, 6] and b[1]["accuracy"] == pytest.approx(2 / 3) and b[2]["accuracy"] is None
    sw = scoring.sweep(recs, (0.2, 0.55, 0.7), 0.15)
    assert [s["above"] for s in sw] == [10, 9, 6]
    assert scoring.recommend(recs, (0.2, 0.55, 0.7), 0.15, min_acc=0.95, min_cov=0.7) is None
    best = scoring.recommend(recs, (0.2, 0.55, 0.7), 0.15, min_acc=0.85, min_cov=0.7)
    assert best is not None and best["conf"] == 0.55
    acc = scoring.field_accuracy(recs)
    assert acc["kind"] == (8, 10) and acc["core"] == (8, 10) and acc["verb"] == (0, 0)


# ---------------------------------------------------------------- 抉择集


def test_decisions_agreement_and_mass() -> None:
    it = {"canon": "a4", "accept": ["a2"]}
    assert scoring.decision_agrees(it, "a4") and scoring.decision_agrees(it, "a2")
    assert not scoring.decision_agrees(it, "a1") and not scoring.decision_agrees(it, None)
    assert scoring.canon_mass(it, {"a1": 0.5, "a2": 0.2, "a4": 0.3}) == pytest.approx(0.5)
    recs = [{"agree": True, "char": "a"}, {"agree": False, "char": "a"}, {"agree": True, "char": "b"},
            {"agree": False, "char": "b", "failed": True}]
    assert scoring.agreement(recs) == (2, 3)
    assert scoring.group_agreement(recs, "char") == {"a": (1, 2), "b": (1, 1)}
    assert scoring.answer_decision({"type": "choice", "choice": "a1", "probabilities": {"a1": 0.4, "a2": 0.6}}) == "a2"
    assert scoring.answer_decision({"type": "noul", "noul": 0.65}, 0.7) is False
    assert scoring.answer_decision({"type": "noul", "noul": 0.75}, 0.7) is True
    assert scoring.answer_decision(None) is None
    # 过门槛样本一致不低于 70%、覆盖不少于一半
    assert scoring.decisions_gate({"n": 10, "accuracy": 0.7, "coverage": 0.5}) is True
    assert scoring.decisions_gate({"n": 10, "accuracy": 0.69, "coverage": 0.9}) is False
    assert scoring.decisions_gate({"n": 10, "accuracy": 0.9, "coverage": 0.49}) is False
    assert scoring.decisions_gate({"n": 10, "accuracy": None, "coverage": 0.0}) is False


# ---------------------------------------------------------------- 核对集


def check_rec(labels: tuple[bool, bool, bool], p: tuple[float, float, float]) -> dict:
    return {"labels": dict(zip(scoring.CHECK_LABELS, labels, strict=True)),
            "p": dict(zip(scoring.CHECK_LABELS, p, strict=True))}


def test_checks_detection_false_kills_and_sweep() -> None:
    recs = [
        check_rec((False, True, False), (0.1, 0.95, 0.1)),   # 读心，查出
        check_rec((True, False, False), (0.75, 0.1, 0.1)),   # 多写，0.7 查出、0.8 漏掉
        check_rec((False, False, True), (0.1, 0.1, 0.4)),    # 替玩家做主，漏掉
        check_rec((True, False, False), (0.2, 0.85, 0.1)),   # 标的是多写，却靠读心一题过线：也算检出
        check_rec((False, False, False), (0.1, 0.1, 0.1)),   # 干净
        check_rec((False, False, False), (0.72, 0.1, 0.1)),  # 干净，0.7 误杀
        check_rec((False, False, False), (0.1, 0.1, 0.65)),  # 干净，0.6 才误杀
        {**check_rec((True, True, True), (0.0, 0.0, 0.0)), "failed": True},  # 没答上来：不计
    ]
    st = scoring.checks_stats(recs, 0.7)
    assert (st["violating"], st["clean"], st["detected"], st["false_kills"]) == (4, 3, 3, 1)
    assert st["detection"] == pytest.approx(0.75) and st["false_kill"] == pytest.approx(1 / 3)
    assert scoring.checks_gate(st) is False
    ex = st["labels"]["extra"]
    assert (ex["tp"], ex["fp"], ex["fn"], ex["tn"]) == (1, 1, 1, 4)
    assert ex["precision"] == 0.5 and ex["recall"] == 0.5
    assert st["labels"]["mind"]["precision"] == 0.5 and st["labels"]["mind"]["recall"] == 1.0
    assert st["labels"]["puppet"]["precision"] is None and st["labels"]["puppet"]["recall"] == 0.0
    sw = scoring.checks_sweep(recs, (0.6, 0.7, 0.8))
    assert [s["detected"] for s in sw] == [3, 3, 2] and [s["false_kills"] for s in sw] == [2, 1, 0]
    assert scoring.checks_recommend(recs, (0.6, 0.7, 0.8)) is None
    # 检出不低于 80%、误杀不高于 10%
    assert scoring.checks_gate({"detection": 0.8, "false_kill": 0.1}) is True
    assert scoring.checks_gate({"detection": 0.79, "false_kill": 0.0}) is False
    assert scoring.checks_gate({"detection": 1.0, "false_kill": 0.11}) is False
    assert scoring.checks_gate({"detection": None, "false_kill": None}) is None
    good = [check_rec((True, False, False), (0.9, 0.0, 0.0))] * 5 + [check_rec((False, False, False), (0.75, 0.0, 0.0))] \
        + [check_rec((False, False, False), (0.0, 0.0, 0.0))] * 9
    best = scoring.checks_recommend(good, (0.7, 0.8, 0.95))
    assert best is not None and best["threshold"] == 0.8 and best["detection"] == 1.0 and best["false_kill"] == 0.0
    # 每道题各用各的门槛
    st = scoring.checks_stats(recs, {"extra": 0.8, "mind": 0.7, "puppet": 0.3})
    assert st["detected"] == 3 and st["false_kills"] == 1


# ---------------------------------------------------------------- 稳定性


def test_stability_and_same_rate() -> None:
    runs = [{"a": "x", "b": True, "c": 1}, {"a": "x", "b": True, "c": 2}, {"a": "x", "b": True, "c": 1}]
    st = scoring.stability(runs)
    assert (st["questions"], st["same"]) == (3, 2) and st["rate"] == pytest.approx(2 / 3)
    assert scoring.stability([])["rate"] is None
    # 某一次没答上来的题不计
    assert scoring.stability([{"a": 1, "b": 2}, {"a": 1}])["questions"] == 1
    assert scoring.same_rate({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 9}) == (1, 2)
    assert scoring.answer_p({"type": "noul", "noul": 0.3}) == 0.3
    assert scoring.answer_p({"type": "choice", "probabilities": {"a": 0.2, "b": 0.8}}) == 0.8


# ---------------------------------------------------------------- J1 的原始答案（README 2.7 的投影）


def raw(**kw: object) -> dict:
    base = {"kind": "act", "has_speech": False, "meaningful": True, "verb": None, "name": None, "targets": [],
            "act": "tell", "to": [], "asks": None, "ask_menu": None, "claims": [], "cls": None, "urgent": False}
    base.update(kw)
    return base


def test_raw_j1_answers_are_scored_per_question() -> None:
    gold = {"kind": "act", "verb": "give", "target": "tea", "target2": "boss"}
    # 物与人不分先后；纯动作不看 act、to（J1 的 act 题没有「没说话」一项）
    s = scoring.score_raw_item(item(gold), raw(verb="give", targets=["boss", "tea"]))
    assert s["kind"] and s["verb"] and s["target"] and s["has_speech"] and s["meaningful"]
    assert s["act"] is None and s["to"] is None and s["asks"] is None and s["cls"] is None
    # 两题都答了那个人：对象错；是非题说有话：has_speech 错
    s = scoring.score_raw_item(item(gold), raw(verb="give", targets=["boss", "boss"], has_speech=True))
    assert s["target"] is False and s["has_speech"] is False and s["verb"] is True
    # 不带对象的动作，随手选了一个人：对象错
    s = scoring.score_raw_item(item({"kind": "act", "verb": "look"}), raw(verb="look", targets=["guest"]))
    assert s["verb"] is True and s["target"] is False
    assert scoring.score_raw_item(item({"kind": "act", "verb": "look"}), raw(verb="look"))["target"] is True
    # 情境动作看名字；本事不算对象
    g = {"kind": "act", "verb": "setpiece", "name": "laugh"}
    assert scoring.score_raw_item(item(g), raw(verb="setpiece", name="laugh"))["verb"] is True
    assert scoring.score_raw_item(item(g), raw(verb="setpiece", name="bow"))["verb"] is False
    g = {"kind": "act", "verb": "study", "target": "book", "art": "sword"}
    assert scoring.score_raw_item(item(g), raw(verb="study", targets=["book", "sword"]))["target"] is True
    # 说话的条目
    g = {"kind": "say", "act": "request", "to": ["boss"], "asks": {"type": "release", "target": "girl"},
         "claims": ["F1"], "cls": "costly", "urgent": True}
    good = raw(kind="say", has_speech=True, act="request", to=["boss"], asks={"type": "release", "target": "girl"},
               claims=["F1"], cls="costly", urgent=True)
    s = scoring.score_raw_item(item(g), good)
    assert all(v is True for k, v in s.items() if k != "target") and s["target"] is None
    s = scoring.score_raw_item(item(g), dict(good, verb="go", asks=None, cls=None))
    assert s["verb"] is False and s["asks"] is False and s["cls"] is None  # 没选中请求：cls、urgent 不计
    # impossible：verb 应答「都不是」，kind 不计；unclear 只看「读得出意思」
    s = scoring.score_raw_item(item({"kind": "impossible"}), raw(verb="take"))
    assert s["verb"] is False and s["kind"] is None and s["meaningful"] is True
    s = scoring.score_raw_item(item({"kind": "unclear"}), raw(meaningful=False))
    assert s["meaningful"] is True and s["kind"] is None and s["verb"] is None
    recs = [{"raw": scoring.score_raw_item(item(gold), raw(verb="give", targets=["tea", "boss"]))},
            {"raw": scoring.score_raw_item(item(gold), raw(verb="show", targets=["tea"]))}, {"raw": None}]
    acc = scoring.raw_accuracy(recs)
    assert acc["verb"] == (1, 2) and acc["target"] == (1, 2) and acc["act"] == (0, 0)
