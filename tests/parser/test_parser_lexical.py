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
    # 是非题落在中间，kind 又不是很有把握：没把握，转 C1
    weak = {**base, "kind": {"act": 0.8, "say": 0.15, "both": 0.03, "meta": 0.02}}
    a = _ans(**weak, has_speech=0.5, meaningful=0.95)
    it = intake.intent_from_jev(a, menu, th, [])
    assert intake.jev_unsure(a, it, th)
    # kind 很有把握（不低于 0.9）时以它为准：has_speech 落在中间不算没把握
    a = _ans(**base, has_speech=0.5, meaningful=0.95)
    it = intake.intent_from_jev(a, menu, th, [])
    assert it.kind == "act" and not intake.jev_unsure(a, it, th)
    # 读不读得出意思落在中间：照旧算没把握
    a = _ans(**base, has_speech=0.1, meaningful=0.5)
    assert intake.jev_unsure(a, intake.intent_from_jev(a, menu, th, []), th)
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


# ---------------------------------------------------------------- 多字词压过单字（「打招呼」不是打人）


def test_longer_phrases_claim_their_characters() -> None:
    def verbs(text: str) -> list[str]:
        return [v for _, v, _ in lx.verbs_in(text)]

    # 含着「打」而不是打人的词：一个动作也不算
    for text in ("打个招呼", "打声招呼", "打了个招呼", "打听打听", "打点行装", "打算明天再说", "打发时间", "打扰了",
                 "打开包袱", "您老打哪儿来"):
        assert verbs(text) == [], text
    # 含着「打」的别的动作：是那个动作，不是打人
    assert verbs("上下打量他") == ["examine"] and verbs("打坐一个时辰") == ["rest"] and verbs("打个盹") == ["rest"]
    # 真要打人的照旧
    for text in ("打他", "打他一个耳光", "一拳打在他脸上", "把他打倒", "动手打人"):
        assert set(verbs(text)) == {"attack"}, text
    # 别的单字也一样：多字词先占位置
    assert verbs("佩服佩服") == [] and verbs("吃了一惊") == [] and verbs("推辞不受") == [] and verbs("不用客气") == []
    assert verbs("偷偷看他一眼") == ["examine"] and verbs("偷偷溜出去")[0] == "go"
    assert verbs("偷他的钥匙") == ["steal"] and verbs("偷偷拿走钥匙") == ["steal"]
    # 一样长的词靠前的先占；占住的字不再另认
    assert [(v, w) for _, v, w in lx.verbs_in("偷偷看")] == [("examine", "看")]
    # 表里的每个「不算动作」的词都确实压得住它里面的单字
    for word, _act in lx._PHRASE_WORDS:
        if not any(ch in word for ch in "()?[]"):
            assert verbs(word) == [], word


def test_narrated_speech_is_speech() -> None:
    assert lx.narrated_act("我向他打个招呼") == "greet" and lx.narrated_act("跟他打听打听后山的路") == "ask"
    assert lx.narrated_act("向东家问声好") == "greet" and lx.narrated_act("把他打倒") is None
    # 直接冲着人说的原话不算叙述口吻：说的是什么由话本身定
    assert lx.narrated_act("公子，替我向你师父问好") is None and lx.narrated_act("我想向您打听个人") is None
    assert [v for _, v, _ in lx.verbs_in("我想向您打听个人")] == []  # 可「打听」照样不是打人
    assert lx.split_speech("我向他打个招呼") == ("", "我向他打个招呼")
    assert lx.split_speech("去跟那个舞剑的青年打个招呼") == ("", "去跟那个舞剑的青年打个招呼")
    # 前面的分句里另有身体动作：又做又说
    assert lx.split_speech("跟上他，向他打个招呼") == ("跟上他", "跟上他，向他打个招呼")
    # 写出了原话的照旧按引号拆
    assert lx.split_speech("向他打个招呼：「久仰」") == ("向他打个招呼", "久仰")
    assert lx.SPEECH_OBJ_RE.search("向东家打个招呼").group(1) == "东家"
    assert lx.NARRATED_OBJ_RE.search("和东家打个招呼就走").group(1) == "东家"
    assert lx.NARRATED_OBJ_RE.search("我和东家是一路的") is None


async def test_greeting_someone_is_not_an_attack(scene: Scene) -> None:
    """「我向某人打个招呼」：规则一路读成对他的一句寒暄，不是动手（Mock 的 J1、C1 照规则猜测作答）。"""
    scene.llm.settings.route.classify = "rules_only"
    for raw in ("我向段公子打个招呼", "跟段公子打声招呼", "和段公子打个招呼"):
        r, b = await scene.bind(raw)
        assert r.intent.kind == "say" and r.intent.verb is None and r.intent.act == "greet", raw
        assert r.intent.to == ["duan_yu"], raw
        assert b.kind == "action" and b.action is not None and b.action.verb != "attack", raw
        assert b.speech is not None and b.speech.act == "greet" and b.speech.to == ["duan_yu"], raw
    before = len(scene.state.events_tail)
    b, evs = await scene.play("我向段公子打个招呼")
    assert evs and not any(e.verb == "attack" for e in scene.state.events_tail[before:])
    # 打听：一句询问
    r, b = await scene.bind("跟东家打听打听这里的规矩")
    assert r.intent.kind == "say" and r.intent.act == "ask" and r.intent.to == ["ma_wude"]
    assert b.kind == "action" and b.action.verb != "attack"
    # 打量：细看，不是打
    r, b = await scene.bind("上下打量段公子")
    assert r.intent.verb == "examine" and b.action is not None and b.action.verb == "examine"
    assert b.action.params.get("target") == "duan_yu"
    # 真要动手的还是动手
    r, b = await scene.bind("打龚光杰一个耳光")
    assert r.intent.kind == "act" and r.intent.verb == "attack" and r.intent.target == "gong_guangjie"
    # J1 → C1 的 Mock 路上也一样（提示来自同一份规则猜测）
    scene.llm.settings.route.classify = "jev_first"
    r, b = await scene.bind("我向段公子打个招呼")
    assert r.intent.kind == "say" and r.intent.act == "greet" and b.action is not None and b.action.verb != "attack"
