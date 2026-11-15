"""规则猜测用的小工具，以及 J1 答案的解读（D-014 的两道是非题）。"""

from __future__ import annotations

from typing import Any

from tianlong.config import JevThresholds
from tianlong.llm.jev import wrap_answer
from tianlong.parser import intake
from tianlong.parser import lexical as lx

from .conftest import Scene


def test_durations() -> None:
    assert lx.duration_ticks("我等一个时辰") == 8
    assert lx.duration_ticks("歇两个时辰") == 16
    assert lx.duration_ticks("等半个时辰") == 4
    assert lx.duration_ticks("等三刻") == 3
    assert lx.duration_ticks("睡一夜") == 64
    assert lx.duration_ticks("练十二个时辰") == 96
    assert lx.duration_ticks("等一会儿") == 1
    assert lx.duration_ticks("我四下看看") is None
    assert lx.cn_to_int("十五") == 15 and lx.cn_to_int("二十") == 20 and lx.cn_to_int("7") == 7


def test_split_speech() -> None:
    assert lx.split_speech("我把他打晕了") == ("我把他打晕了", "")
    assert lx.split_speech("我扶起他，问他：「伤在哪里？」") == ("我扶起他，问他", "伤在哪里？")
    assert lx.split_speech("帮主，让我跟段公子一道去吧") == ("", "帮主，让我跟段公子一道去吧")
    assert lx.split_speech("我告诉左掌门干光豪有私情")[1]
    assert lx.split_speech("我对段公子说：快走") == ("我对段公子说", "快走")
    assert lx.split_speech("拿起绳子") == ("拿起绳子", "")


def test_verbs_skip_approach_words() -> None:
    found = [v for _, v, _ in lx.verbs_in("我走过去扶起他")]
    assert found == ["help"]
    assert [v for _, v, _ in lx.verbs_in("我把他打晕了")] == ["attack"]


def test_name_matching(scene: Scene) -> None:
    s, d = scene.state, scene.defs
    assert lx.match_score(s, d, "duan_yu", "段公子，快走") > lx.match_score(s, d, "zuo_zimu", "段公子，快走")
    assert lx.match_score(s, d, "duan_yu", "段誉") >= 10  # 真名（玩家从书里知道的）也认
    assert lx.match_score(s, d, "zhong_ling", "钟姑娘在哪里") >= 6  # 姓加称呼
    assert lx.match_score(s, d, "sikong_xuan", "那个山羊胡老头") >= 2  # 叫法的一部分
    assert lx.match_score(s, d, "rope", "我拿起绳子") == 2  # 物件的中心字：弱匹配
    assert lx.match_score(s, d, "tinder", "我拿起绳子") == 0


def _ans(**kw: Any) -> dict[str, Any]:
    out = {}
    for k, v in kw.items():
        if isinstance(v, float):
            out[k] = wrap_answer({"type": "noul", "noul": v})
        else:
            probs = v
            top = max(probs, key=probs.get)
            out[k] = wrap_answer({"type": "choice", "choice": top, "confidence": probs[top], "probabilities": probs})
    return out


async def test_intent_from_jev_uses_the_two_nouls(scene: Scene) -> None:
    menu = intake.build_menu(scene.state, scene.defs, "我四下看看")
    th = JevThresholds()
    base = {"kind": {"act": 0.9, "say": 0.05, "both": 0.03, "meta": 0.02},
            "verb": {"look": 0.95, "none": 0.05}, "target": {"none": 0.97, "p1": 0.03}}
    a = _ans(**base, has_speech=0.1, meaningful=0.95)
    it = intake.intent_from_jev(a, menu, th, [])
    assert it.kind == "act" and it.verb == "look"
    assert not intake.jev_unsure(a, it, th)
    # 读不出意思：不管 kind 选了什么都是 unclear
    a = _ans(**base, has_speech=0.1, meaningful=0.2)
    assert intake.intent_from_jev(a, menu, th, []).kind == "unclear"
    # 动作之外还开了口：both
    a = _ans(**base, has_speech=0.9, meaningful=0.9, act={"tell": 0.9, "ask": 0.1}, to={"all": 0.9, "p1": 0.1})
    it = intake.intent_from_jev(a, menu, th, [])
    assert it.kind == "both" and it.act == "tell" and it.to == []
    # 是非题落在中间：没把握，转 C1
    a = _ans(**base, has_speech=0.5, meaningful=0.95)
    it = intake.intent_from_jev(a, menu, th, [])
    assert intake.jev_unsure(a, it, th)
    # kind 头两名太近：转 C1
    a = _ans(kind={"act": 0.5, "say": 0.45, "both": 0.03, "meta": 0.02}, verb=base["verb"], target=base["target"],
             has_speech=0.1, meaningful=0.95)
    it = intake.intent_from_jev(a, menu, th, [])
    assert intake.jev_unsure(a, it, th)


async def test_normalize_drops_things_outside_the_menu(scene: Scene) -> None:
    from tianlong.llm.schemas import Intent

    menu = intake.build_menu(scene.state, scene.defs, "我打龚光杰")
    p = next(o for o in menu.objects if o.ref == "gong_guangjie")
    raw = Intent(kind="act", verb="attack", target=p.key, target2="不存在的东西", act=None, to=["zz"], asks="k99",
                 cls="weird", urgent=False, claim_refs=["c999", "self_unlisted"], attempts=["模型自己编的"],
                 dropped="", note="")
    it = intake.normalize(raw, menu, scene.state, ["他倒下了"])
    assert it.verb == "attack" and it.target == "gong_guangjie" and it.target2 is None
    assert it.to == [] and it.asks is None and it.cls is None and it.claim_refs == []
    assert it.attempts == ["他倒下了"]  # 舞台说明以正则剥下的为准
