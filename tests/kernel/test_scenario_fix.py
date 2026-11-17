"""分岔场景补上的几处内核机制（docs/content_notes.md「分岔场景补的内容」）：

- 同一次结算里新插进来的步骤（答应 + 当场照做）不算「已作废步骤的事件」；
- 答应「跟着走」是持续的一步；
- 「先服下我的毒」只在所求是放人、解药又在开条件的人手里时才开得出；
- 「由我来」（volunteer）：照常裁决，答应即授身份；角色位的 pick 认 prefer；
- 得知与所求有关的事（world.yaml 的 concerns）立即入重新打算的队列；
- 门派之间的敌对（world.yaml 的 enmity）。
"""

from __future__ import annotations

from tianlong.kernel import actions, danger, effects, invariants, mood, predicates, speech
from tianlong.kernel.defs import SituationDef
from tianlong.kernel.state import TICKS_PER_DAY, Ctx, Fact, Request, Speech

from .conftest import World


def own_defs(w: World) -> None:
    """这一条测试要改内容定义：先拷一份，别动夹具里共用的那份。"""
    w.defs = w.defs.model_copy(deep=True)


def befriend(w: World, a: str, b: str) -> None:
    w.fx([{"rel": [a, b, "trust", 70]}, {"rel": [a, b, "affinity", 70]}])


def ask_follow(w: World) -> Request:
    befriend(w, "guest", "player")
    w.say("player", "request", ["guest"], ask_action={"verb": "follow", "params": {"target": "player"}})
    r = next(r for r in w.s.requests.values() if r.frm == "player" and r.to == "guest")
    assert r.verdict == "may"
    return r


# ---------------------------------------------------------------- 不变量：结算当中新插的步骤


def test_agree_and_do_in_one_apply_is_not_rolled_back(w: World) -> None:
    r = ask_follow(w)
    n = len(w.s.violations)
    evs = w.act("guest", "follow", target="player", speech=Speech(act="agree", to=["player"], request=r.id))
    assert not [v for v in w.s.violations[n:] if "rollback" in v], w.s.violations[n:]
    assert r.status == "agreed" and w.c("guest").following == "player"
    assert any(e.step == f"req_{r.id}" for e in evs)


def test_event_of_a_step_finished_before_the_apply_is_still_caught(w: World) -> None:
    w.fx([{"step": ["guest", {"id": "old", "text": "等着", "do": {"verb": "wait"}, "takes": 1}]}])
    w.fx([{"step_state": ["guest", "old", "void"]}])
    ev = w.act("guest", "wait", ticks=1)[0]
    ev = ev.model_copy(update={"step": "old"})
    bad = invariants.check(w.s, w.defs, new_events=[ev], pending_before=set(), steps_before={"old"})
    assert any("finished or void step old" in p for p in bad)
    # 之前没有的步骤（这次结算里才插进来的）不算
    assert not invariants.check(w.s, w.defs, new_events=[ev], pending_before=set(), steps_before=set())


# ---------------------------------------------------------------- 答应「跟着走」


def test_agreed_follow_is_a_lasting_step(w: World) -> None:
    r = ask_follow(w)
    w.say("guest", "agree", ["player"], request=r.id)
    g = w.c("guest")
    st = danger.current_step(g)
    assert st is not None and st.id == f"req_{r.id}" and st.takes == speech.FOLLOW_TICKS == TICKS_PER_DAY
    w.act("guest", "follow", target="player")
    # 跟上之后这一步还在；★ 是「等」（接着跟），不是「不再跟着」
    st = danger.current_step(g)
    assert st is not None and st.id == f"req_{r.id}" and g.following == "player"
    assert actions.following_step(w.s, g, st)
    stars = [a for a in w.legal("guest") if a.star]
    assert stars and all(a.verb == "wait" for a in stars)
    # 领头的走到哪里，他跟到哪里；到了那里也不自己走回去（当前这一步仍是跟着）
    evs = w.act("player", "go", exit="yard")
    assert g.loc == "yard" and any(e.verb == effects.FOLLOW_MOVE and e.actor == "guest" for e in evs)
    assert not [a for a in w.legal("guest") if a.star and a.verb == "go"]
    # 原地等着也是在做这一步：记进度，不算做完
    w.act("guest", "wait", ticks=1)
    assert danger.current_step(g).id == f"req_{r.id}" and g.agenda.progress >= 2


def test_following_ends_when_the_leader_is_gone(w: World) -> None:
    r = ask_follow(w)
    w.say("guest", "agree", ["player"], request=r.id)
    w.act("guest", "follow", target="player")
    g = w.c("guest")
    w.s.replan_queue.clear()
    w.fx([{"follow": ["guest", None]}])  # 被人拿住、自己逃开时内容里写的就是这个效果
    assert g.following is None
    assert next(s for s in g.agenda.steps if s.id == f"req_{r.id}").state == "void"
    assert "guest" in w.s.replan_queue


def test_follower_of_someone_who_leaves_the_map_replans(w: World) -> None:
    befriend(w, "girl", "guest")
    w.fx([{"remove_status": ["girl", "hidden"]}])
    w.fx([{"step": ["girl", {"id": "tag", "text": "跟着他", "do": {"verb": "follow", "target": "guest"}, "takes": 40}]}])
    w.act("girl", "follow", target="guest")
    assert danger.current_step(w.c("girl")).id == "tag"  # 跟上了，还要跟满 takes
    w.s.replan_queue.clear()
    w.fx([{"leave": "guest"}])
    assert w.c("girl").following is None
    assert next(s for s in w.c("girl").agenda.steps if s.id == "tag").state == "void"
    assert "girl" in w.s.replan_queue


# ---------------------------------------------------------------- 还价：先服下我的毒


def _req(w: World, action: dict) -> Request:
    r = Request(id="RX", frm="player", to="fighter", action=action, action_class="costly", created=w.s.tick)
    w.s.requests[r.id] = r
    return r


def test_poison_condition_needs_a_release_ask_and_the_cure_in_hand(w: World) -> None:
    use = {"verb": "use", "params": {"item": "poison"}}
    stop = _req(w, {"verb": "wait", "params": {}})
    assert use not in speech.counter_options(w.s, w.defs, stop)  # 劝人罢手：开不出「先服毒」
    free = _req(w, {"verb": "release", "params": {"target": "guest"}})
    assert use not in speech.counter_options(w.s, w.defs, free)  # 解药不在他手里：那是要命，不是条件
    w.fx([{"give": ["guest", "fighter", "antidote"]}])
    assert speech.leash_poison(w.s, w.c("fighter"), "poison")
    assert use in speech.counter_options(w.s, w.defs, free)
    assert use not in speech.counter_options(w.s, w.defs, stop)
    permit = _req(w, {"verb": "wait", "params": {}, "permit": {"verb": "follow", "target": "guest"}})
    assert use in speech.counter_options(w.s, w.defs, permit)  # 放行也是放人一类


# ---------------------------------------------------------------- 由我来（volunteer）


ERRAND = {
    "id": "errand",
    "origin": "generic",
    "when": {"flag": "need_runner"},
    "roles": {
        "boss": "fighter",
        "runner": {"pick": {"prefer": {"role": ["$it", "runner_volunteer"]}, "where": {"not": {"role": ["$it", "hall_sect"]}}}},
    },
    "at": "$boss",
    "actions": {},
    "volunteer": {"role": "runner", "to": "boss", "grant": "runner_volunteer", "cls": "effort", "urgent": True, "desc": "让{volunteer}替{boss}跑一趟"},
}
NOTE = {"verb": "wait", "params": {"ticks": 1}, "volunteer": {"situation": "errand", "role": "runner"}}


def with_errand(w: World) -> None:
    own_defs(w)
    w.defs.situations["errand"] = SituationDef(**ERRAND)


def test_volunteer_request_is_scored_and_grants_the_role_on_agreement(w: World) -> None:
    with_errand(w)
    # 那件事眼下无从谈起：谈不成
    assert speech.volunteer_spec(w.s, w.defs, "fighter", NOTE) is None
    w.say("player", "request", ["fighter"], ask_action=dict(NOTE))
    r0 = next(r for r in w.s.requests.values() if r.frm == "player")
    assert r0.verdict == "never"
    r0.status = "expired"
    w.fx([{"set_flag": ["need_runner", True]}])
    assert speech.volunteer_spec(w.s, w.defs, "fighter", NOTE) is not None
    assert speech.volunteer_spec(w.s, w.defs, "guest", NOTE) is None  # 开口的对象不是主事的人
    assert speech.classify_request(w.s, w.defs, "player", "fighter", NOTE) == "effort"
    w.say("player", "request", ["fighter"], ask_action=dict(NOTE))
    r = next(r for r in w.s.requests.values() if r.frm == "player" and r.status == "open")
    # 费事 × 陌生 30 分，正好帮到急需 +30（内容里写了 urgent，不靠解析器）
    assert r.action_class == "effort" and r.leverage == 30 and r.score == 60 and r.verdict == "may"
    steps = len(w.c("fighter").agenda.steps) if w.c("fighter").agenda else 0
    w.say("fighter", "agree", ["player"], request=r.id)
    assert r.status == "agreed" and "runner_volunteer" in w.c("player").roles
    # 答应即定：不插步骤，不记许诺
    assert (len(w.c("fighter").agenda.steps) if w.c("fighter").agenda else 0) == steps
    assert not [p for p in w.s.promises.values() if p.request == r.id]


def test_pick_prefers_the_volunteer(w: World) -> None:
    with_errand(w)
    w.fx([{"set_flag": ["need_runner", True]}, {"remove_status": ["girl", "hidden"]}])
    d = w.defs.situations["errand"]
    first = effects.bind_roles(w.s, w.defs, d, Ctx())
    assert first is not None and first[1]["runner"] != "girl"
    w.fx([{"role": ["girl", "runner_volunteer", "add"]}])
    again = effects.bind_roles(w.s, w.defs, d, Ctx())
    assert again is not None and again[1]["runner"] == "girl"


# ---------------------------------------------------------------- 与所求有关的大事（concerns）


def test_learning_a_fact_that_concerns_a_drive_queues_a_replan(w: World) -> None:
    own_defs(w)
    w.defs.world.concerns = {"clan_honor": ["sect_scandal"]}
    w.s.facts["F_scandal"] = Fact(id="F_scandal", text="⟦guest⟧坏了⟦hall⟧的规矩。", tags=["sect_scandal"])
    w.s.replan_queue.clear()
    w.fx([{"learn": ["guest", "F_scandal", 1.0, "told"]}])
    assert "guest" not in w.s.replan_queue  # 他没有这种所求
    w.fx([{"learn": ["fighter", "F_scandal", 0.4, "told"]}])
    assert "fighter" not in w.s.replan_queue  # 半信半疑：还不算
    w.fx([{"learn": ["fighter", "F_scandal", 0.9, "told"]}])
    assert "fighter" in w.s.replan_queue
    w.s.replan_queue.clear()
    w.fx([{"learn": ["fighter", "F_scandal", 1.0, "saw"]}])
    assert "fighter" not in w.s.replan_queue  # 早就信了：不是新出的事


# ---------------------------------------------------------------- 门派之间的敌对（enmity）


def test_faction_enmity_follows_roles_and_flag(w: World) -> None:
    own_defs(w)
    w.defs.world.enmity = [{"between": ["hall_sect", "valley_family"], "while": "feud_on"}]
    fighter, girl = w.c("fighter"), w.c("girl")
    pred = {"enemies": ["fighter", "girl"]}
    assert not predicates.holds(w.s, w.defs, pred)  # 旗标没立
    w.fx([{"set_flag": ["feud_on", True]}])
    assert predicates.holds(w.s, w.defs, pred) and predicates.holds(w.s, w.defs, {"enemies": ["girl", "fighter"]})
    assert not predicates.holds(w.s, w.defs, {"enemies": ["fighter", "guest"]})
    assert mood.band(w.defs, w.s, "fighter", "girl") == "hostile"
    # 只拿得到 state 的查询读状态里的副本：内核入口进门时抄进去
    assert not danger.is_hostile(w.s, fighter, "girl")
    w.act("guest", "wait", ticks=1)
    assert predicates.ENMITY_FLAG in w.s.flags
    assert danger.is_hostile(w.s, fighter, "girl") and danger.is_hostile(w.s, girl, "fighter")
    assert not danger.is_hostile(w.s, fighter, "guest")
    # 有私交的不在此列
    w.fx([{"rel": ["fighter", "girl", "affinity", 40]}, {"rel": ["fighter", "girl", "trust", 40]}])
    assert not danger.is_hostile(w.s, fighter, "girl") and mood.band(w.defs, w.s, "fighter", "girl") != "hostile"
    assert danger.is_hostile(w.s, girl, "fighter")
    # 身份没了，或旗标落了，敌对也就没了
    w.fx([{"role": ["girl", "valley_family", "remove"]}])
    assert not danger.is_hostile(w.s, girl, "fighter") and not predicates.holds(w.s, w.defs, pred)
    w.fx([{"role": ["girl", "valley_family", "add"]}, {"set_flag": ["feud_on", False]}])
    assert not danger.is_hostile(w.s, girl, "fighter") and not predicates.holds(w.s, w.defs, pred)
    # 同时带着两边身份的人不算哪一边
    w.fx([{"set_flag": ["feud_on", True]}, {"role": ["girl", "hall_sect", "add"]}])
    assert not predicates.holds(w.s, w.defs, pred)
