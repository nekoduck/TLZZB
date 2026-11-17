"""真 Jev 评测查出的解析侧毛病的回归测试（evals/results/findings.md）。全部离线：J1 的答案是手写的。"""

from __future__ import annotations

from typing import Any

from tianlong import parser
from tianlong.config import JevThresholds
from tianlong.kernel import speech
from tianlong.kernel.state import Request
from tianlong.llm.jev import wrap_answer
from tianlong.parser import binding, intake, relations, rules

from .conftest import Scene

TH = JevThresholds()


def _ans(**kw: Any) -> dict[str, Any]:
    out = {}
    for k, v in kw.items():
        if isinstance(v, float):
            out[k] = wrap_answer({"type": "noul", "noul": v})
        else:
            top = max(v, key=v.get)
            out[k] = wrap_answer({"type": "choice", "choice": top, "confidence": v[top], "probabilities": v})
    return out


def _result(scene: Scene, raw: str, answers: dict[str, Any], menu: Any) -> parser.ParseResult:
    intent = intake.intent_from_jev(answers, menu, TH, [])
    return parser.ParseResult(intent=intent, clean=raw, via="jev", menu=menu)


SAY = {"kind": {"say": 0.99, "act": 0.01}, "verb": {"none": 0.95, "go": 0.05}}
ACT = {"kind": {"act": 0.99, "both": 0.01}}


# ---------------------------------------------------------------- 1. 候选之外的请求也留着类别


async def test_other_request_keeps_the_models_class(scene: Scene) -> None:
    raw = "东家，把这趟的货款全给我，我自己做买卖去。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    boss = menu.person("ma_wude")
    assert boss is not None and menu.guess_to == "ma_wude" and menu.guess_strength == rules.CALLED
    q = intake.j1_questions(scene.state, scene.defs, menu)
    assert "cls" in q and "urgent" in q
    a = _ans(**SAY, has_speech=0.97, meaningful=0.95, act={"request": 0.99, "tell": 0.01},
             to={boss.key: 0.9, "all": 0.1}, cls={"costly": 0.8, "effort": 0.2}, urgent=0.1,
             **{f"ask_{c.key}": 0.05 for c in menu.asks})
    r = _result(scene, raw, a, menu)
    assert r.intent.act == "request" and r.intent.asks is None and r.intent.cls == "costly"
    b = parser.bind(scene.state, scene.defs, r)
    assert b.kind == "action" and b.speech is not None
    assert b.speech.to == ["ma_wude"] and b.speech.action_class == "costly"
    assert b.speech.ask_action is not None and b.speech.ask_action.get("other")
    # 模型说得更轻也没用：候选之外的事至少算「费事」（D-087）
    a["cls"] = _ans(cls={"trivial": 0.9, "effort": 0.1})["cls"]
    b = parser.bind(scene.state, scene.defs, _result(scene, raw, a, menu))
    assert b.speech is not None and b.speech.action_class == "effort"


# ---------------------------------------------------------------- 2. 「告诉我某事」带着那条事实


async def test_tell_about_carries_the_fact_so_bottom_lines_fire(scene: Scene) -> None:
    scene.move_player("zhong_hall")
    raw = "夫人，把您年轻时那段旧情讲给我听听。"
    assert relations.topic_fact(scene.state, scene.defs, "gan_baobao", raw) == "F_gan_old_affair"
    cands = parser.request_candidates(scene.state, scene.defs, "player", "gan_baobao", text=raw)
    tell = next(c for c in cands if c.type == "tell_about")
    assert tell.action == {"verb": "say", "params": {"act": "tell", "fact": "F_gan_old_affair"}, "desc": tell.desc}
    req = Request(id="R_t", frm="player", to="gan_baobao", action=tell.action, action_class="trivial")
    assert speech.request_blocked(scene.state, scene.defs, req)
    # 整条链（规则猜测也读得出这是打听）：裁决 never
    b, _evs = await scene.play(raw)
    assert b.speech is not None and b.speech.act == "request"
    mine = [r for r in scene.state.requests.values() if r.frm == "player" and r.to == "gan_baobao"]
    assert mine and mine[-1].verdict == "never"
    # 对不上她知道的任何一件事：仍是泛泛的一问，不带 fact
    cands = parser.request_candidates(scene.state, scene.defs, "player", "gan_baobao", text="夫人，跟我说说话吧。")
    tell = next(c for c in cands if c.type == "tell_about")
    assert "fact" not in tell.action["params"]


# ---------------------------------------------------------------- 3. 近义的关系词也对得上事实


async def test_claims_match_by_entities_and_relation_class(scene: Scene) -> None:
    assert "lovers" in relations.classes() and {"lovers"} <= relations.classes_in("两人私下相好")
    raw = "左掌门，干光豪和葛光佩私下相好，打算趁乱私奔下山。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    g = rules.guess(scene.state, scene.defs, raw, menu)
    refs = {menu.claim(k).ref for k in g.claims}
    assert {"F_lovers", "F_elope_plan"} <= refs
    f = scene.state.facts["F_lovers"].text
    # 只点到一个人，或者说的是别的事：对不上
    assert relations.fact_score(scene.state, scene.defs, f, "干光豪跟人私下相好") == 0
    assert relations.fact_score(scene.state, scene.defs, f, "干光豪和葛光佩的剑法都不坏") == 0
    assert relations.fact_score(scene.state, scene.defs, f, "今天天气不错") == 0


# ---------------------------------------------------------------- 4/5. 对象：物一题、人一题，按动词落地


async def test_target_questions_are_split_by_kind(scene: Scene) -> None:
    menu = intake.build_menu(scene.state, scene.defs, "把样茶双手捧给左掌门。")
    q = intake.j1_questions(scene.state, scene.defs, menu)
    kinds = {o.key: o.kind for o in menu.objects}
    assert all(kinds[k] != "person" for k in q["target"]["criteria"] if k != "none")
    assert all(kinds[k] == "person" for k in q["target2"]["criteria"] if k != "none")
    assert "none" in q["target"]["criteria"] and "none" in q["target2"]["criteria"]
    tea, zuo = menu.obj("tea_sample"), menu.obj("zuo_zimu")
    assert tea is not None and zuo is not None
    a = _ans(**ACT, verb={"give": 1.0}, target={tea.key: 0.98, "none": 0.02}, target2={zuo.key: 0.97, "none": 0.03},
             has_speech=0.3, meaningful=0.95)
    r = _result(scene, "把样茶双手捧给左掌门。", a, menu)
    assert (r.intent.kind, r.intent.verb, r.intent.target, r.intent.target2) == ("act", "give", "tea_sample", "zuo_zimu")
    b = parser.bind(scene.state, scene.defs, r)
    assert b.action is not None and b.action.params == {"item": "tea_sample", "target": "zuo_zimu"}
    # Mock 的提示也照这个分法给
    g = rules.guess(scene.state, scene.defs, "把样茶双手捧给左掌门。", menu)
    h = intake.hints_for(menu, g)
    assert h["target"] == tea.key and h["target2"] == zuo.key


async def test_verbs_without_objects_drop_spurious_targets(scene: Scene) -> None:
    raw = "我四下看看厅里都坐着些什么人。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    someone = next(o for o in menu.objects if o.kind == "person")
    thing = next(o for o in menu.objects if o.kind == "feature")
    a = _ans(**ACT, verb={"look": 1.0}, target={thing.key: 0.6, "none": 0.4}, target2={someone.key: 0.57, "none": 0.43},
             has_speech=0.2, meaningful=0.97)
    r = _result(scene, raw, a, menu)
    assert (r.intent.verb, r.intent.target, r.intent.target2) == ("look", None, None)
    assert parser.bind(scene.state, scene.defs, r).action.verb == "look"
    assert intake.gate_keys(a, r.intent) == ["kind", "verb"]  # 不带对象的动词不看对象题的把握
    # 第二个对象要过 choice_confidence 才取：拿东西不问冲着谁；用东西时人答得含糊就不取
    item = menu.obj("rations")
    assert item is not None
    a = _ans(**ACT, verb={"use": 1.0}, target={item.key: 0.95, "none": 0.05}, target2={someone.key: 0.4, "none": 0.35},
             has_speech=0.1, meaningful=0.97)
    it = intake.intent_from_jev(a, menu, TH, [])
    assert (it.target, it.target2) == ("rations", None)
    a["target2"] = _ans(target2={someone.key: 0.9, "none": 0.1})["target2"]
    it = intake.intent_from_jev(a, menu, TH, [])
    assert (it.target, it.target2) == ("rations", someone.ref)
    # 只取一个对象的动词（细看）：物与人里取把握大的那个
    a = _ans(**ACT, verb={"examine": 1.0}, target={thing.key: 0.5, "none": 0.5}, target2={someone.key: 0.9, "none": 0.1},
             has_speech=0.1, meaningful=0.97)
    it = intake.intent_from_jev(a, menu, TH, [])
    assert (it.target, it.target2) == (someone.ref, None)
    assert intake.gate_keys(a, it) == ["kind", "verb", "target2"]


# ---------------------------------------------------------------- 6. kind 很有把握时以它为准


async def test_confident_kind_beats_a_middling_has_speech(scene: Scene) -> None:
    raw = "把麻绳放在脚边地上。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    rope = menu.obj("rope")
    assert rope is not None
    base = dict(verb={"drop": 1.0}, target={rope.key: 0.9, "none": 0.1}, target2={"none": 1.0}, meaningful=0.97,
                act={"request": 0.8, "tell": 0.2}, to={"all": 0.8})
    a = _ans(**ACT, **base, has_speech=0.66)
    it = intake.intent_from_jev(a, menu, TH, [])
    assert it.kind == "act" and it.act is None and it.asks is None and not intake.jev_unsure(a, it, TH)
    # 是非题答得明确（不低于 0.75）才另算一句话
    a = _ans(**ACT, **base, has_speech=0.8)
    assert intake.intent_from_jev(a, menu, TH, []).kind == "both"
    # kind 没那么有把握：照旧看是非题过没过 noul_yes
    a = _ans(kind={"act": 0.8, "both": 0.2}, **base, has_speech=0.66)
    assert intake.intent_from_jev(a, menu, TH, []).kind == "both"
    # kind 很有把握地说在说话：是非题不明确说「没有」，就算有话
    a = _ans(**SAY, has_speech=0.5, meaningful=0.95, act={"tell": 0.9, "ask": 0.1}, to={"all": 0.9})
    assert intake.intent_from_jev(a, menu, TH, []).kind == "say"


async def test_action_without_a_listed_verb_is_impossible_not_unclear(scene: Scene) -> None:
    raw = "掬一捧湖水喝。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    a = _ans(**ACT, verb={"none": 0.97, "use": 0.03}, target={"none": 1.0}, target2={"none": 1.0},
             has_speech=0.1, meaningful=0.95)
    r = _result(scene, raw, a, menu)
    assert r.intent.kind == "impossible"
    b = parser.bind(scene.state, scene.defs, r)
    assert b.kind == "impossible" and b.reply


# ---------------------------------------------------------------- 说话的对象与所求之事


async def test_asks_only_go_with_request_acts(scene: Scene) -> None:
    raw = "左掌门，这比剑还得比多久？"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    zuo = menu.person("zuo_zimu")
    tell = next(c for c in menu.asks if c.type == "tell_about")
    assert zuo is not None
    base = dict(has_speech=0.97, meaningful=0.95, to={zuo.key: 0.99}, cls={"trivial": 0.9}, urgent=0.1,
                **{f"ask_{c.key}": (0.9 if c.key == tell.key else 0.05) for c in menu.asks})
    it = intake.intent_from_jev(_ans(**SAY, act={"ask": 0.95, "request": 0.05}, **base), menu, TH, [])
    assert it.act == "ask" and it.asks is None and it.cls is None  # 询问由内核另挂待答的问题
    it = intake.intent_from_jev(_ans(**SAY, act={"request": 0.95, "ask": 0.05}, **base), menu, TH, [])
    assert it.act == "request" and it.asks == tell.key and it.cls == "trivial"


async def test_addressee_combines_the_model_and_the_vocative(scene: Scene) -> None:
    raw = "东家，咱们几时下山？"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    boss, other = menu.person("ma_wude"), menu.person("zuo_zimu")
    assert boss is not None and other is not None
    assert (menu.guess_to, menu.guess_strength) == ("ma_wude", rules.CALLED)
    base = dict(has_speech=0.97, meaningful=0.95, act={"ask": 0.95, "tell": 0.05})
    # J1 没把握、或只是有点把握地答了别人：听称呼的
    for probs in ({other.key: 0.43, boss.key: 0.3, "all": 0.27}, {other.key: 0.7, boss.key: 0.3}):
        assert intake.intent_from_jev(_ans(**SAY, **base, to=probs), menu, TH, []).to == ["ma_wude"]
    # J1 很有把握：听 J1 的
    it = intake.intent_from_jev(_ans(**SAY, **base, to={other.key: 0.95, boss.key: 0.05}), menu, TH, [])
    assert it.to == ["zuo_zimu"]
    # J1 的状态里写明谁听谁的
    text = intake.scene_text(scene.state, scene.defs, menu, words_text=raw, recent=None)
    assert "你听他的吩咐" in text and "【眼前刚发生的事】" in text and "【等你答复的事】" in text


async def test_to_all_with_two_hearers_means_both(scene: Scene) -> None:
    scene.fx([{"move": ["player", "back_trail"]}, {"move": ["gan_guanghao", "back_trail"]},
              {"move": ["ge_guangpei", "back_trail"]}])
    raw = "这件事我绝不说出去。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    assert len(menu.people) == 2
    a = _ans(**SAY, has_speech=0.97, meaningful=0.95, act={"promise": 0.95, "tell": 0.05}, to={"all": 0.9, "p1": 0.1})
    assert set(intake.intent_from_jev(a, menu, TH, []).to) == {"gan_guanghao", "ge_guangpei"}


async def test_request_follows_the_addressee_not_the_menu_owner(scene: Scene) -> None:
    scene.fx([{"move": ["player", "back_trail"]}, {"move": ["gan_guanghao", "back_trail"]},
              {"move": ["ge_guangpei", "back_trail"]}])
    raw = "你跟我回去认个错吧。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    assert menu.addressee is None  # 认不出冲谁说：两个人的候选都列
    hers = next(c for c in menu.asks if c.type == "come_along" and c.to == "ge_guangpei")
    him = menu.person("gan_guanghao")
    assert him is not None
    a = _ans(**SAY, has_speech=0.97, meaningful=0.95, act={"request": 0.99, "tell": 0.01}, to={him.key: 0.95, "all": 0.05},
             cls={"costly": 0.8, "effort": 0.2}, urgent=0.1,
             **{f"ask_{c.key}": (0.9 if c.key == hers.key else 0.05) for c in menu.asks})
    b = parser.bind(scene.state, scene.defs, _result(scene, raw, a, menu))
    assert b.speech is not None and b.speech.to == ["gan_guanghao"]
    assert b.speech.ask_action is not None and b.speech.ask_action["verb"] == "follow"


async def test_request_to_everyone_lands_on_whoever_is_in_charge(scene: Scene) -> None:
    scene.fx([{"move": ["player", "mountain_path"]}])
    raw = "快放我过去。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    assert len(menu.people) >= 3
    a = _ans(**SAY, has_speech=0.97, meaningful=0.95, act={"request": 0.95, "tell": 0.05}, to={"all": 0.9, "p1": 0.1},
             cls={"trivial": 0.7, "effort": 0.3}, urgent=0.1, **{f"ask_{c.key}": 0.2 for c in menu.asks})
    b = parser.bind(scene.state, scene.defs, _result(scene, raw, a, menu))
    assert b.speech is not None and b.speech.act == "request" and b.speech.to == ["sn_chief_b"]
    assert binding._in_charge(scene.state, ["sn_guard_3", "sn_guard_4"]) is None


async def test_exits_off_the_map_are_real_targets(scene: Scene) -> None:
    scene.fx([{"move": ["player", "mountain_path"]}])
    raw = "一直往东走，离开这片大山。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    out = next(o for o in menu.objects if o.kind == "exit" and o.ref not in scene.state.locations)
    a = _ans(**ACT, verb={"go": 1.0}, target={out.key: 0.9, "none": 0.1}, target2={"none": 1.0},
             has_speech=0.2, meaningful=0.97)
    b = parser.bind(scene.state, scene.defs, _result(scene, raw, a, menu))
    assert b.kind == "action" and b.action is not None and b.action.params.get("exit") == out.ref


async def test_a_bare_verb_phrase_is_an_action(scene: Scene) -> None:
    """「等一等」：J1 的 verb 认定是等候，kind 却没多大把握地答成说话；规则也说整句只是个动作 → 按动作算。"""
    raw = "等一等"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    g = rules.guess(scene.state, scene.defs, raw, menu)
    assert g.kind == "act" and g.verb == "wait"
    a = _ans(kind={"say": 0.83, "act": 0.10, "both": 0.07}, verb={"wait": 1.0}, target={"none": 1.0},
             target2={"none": 1.0}, has_speech=0.79, meaningful=0.92, act={"request": 0.96, "tell": 0.04},
             to={"all": 0.92}, cls={"trivial": 0.9}, urgent=0.1)
    it = intake.intent_from_jev(a, menu, TH, [], g)
    assert (it.kind, it.verb, it.act) == ("act", "wait", None)
    # 没有规则猜测作证，或 kind 很有把握：照 J1 的
    assert intake.intent_from_jev(a, menu, TH, []).kind == "say"
    a["kind"] = _ans(kind={"say": 0.95, "act": 0.05})["kind"]
    assert intake.intent_from_jev(a, menu, TH, [], g).kind == "say"


async def test_close_ask_candidates_are_split_by_the_class_answer(scene: Scene) -> None:
    scene.move_player("zhong_hall")
    raw = "夫人，把金盒给我看看吧。"
    menu = intake.build_menu(scene.state, scene.defs, raw)
    give = next(c for c in menu.asks if c.type == "give_item" and c.slots.get("item") == "gold_box")
    show = next(c for c in menu.asks if c.type == "show_item" and c.slots.get("item") == "gold_box")
    lady = menu.person("gan_baobao")
    assert lady is not None and give.cls != "trivial" and show.cls == "trivial"
    base = dict(has_speech=0.97, meaningful=0.95, act={"request": 0.99, "tell": 0.01}, to={lady.key: 0.99}, urgent=0.1)

    def asks(p_give: float, p_show: float, cls: dict[str, float]) -> str | None:
        a = _ans(**SAY, **base, cls=cls,
                 **{f"ask_{c.key}": (p_give if c.key == give.key else p_show if c.key == show.key else 0.05)
                    for c in menu.asks})
        return intake.intent_from_jev(a, menu, TH, []).asks

    trivial = {"trivial": 0.85, "costly": 0.15}
    assert asks(0.90, 0.86, trivial) == show.key  # 咬得很紧：听 cls 的
    assert asks(0.86, 0.90, trivial) == show.key
    assert asks(0.95, 0.70, trivial) == give.key  # 差得远：取概率高的
    assert asks(0.90, 0.86, {"trivial": 0.5, "costly": 0.45}) == give.key  # cls 自己没把握：取概率高的
