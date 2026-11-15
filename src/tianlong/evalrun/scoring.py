"""计分（纯函数，不碰模型与世界）：归类集、抉择集、核对集、稳定性，以及 7.12 的各道闸。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from tianlong.evalrun.assertions import action_matches

# ---------------------------------------------------------------- 归类集

GOLD_DEFAULTS: dict[str, Any] = {
    "verb": None, "target": None, "target2": None, "act": None, "to": [], "asks": None, "claims": [],
    "cls": None, "urgent": False, "art": None, "name": None,
}
FIELDS = ("kind", "verb", "target", "act", "to", "asks", "claims", "cls", "urgent")
CORE = ("kind", "verb", "target", "act", "to", "asks")  # 决定「做了什么、对谁说了什么、求了什么」的六项
ASK_SLOTS = ("target", "item", "art", "place")
BUCKETS = ((0.0, 0.55), (0.55, 0.70), (0.70, 0.85), (0.85, 1.0001))
SPOKEN = ("act", "say", "both")


def gold_of(item: Mapping[str, Any]) -> dict[str, Any]:
    g = dict(GOLD_DEFAULTS)
    g.update(item["gold"])
    return g


def gold_variants(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """标注本身，加上 alt 里每一种（alt 只覆盖它写出的字段）。"""
    g = gold_of(item)
    out = [g]
    for alt in item.get("alt") or []:
        v = dict(g)
        v.update(alt)
        out.append(v)
    return out


def to_equal(pred: Sequence[str] | None, gold: Sequence[str] | None) -> bool:
    p = [x for x in (pred or []) if x != "*"]
    g = [x for x in (gold or []) if x != "*"]
    if not g:
        return not p
    if not p:
        return False
    return p[0] == g[0] if len(p) == 1 else set(p) == set(g)


def ask_on_menu(gold: Mapping[str, Any] | None, menu: Iterable[Mapping[str, Any]] | None) -> bool:
    """标注的请求在候选里有没有对得上的一条（menu 为 None 表示不知道，当作有）。"""
    if gold is None or gold.get("type") == "other":
        return False
    if menu is None:
        return True
    return any(_ask_same(m, gold) for m in menu)


def _ask_same(pred: Mapping[str, Any], gold: Mapping[str, Any]) -> bool:
    if pred.get("type") != gold.get("type"):
        return False
    return all(pred.get(k) == gold[k] for k in ASK_SLOTS if gold.get(k) is not None)


def asks_equal(pred: Mapping[str, Any] | None, gold: Mapping[str, Any] | None,
               menu: Iterable[Mapping[str, Any]] | None = None) -> bool:
    """README 2.4：对得上的那条算对；``other``、候选里本来没有的事，应当一条也不选。"""
    if not ask_on_menu(gold, menu):
        return pred is None
    assert gold is not None
    return pred is not None and _ask_same(pred, gold)


def score_fields(g: Mapping[str, Any], pred: Mapping[str, Any], skip: Iterable[str] = ()) -> dict[str, bool | None]:
    """一种标注下各字段对不对；None 表示这一项不计分。

    pred：``{kind, verb, action: {verb, params} | None, loc, act, to, asks, ask_menu, claims, cls, urgent}``，
    其中 kind 是整条解析链的结论（对不上合法动作的记 impossible）。
    """
    skip = set(skip)
    out: dict[str, bool | None] = dict.fromkeys(FIELDS)
    out["kind"] = pred.get("kind") == g["kind"]
    if g["kind"] in SPOKEN:
        out["verb"] = pred.get("verb") == g["verb"]
        if g["verb"] is not None:
            act = pred.get("action") or {}
            out["target"] = action_matches(act.get("verb"), act.get("params") or {}, g, str(pred.get("loc") or ""))
        out["act"] = pred.get("act") == g["act"]
        if g["act"] is not None:
            out["to"] = to_equal(pred.get("to"), g["to"])
            out["asks"] = asks_equal(pred.get("asks"), g["asks"], pred.get("ask_menu"))
            out["claims"] = set(pred.get("claims") or []) == set(g["claims"] or [])
            if g["asks"] is not None:
                if g["asks"].get("type") != "other" and g["cls"] is not None:
                    out["cls"] = pred.get("cls") == g["cls"]
                out["urgent"] = bool(pred.get("urgent")) == bool(g["urgent"])
    for k in skip:
        if k in out:
            out[k] = None
    return out


def score_classify(item: Mapping[str, Any], pred: Mapping[str, Any]) -> dict[str, bool | None]:
    """取对得最多的那一种标注（gold 或某个 alt）来计分。"""
    best: dict[str, bool | None] | None = None
    best_n = -1
    for g in gold_variants(item):
        s = score_fields(g, pred, item.get("skip") or ())
        wrong = sum(1 for v in s.values() if v is False)
        n = -wrong
        if best is None or n > best_n:
            best, best_n = s, n
    assert best is not None
    return best


RAW = ("kind", "has_speech", "meaningful", "verb", "target", "act", "to", "asks", "claims", "cls", "urgent")


def score_raw(g: Mapping[str, Any], raw: Mapping[str, Any], skip: Iterable[str] = ()) -> dict[str, bool | None]:
    """README 2.7：J1 各题的答案直接对标注的投影（不经 bind）。None 表示这一题不计分。

    raw：``{kind, has_speech, meaningful, verb, name, targets, act, to, asks, ask_menu, claims, cls, urgent}``，
    其中各项已经从选项编号换回实体 id；``has_speech``、``meaningful``、``urgent`` 是过没过 noul_yes 的布尔值；
    ``targets`` 是 target、target2 两题选中的对象（不含「没有」）。
    """
    out: dict[str, bool | None] = dict.fromkeys(RAW)
    kind = g["kind"]
    out["meaningful"] = bool(raw.get("meaningful")) == (kind != "unclear")
    if kind == "impossible":
        out["verb"] = raw.get("verb") is None  # 对不上任何合法动作：verb 应答「都不是」
    if kind in ("act", "say", "both", "meta"):
        out["kind"] = raw.get("kind") == kind
    if kind in SPOKEN:
        out["has_speech"] = bool(raw.get("has_speech")) == (g["act"] is not None)
        out["verb"] = raw.get("verb") == g["verb"] and (g["verb"] != "setpiece" or raw.get("name") == g.get("name"))
        if g["verb"] is not None and g["verb"] != "setpiece":
            want = {x for x in (g.get("target"), g.get("target2")) if x}
            got = {x for x in raw.get("targets") or [] if x and x != g.get("art")}
            out["target"] = got == want
        if g["act"] is not None:
            out["act"] = raw.get("act") == g["act"]
            out["to"] = to_equal(raw.get("to"), g["to"])
            out["asks"] = asks_equal(raw.get("asks"), g["asks"], raw.get("ask_menu"))
            out["claims"] = set(raw.get("claims") or []) == set(g["claims"] or [])
            if g["asks"] is not None and raw.get("asks") is not None:
                if g["asks"].get("type") != "other" and g["cls"] is not None:
                    out["cls"] = raw.get("cls") == g["cls"]
                out["urgent"] = bool(raw.get("urgent")) == bool(g["urgent"])
    for k in skip:
        if k in out:
            out[k] = None
    return out


def score_raw_item(item: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, bool | None]:
    """取答错最少的那一种标注（gold 或某个 alt）。"""
    scored = [score_raw(g, raw, item.get("skip") or ()) for g in gold_variants(item)]
    return min(scored, key=lambda s: sum(1 for v in s.values() if v is False))


def raw_accuracy(records: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for k in RAW:
        vals = [r["raw"][k] for r in records if r.get("raw") and r["raw"].get(k) is not None]
        out[k] = (sum(1 for v in vals if v), len(vals))
    return out


def all_right(fields: Mapping[str, bool | None], keys: Iterable[str] = FIELDS) -> bool:
    return all(fields.get(k) is not False for k in keys)


def field_accuracy(records: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    """各字段 (对的条数, 计分的条数)，外加 core（六项全对）与 all（全部字段全对）。"""
    out: dict[str, tuple[int, int]] = {}
    for k in FIELDS:
        vals = [r["fields"][k] for r in records if r["fields"].get(k) is not None]
        out[k] = (sum(1 for v in vals if v), len(vals))
    out["core"] = (sum(1 for r in records if all_right(r["fields"], CORE)), len(records))
    out["all"] = (sum(1 for r in records if all_right(r["fields"])), len(records))
    return out


def passes(r: Mapping[str, Any], conf: float, margin: float) -> bool:
    """这一条的 Jev 答案过不过门槛：该看的 choice 题里最低的 confidence 与最小的头两名差都够，是非题不在中间。"""
    if r.get("failed") or r.get("conf") is None:
        return False
    return float(r["conf"]) >= conf and float(r.get("margin", 1.0)) >= margin and not r.get("noul_mid", False) \
        and not r.get("needs_thought", False)


def gate_stats(records: Sequence[Mapping[str, Any]], conf: float, margin: float, key: str = "core"
               ) -> dict[str, Any]:
    """门槛之上的覆盖率与准确率。key：records 里表示「这条对了」的布尔字段。"""
    n = len(records)
    above = [r for r in records if passes(r, conf, margin)]
    hit = sum(1 for r in above if r.get(key))
    return {
        "conf": conf, "margin": margin, "n": n, "above": len(above), "hits": hit,
        "coverage": len(above) / n if n else None,
        "accuracy": hit / len(above) if above else None,
    }


def sweep(records: Sequence[Mapping[str, Any]], confs: Sequence[float], margin: float, key: str = "core"
          ) -> list[dict[str, Any]]:
    return [gate_stats(records, c, margin, key) for c in confs]


def bucket_stats(records: Sequence[Mapping[str, Any]], key: str = "core", conf_key: str = "conf"
                 ) -> list[dict[str, Any]]:
    """按置信度分桶的准确率。"""
    out = []
    for lo, hi in BUCKETS:
        rs = [r for r in records if r.get(conf_key) is not None and lo <= float(r[conf_key]) < hi]
        hit = sum(1 for r in rs if r.get(key))
        out.append({"lo": lo, "hi": min(hi, 1.0), "n": len(rs), "hits": hit, "accuracy": hit / len(rs) if rs else None})
    return out


def recommend(records: Sequence[Mapping[str, Any]], confs: Sequence[float], margin: float, *, min_acc: float,
              min_cov: float, key: str = "core") -> dict[str, Any] | None:
    """过闸（准确率与覆盖率都达标）的门槛里覆盖率最高的一个；没有就是 None。"""
    ok = [s for s in sweep(records, confs, margin, key)
          if s["accuracy"] is not None and s["accuracy"] >= min_acc and (s["coverage"] or 0.0) >= min_cov]
    return max(ok, key=lambda s: (s["coverage"], -s["conf"])) if ok else None


def classify_gate(stats: Mapping[str, Any]) -> bool | None:
    """7.12：过门槛的样本上一致不低于 95%，且过门槛的样本不少于七成。"""
    if not stats.get("n") or stats.get("accuracy") is None:
        return None if not stats.get("n") else False
    return stats["accuracy"] >= 0.95 and stats["coverage"] >= 0.70


# ---------------------------------------------------------------- 抉择集


def decision_agrees(item: Mapping[str, Any], pick: str | None) -> bool:
    return pick is not None and pick in {item["canon"], *(item.get("accept") or [])}


def canon_mass(item: Mapping[str, Any], probabilities: Mapping[str, float]) -> float:
    """模型分给原著做法（含 accept）的概率之和：游戏里是按概率取样的，这就是取样的期望一致率。"""
    keys = {item["canon"], *(item.get("accept") or [])}
    return float(sum(float(probabilities.get(k, 0.0)) for k in keys))


def agreement(records: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    rs = [r for r in records if not r.get("failed")]
    return sum(1 for r in rs if r.get("agree")), len(rs)


def group_agreement(records: Sequence[Mapping[str, Any]], key: str) -> dict[str, tuple[int, int]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for r in records:
        out.setdefault(str(r.get(key)), []).append(r)
    return {k: agreement(v) for k, v in out.items()}


def decisions_gate(stats: Mapping[str, Any]) -> bool | None:
    """7.12：过门槛样本与原著做法一致不低于 70%，且过门槛的样本不少于一半。"""
    if not stats.get("n") or stats.get("accuracy") is None:
        return None if not stats.get("n") else False
    return stats["accuracy"] >= 0.70 and stats["coverage"] >= 0.50


# ---------------------------------------------------------------- 核对集

CHECK_LABELS = ("extra", "mind", "puppet")


def flags(p: Mapping[str, float], th: float | Mapping[str, float]) -> dict[str, bool]:
    def t(k: str) -> float:
        return float(th[k]) if isinstance(th, Mapping) else float(th)

    return {k: float(p.get(k, 0.0)) > t(k) for k in CHECK_LABELS}


def checks_stats(records: Sequence[Mapping[str, Any]], th: float | Mapping[str, float]) -> dict[str, Any]:
    """检出率（违规句里至少一项过门槛）、误杀率（干净句里任一项过门槛）、各标签的精确率与召回率。"""
    rs = [r for r in records if not r.get("failed")]
    bad = [r for r in rs if any(r["labels"].get(k) for k in CHECK_LABELS)]
    clean = [r for r in rs if not any(r["labels"].get(k) for k in CHECK_LABELS)]
    det = sum(1 for r in bad if any(flags(r["p"], th).values()))
    kill = sum(1 for r in clean if any(flags(r["p"], th).values()))
    per: dict[str, dict[str, Any]] = {}
    for k in CHECK_LABELS:
        tp = sum(1 for r in rs if r["labels"].get(k) and flags(r["p"], th)[k])
        fp = sum(1 for r in rs if not r["labels"].get(k) and flags(r["p"], th)[k])
        fn = sum(1 for r in rs if r["labels"].get(k) and not flags(r["p"], th)[k])
        tn = len(rs) - tp - fp - fn
        per[k] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "accuracy": (tp + tn) / len(rs) if rs else None,
        }
    return {
        "threshold": th if not isinstance(th, Mapping) else dict(th),
        "n": len(rs), "violating": len(bad), "clean": len(clean), "detected": det, "false_kills": kill,
        "detection": det / len(bad) if bad else None,
        "false_kill": kill / len(clean) if clean else None,
        "labels": per,
    }


def checks_sweep(records: Sequence[Mapping[str, Any]], ths: Sequence[float]) -> list[dict[str, Any]]:
    return [checks_stats(records, t) for t in ths]


def checks_gate(stats: Mapping[str, Any]) -> bool | None:
    """7.12：违规句检出不低于 80%，误杀不高于 10%。"""
    if stats.get("detection") is None or stats.get("false_kill") is None:
        return None
    return stats["detection"] >= 0.80 and stats["false_kill"] <= 0.10


def checks_recommend(records: Sequence[Mapping[str, Any]], ths: Sequence[float]) -> dict[str, Any] | None:
    """过闸的门槛里检出率最高的（同分取误杀少的、再取门槛高的）；没有就是 None。"""
    ok = [s for s in checks_sweep(records, ths) if checks_gate(s)]
    return max(ok, key=lambda s: (s["detection"], -s["false_kill"], s["threshold"])) if ok else None


# ---------------------------------------------------------------- 稳定性与中英对比


def answer_decision(answer: Mapping[str, Any] | None, th: float = 0.5) -> Any:
    """一道题的「判定」：choice 取概率最高的一项，noul 看过不过门槛，score 取最可能的一级。"""
    if answer is None:
        return None
    t = answer.get("type")
    if t == "choice":
        probs = answer.get("probabilities") or {}
        if probs:
            return max(probs, key=lambda k: float(probs[k]))
        return answer.get("choice")
    if t == "noul":
        return float(answer.get("noul", 0.0)) > th
    if t == "score":
        probs = answer.get("probabilities") or {}
        return max(probs, key=lambda k: float(probs[k])) if probs else answer.get("score")
    return None


def answer_p(answer: Mapping[str, Any] | None) -> float | None:
    """用来看抖动的那个数：choice 的头名概率，noul 的 p。"""
    if answer is None:
        return None
    if answer.get("type") == "noul":
        return float(answer.get("noul", 0.0))
    probs = answer.get("probabilities") or {}
    return max((float(v) for v in probs.values()), default=None)


def stability(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """同一请求重复几次：runs[i] 是第 i 次的 {题目键: 判定}。返回判定全同的题数与请求数。"""
    if not runs:
        return {"questions": 0, "same": 0, "rate": None}
    keys = [k for k in runs[0] if all(k in r for r in runs)]
    same = sum(1 for k in keys if len({repr(r[k]) for r in runs}) == 1)
    return {"questions": len(keys), "same": same, "rate": same / len(keys) if keys else None}


def same_rate(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[int, int]:
    """两次跑（例如中文问法与英文问法）里判定相同的条数。"""
    keys = [k for k in a if k in b]
    return sum(1 for k in keys if a[k] == b[k]), len(keys)
