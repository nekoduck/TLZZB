"""分岔场景补上的几处世界层机制（docs/wip/scenario-fix.md），真实的 content/：

- 掌门得知门人的私情（带 sect_scandal 标签的事实）→ 立即重新打算 → 「清理门户」压过「照旧」；
- goals.yaml 的 fact_subject / threat_here 绑定方法，pressing / cooldown 两个规则键；
- 没写 ask 的请求步骤用内容里的 gist，写了 ask 的由内核按所求之事拼要旨；
- 围山期间神农帮与无量剑互为对头（world.yaml 的 enmity），离场的盘问照此结算。
"""

from __future__ import annotations

from tianlong.kernel import danger, mood, predicates
from tianlong.kernel.state import Ctx
from tianlong.world import goals, offscreen, replan
from tianlong.world.common import GOAL_FLAG

from .conftest import fx, hours, player_off


def test_head_who_learns_of_the_scandal_replans_at_once(world):
    defs, state = world
    zz = state.chars["zuo_zimu"]
    assert "zuo_zimu" not in state.replan_queue
    fx(state, defs, [{"learn": ["zuo_zimu", "F_lovers", 0.7, "told:player"]}])
    assert "zuo_zimu" in state.replan_queue
    cands = goals.candidates(state, defs, "zuo_zimu", allow_keep=True)
    purge = [c for c in cands if c.rule == "honor_purge"]
    assert purge and purge[0].pressing
    assert purge[0].bindings["culprit"] in ("gan_guanghao", "ge_guangpei")
    assert purge[0].steps[0]["do"] == {"verb": "restrain", "target": purge[0].bindings["culprit"]}
    # 等不得的事：候选里不再有「照旧」，排第一的就是它
    assert not [c for c in cands if c.keep] and cands[0].rule == "honor_purge"
    # 普通弟子知道了也不归他管；不知道这件事的掌门没有这个候选
    assert not [c for c in goals.candidates(state, defs, "xin_shuangqing", allow_keep=True) if c.rule == "honor_purge"]
    assert zz.beliefs["F_lovers"].confidence >= predicates.BELIEVE


async def test_the_lovers_are_seized_and_never_elope(llm, world):
    defs, state = world
    player_off(state, defs)
    fx(state, defs, [{"learn": ["zuo_zimu", "F_lovers", 0.7, "told:player"]}])
    events = await hours(llm, state, defs, 6, rules_only=True)
    held = lambda cid: any(s.kind in ("captive", "bound") for s in state.chars[cid].statuses)  # noqa: E731
    assert held("gan_guanghao") and held("ge_guangpei"), [e.fact_text for e in events if "zuo_zimu" in e.fact_text]
    # 拿下第二个人之后，这条规则不再成立，「照旧」回到候选里
    cands = goals.candidates(state, defs, "zuo_zimu", allow_keep=True)
    assert not [c for c in cands if c.rule == "honor_purge"]
    # 私逃那场撞见人的情境此后开不了场
    d = defs.situations["caught_eloping"]
    assert not predicates.holds(state, defs, d.when, Ctx())


def test_fact_subject_binder_follows_beliefs(world):
    defs, state = world
    zz = state.chars["zuo_zimu"]
    spec = {"fact_subject": {"tag": "sect_scandal", "where": {"role": ["$it", "wuliang_disciple"]}}}
    assert goals.bind_one(state, defs, zz, spec, {}) is None  # 他还不知道
    fx(state, defs, [{"learn": ["zuo_zimu", "F_lovers", 1.0, "saw"]}])
    assert goals.bind_one(state, defs, zz, spec, {}) in ("gan_guanghao", "ge_guangpei")
    nobody = {"fact_subject": {"tag": "sect_scandal", "where": {"role": ["$it", "shennong_member"]}}}
    assert goals.bind_one(state, defs, zz, nobody, {}) is None


def test_threat_here_needs_a_real_threat(world):
    defs, state = world
    zz, xs = state.chars["zuo_zimu"], state.chars["xin_shuangqing"]
    assert zz.loc == xs.loc
    fx(state, defs, [{"rel": ["xin_shuangqing", "zuo_zimu", "affinity", -80]}])
    assert danger.is_hostile(state, xs, "zuo_zimu")
    assert goals.bind_one(state, defs, zz, {"hostile_here": "self"}, {}) == "xin_shuangqing"
    # 势均力敌、只是看不顺眼：不用躲
    assert goals.bind_one(state, defs, zz, {"threat_here": "self"}, {}) is None
    assert not [c for c in goals.candidates(state, defs, "zuo_zimu") if c.rule == "survive_flee"]
    # 对方正冲着他来：这才是威胁
    step = {"id": "t_hunt", "text": "拿下他", "do": {"verb": "restrain", "target": "zuo_zimu"}, "takes": 1}
    fx(state, defs, [{"step": ["xin_shuangqing", step]}])
    assert goals.bind_one(state, defs, zz, {"threat_here": "self"}, {}) == "xin_shuangqing"


async def test_cooldown_keeps_a_goal_from_repeating_every_hour(llm, world):
    defs, state = world
    player_off(state, defs)
    assert next(r for r in goals.rules(defs) if r["id"] == "honor_save_face").get("cooldown") == 96
    has = lambda: [c for c in goals.candidates(state, defs, "zuo_zimu") if c.rule == "honor_save_face"]  # noqa: E731
    cand = has()
    assert cand
    job = replan.Job(cid="zuo_zimu", trigger="void", what="", important=False)
    job.cands, job.choice, job.via = cand, cand[0], "rule"
    replan._land(state, defs, job)
    assert state.flags[GOAL_FLAG]["zuo_zimu"]["honor_save_face"] == state.tick
    assert not has()  # 刚选过：一日之内不再进候选
    state.tick += 96
    assert has()


async def test_request_steps_get_a_readable_gist(llm, world):
    defs, state = world
    player_off(state, defs)
    dy = state.chars["duan_yu"]
    st = next(s for s in dy.agenda.steps if s.id == "dy_plead_shennong")
    sp = offscreen._speech_for(state, dy, st)
    assert sp.gist == st.do["gist"] and not sp.gist.startswith("去")  # 内容里写的要旨，不是这一步的说明
    # 写明了所求之事的：不借步骤的说明（「去请某人……」），由内核按所求之事拼
    step = {"id": "t_ask", "text": "去请⟦sn_guard_3⟧助一臂之力", "at": "mountain_path",
            "do": {"verb": "say", "act": "request", "to": "sn_guard_3", "ask": {"verb": "follow", "params": {"target": "self"}}},
            "takes": 1}
    fx(state, defs, [{"step": ["sn_chief_b", step]}])
    events = await offscreen.advance(state, defs, "sn_chief_b", 8)
    said = next(e for e in events if e.verb == "say" and e.actor == "sn_chief_b")
    assert "去请" not in said.fact_text and "跟着" in said.fact_text, said.fact_text


def test_siege_makes_the_two_sides_enemies(world):
    defs, state = world
    guard, disciple = "sn_chief_b", "gong_guangjie"
    pred = {"enemies": [guard, disciple]}
    assert not predicates.holds(state, defs, pred)  # 还没围山
    fx(state, defs, [{"set_flag": ["siege_on", True]}])
    assert predicates.holds(state, defs, pred)
    assert danger.is_hostile(state, state.chars[guard], disciple)
    assert mood.band(defs, state, guard, disciple) == "hostile"
    # 没有门派的外人不在此列；玩家成了无量剑弟子就算进去，身份没了又不算
    assert not danger.is_hostile(state, state.chars[guard], state.player)
    assert mood.band(defs, state, guard, state.player) == "stranger"
    fx(state, defs, [{"role": [state.player, "wuliang_disciple", "add"]}])
    assert danger.is_hostile(state, state.chars[guard], state.player)
    assert mood.band(defs, state, guard, state.player) == "hostile"
    fx(state, defs, [{"role": [state.player, "wuliang_disciple", "remove"]}])
    assert not danger.is_hostile(state, state.chars[guard], state.player)
    # 围山一解，敌对也就没了
    fx(state, defs, [{"role": [state.player, "wuliang_disciple", "add"]}, {"set_flag": ["siege_on", False]}])
    assert not danger.is_hostile(state, state.chars[guard], state.player)
    assert mood.band(defs, state, guard, disciple) != "hostile"


def test_roadblock_treats_an_enemy_disciple_as_such_offscreen(world):
    """离场的盘问：来人是对头门下，守卡的动手拿人（逃得掉就被赶开），不走「问几句放行」那一条。"""
    defs, state = world
    d = defs.situations["challenge"]
    fx(state, defs, [{"set_flag": ["siege_on", True]}])
    ctx = Ctx(roles={"keeper": "sn_chief_b", "intruder": "gong_guangjie"})
    hit = next(i for i, rule in enumerate(d.offscreen) if predicates.holds(state, defs, rule.when, ctx))
    assert any("check" in e and e["check"].get("kind") == "flee" for e in d.offscreen[hit].effects)
    other = Ctx(roles={"keeper": "sn_chief_b", "intruder": "ma_wude"})
    first = next(i for i, rule in enumerate(d.offscreen) if predicates.holds(state, defs, rule.when, other))
    assert first > hit
