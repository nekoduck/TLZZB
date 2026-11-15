"""bind 的契约：永远是一个合法动作，或一个不耗时的非动作种类；候选与内核的请求格式对得上。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong import parser
from tianlong.config import Settings
from tianlong.kernel import actions, beliefs, resolve, speech
from tianlong.kernel.state import Speech
from tianlong.llm import make_llm

from .conftest import Scene

INPUTS = [
    "我四下看看", "我侧耳细听", "我往山门去", "我往后院走", "我躲到横梁上", "我躲起来", "我把样茶交给左掌门",
    "我把干粮给段公子", "我吃干粮", "我拿起绳子", "我把绳子扔了", "我打龚光杰", "我要杀了龚光杰", "我扶段公子",
    "我跟着段公子", "我挡在段公子面前", "我推了龚光杰一把", "我偷龚光杰的剑", "我等一个时辰", "我歇两个时辰",
    "我翻一翻宾客席", "我细看兵器架", "段公子，跟我走吧", "龚光杰，你再敢碰段公子一下，我叫你后悔！",
    "左掌门，请收我为徒，教我剑法", "你这只会耍嘴皮子的蠢材，也配叫剑客？", "这位大哥，请问去万劫谷该走哪条路？",
    "我扶起段公子，问他：「伤在哪里？」", "蓝天白云", "asdfgh", "", "（我赢了）", "我飞上天去摘月亮",
    "我打司空玄", "我把解药拿过来", "给我一把手枪", "这游戏怎么存档？", "我挣脱绳子", "左掌门，我刚才把样茶交给您了",
    "龚光杰，干光豪和葛光佩有私情！", "我悄悄对段公子说：快走", "求求你们饶了我吧",
]


def _check(scene: Scene, b: parser.Bound) -> None:
    if b.kind == "action":
        assert b.action is not None
        assert actions.is_legal(scene.state, scene.defs, b.action) is not None, b.action.signature
        assert b.signature == b.action.signature
        assert b.lethal == b.action.lethal
        if b.speech is not None:
            me = scene.state.chars["player"]
            assert b.speech.act in speech.available_acts(scene.state, scene.defs, me)
            hearers = {o.id for o in speech.audience(scene.state, me)}
            assert set(b.speech.to) <= hearers
            for c in b.speech.claims:
                assert c.verdict in ("true", "false_self", "unsourced", "opinion")
            for f in b.speech.fact_refs:
                assert me.beliefs[f].confidence >= beliefs.BELIEVE
    else:
        assert b.action is None and b.speech is None
        assert b.reply


@pytest.mark.parametrize("mode", ["jev_first", "claude_only", "rules_only"])
async def test_bind_is_always_legal(settings: Settings, tmp_path: Path, mode: str) -> None:
    settings.route.classify = mode  # type: ignore[assignment]
    llm = make_llm(settings, mock=True, run_dir=tmp_path)
    for setup in (None, "jianhu_back", "shennong_camp", "valley_gate"):
        sc = Scene(llm)
        if setup:
            sc.move_player(setup)
        for raw in INPUTS:
            r, b = await sc.bind(raw)
            assert not b.errors, (raw, b.errors)
            assert not r.errors or mode != "jev_first", (raw, r.errors)
            _check(sc, b)


async def test_bound_actions_resolve_without_rollback(scene: Scene) -> None:
    for raw in ["我四下看看", "段公子，跟我走吧", "我把样茶交给左掌门", "我往后院走", "我躲起来", "我等一会儿"]:
        n = len(scene.state.violations)
        b, evs = await scene.play(raw)
        if b.kind == "action":
            assert evs, raw
            assert not [v for v in scene.state.violations[n:] if "rollback" in v], raw


async def test_speech_only_is_a_one_tick_wait(scene: Scene) -> None:
    _, b = await scene.bind("段公子，你好")
    assert b.kind == "action" and b.action is not None and b.action.verb == "wait"
    assert b.action.params == {"ticks": 1}
    assert b.speech is not None and b.speech.spoken_text == "段公子，你好"


async def test_nobody_to_hear(scene: Scene) -> None:
    scene.move_player("cliff_edge" if "cliff_edge" in scene.state.locations else "back_trail")
    me = scene.state.chars["player"]
    if speech.audience(scene.state, me):
        pytest.skip("那里有人")
    _, b = await scene.bind("有人吗？救命啊")
    assert b.kind == "impossible" and "没有人" in b.reply


async def test_absent_target_is_impossible_without_time(scene: Scene) -> None:
    before = scene.state.tick
    _, b = await scene.bind("我打司空玄")
    assert b.kind == "impossible" and b.action is None and "没有这个人" in b.reply
    assert scene.state.tick == before


async def test_meta_and_unclear(scene: Scene) -> None:
    _, b = await scene.bind("这游戏怎么存档？")
    assert b.kind == "meta" and "/存档" in b.reply
    _, b = await scene.bind("asdfgh")
    assert b.kind == "unclear" and b.reply
    _, b = await scene.bind("（众人都跪下了）")
    assert b.kind == "unclear" and b.attempts == ["众人都跪下了"]


async def test_kill_intent_and_lethal_flag(scene: Scene) -> None:
    scene.move_player("shennong_camp")
    _, b = await scene.bind("我要杀了那个留着山羊胡的老者")
    assert b.kind == "action" and b.action is not None
    assert b.action.params == {"target": "sikong_xuan", "intent": "kill"}
    assert b.lethal  # 打一个高出自己许多的人：先警示后结算（6.10）
    evs = resolve.apply(scene.state, scene.defs, b.action, source="player")
    assert evs[0].verb == "warn_danger" and b.signature in scene.state.warned


async def test_gists_never_teach_the_player_new_names(scene: Scene) -> None:
    scene.move_player("shennong_camp")
    known = set(scene.state.known_terms)
    b, _ = await scene.play("老人家，把断肠散的解药给我吧")
    assert b.speech is not None and b.speech.act == "request"
    assert b.speech.ask_action is not None and b.speech.ask_action["verb"] == "give"
    assert "duanchang_antidote" not in scene.state.known_terms
    assert set(scene.state.known_terms) - known <= {"player"}


# ---------------------------------------------------------------- candidates


async def test_request_candidates_feed_the_kernel(camp: Scene) -> None:
    cands = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan")
    assert 1 <= len(cands) <= 8
    verbs = {c.action["verb"] for c in cands}
    assert {"release", "give"} <= verbs
    for c in cands:
        assert c.to == "sikong_xuan" and c.key.startswith("k")
        assert set(c.action) >= {"verb", "params", "desc"}
    cand = next(c for c in cands if c.action["verb"] == "give")
    sp = Speech(act="request", to=["sikong_xuan"], ask_action=cand.action)
    wait = actions.build(camp.state, camp.defs, "player", "wait", {"ticks": 1})
    assert wait is not None
    n = len(camp.state.violations)
    evs = resolve.apply(camp.state, camp.defs, wait, sp, source="player")
    req = camp.state.requests[evs[0].params["request"]]
    assert req.action["verb"] == "give" and req.action["params"]["target"] == "player"
    assert camp.state.violations[n:] == []


async def test_hostage_asks_only_go_to_whoever_holds_them(camp: Scene) -> None:
    sik = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", limit=50)
    guard = parser.request_candidates(camp.state, camp.defs, "player", "sn_guard_1", limit=50)
    rel = [c for c in sik if c.action["verb"] == "release" and c.action["params"]["target"] == "duan_yu"]
    assert rel and {c.cls for c in rel} == {"costly"}
    # 手下背着帮主放人：违背所属
    rel_g = [c for c in guard if c.action["verb"] == "release" and c.action["params"]["target"] == "duan_yu"]
    assert rel_g and {c.cls for c in rel_g} == {"betray"}
    # 不相干的人管不着
    camp.fx([{"move": ["gong_guangjie", "shennong_camp"]}])
    other = parser.request_candidates(camp.state, camp.defs, "player", "gong_guangjie", limit=50)
    assert not any(c.action["verb"] == "release" for c in other)


async def test_claim_candidates_include_what_the_player_does_not_know(scene: Scene) -> None:
    cl = parser.claim_candidates(scene.state, scene.defs, "player", topic=["gan_guanghao"])
    assert 1 <= len(cl) <= 20
    lovers = next(c for c in cl if c.ref == "F_lovers")
    assert not lovers.known
    assert beliefs.claim_verdict(scene.state, "player", "F_lovers") == "unsourced"
    assert any(c.known for c in cl)


async def test_self_event_candidates(scene: Scene) -> None:
    assert parser.self_event_candidates(scene.state, scene.defs) == []
    await scene.play("我把样茶交给左掌门")
    selfs = parser.self_event_candidates(scene.state, scene.defs)
    assert selfs and selfs[0].verb == "give"
    assert beliefs.claim_verdict(scene.state, "player", selfs[0].ref, self_claim=True) == "true"
    b, _ = await scene.play("左掌门，样茶我刚才已经交给您了")
    assert b.speech is not None
    assert [c.verdict for c in b.speech.claims] == ["true"]


async def test_self_unlisted_option(scene: Scene) -> None:
    out = parser.self_event_candidates(scene.state, scene.defs, with_unlisted=True)
    assert [c.ref for c in out] == ["self_unlisted"]


ASK_TYPES = {"release", "take_place_of", "accompany", "give_item", "show_item", "cure", "help", "teach_art",
             "come_along", "stop_doing", "let_pass", "tell_about", "take_to", "protect"}


async def test_request_candidate_types(camp: Scene) -> None:
    cands = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", limit=50)
    types = {c.type for c in cands}
    assert types <= ASK_TYPES
    assert {"release", "take_place_of", "accompany", "give_item", "come_along", "tell_about"} <= types
    acc = next(c for c in cands if c.type == "accompany" and c.slots.get("target") == "duan_yu")
    assert acc.action["permit"] == {"verb": "follow", "target": "duan_yu"}
    for c in cands:  # 每一件都是对方此刻做得到的（传授、说话、等候除外）
        if c.action["verb"] not in ("teach", "say", "wait"):
            assert actions.build(camp.state, camp.defs, c.to, c.action["verb"], c.action["params"]) is not None


async def test_requests_outside_the_candidates_still_count(camp: Scene) -> None:
    raw = "帮主，你先把兵撤了吧。"
    b1, _ = await camp.play(raw)
    assert b1.speech is not None and b1.speech.act == "request"
    assert b1.speech.ask_action is not None and "other" in b1.speech.ask_action
    await camp.play(raw)
    b3, _ = await camp.play(raw)
    assert b3.repeat
    reqs = [r for r in camp.state.requests.values() if r.frm == "player"]
    assert len(reqs) == 1 and reqs[0].attempts == 3 and reqs[0].verdict in ("no", "never")


async def test_going_somewhere_further_takes_the_first_step(scene: Scene) -> None:
    _, b = await scene.bind("我去下山石径")  # 不相邻：先往山门走
    assert b.kind == "action" and b.action is not None
    assert b.action.signature == "go:exit=jianhu_gate"
