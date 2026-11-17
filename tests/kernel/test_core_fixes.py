"""终审修补（core 包）：被拦着追着时走开要判定、图外的死没有人看见、动了人就藏不住、
没有救人就没有救命之恩、判不出守没守的许诺静默了结、侥幸不死可再逃一次。"""

from __future__ import annotations

import pytest

from tianlong.kernel import actions, checks, danger, invariants, perception, rng, timeflow
from tianlong.kernel.state import Agenda, Relation, Status, Step, is_alive

from .conftest import World


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """把 d100 换成给定的点数序列（用完以后一律 50）。"""
    seq: list[int] = []

    def roll(state):  # type: ignore[no-untyped-def]
        state.rng_counter += 1
        return seq.pop(0) if seq else 50

    monkeypatch.setattr(rng, "roll", roll)

    def set_(*vals: int) -> None:
        seq[:] = list(vals)

    return set_


def _rel(w: World, a: str, b: str) -> Relation:
    c = w.c(a)
    if b not in c.rel:
        c.rel[b] = Relation()
    return c.rel[b]


def _hunter(w: World, intent: str = "injure", power: int = 6) -> None:
    f = w.c("fighter")
    f.base_power = power
    f.agenda = Agenda(
        goal="k",
        steps=[Step(id="k1", text="k", do={"verb": "attack", "target": "player", "intent": intent})],
    )


# ---------------------------------------------------------------- 1. 被拦着、追着时走开


def test_go_under_pursuit_rolls_and_can_be_caught(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    _hunter(w)
    assert [o.id for o in danger.blockers(w.s, "player")] == ["fighter"]
    a = w.build("player", "go", exit="yard")
    assert a is not None and "夺路" in a.desc  # 合法清单上看得出这一步要闯
    assert checks.flee_chance(w.s, w.defs, w.c("player"), [w.c("fighter")]) == 5
    dice(100)
    before = w.s.rng_counter
    evs = w.act("player", "go", exit="yard")
    assert w.s.rng_counter == before + 1  # 掷了逃跑判定
    assert w.c("player").loc == "hall" and evs[-1].verb == "go" and evs[-1].outcome == "fail"
    assert evs[-1].params["winner"] == "fighter" and evs[-1].params["chance"] == 5
    assert w.status("player", "injured") is not None  # 被追上：按追者的意图挨了一下
    dice(1)
    evs = w.act("player", "go", exit="yard")
    assert w.c("player").loc == "yard" and evs[0].outcome == "ok"


def test_go_under_pursuit_many_seeds_mostly_caught() -> None:
    """高出 4 以上的追者：成功率封顶 10（6.4），走开不再是白送的。"""
    got_away = 0
    for seed in range(60):
        w = World(seed)
        _hunter(w)
        w.act("player", "go", exit="yard")
        got_away += w.c("player").loc == "yard"
    assert got_away <= 12


def test_go_after_block_rolls_against_blocker(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.act("guest", "block", target="player")  # 客人并无敌意，只是挡着
    assert w.status("player", "impeded").source == "guest"
    assert not danger.hostiles_toward(w.s, "player")
    assert [o.id for o in danger.blockers(w.s, "player")] == ["guest"]
    dice(100)
    before = w.s.rng_counter
    evs = w.act("player", "go", exit="yard")
    assert w.s.rng_counter == before + 1
    assert w.c("player").loc == "hall" and evs[-1].outcome == "fail"
    assert evs[-1].params["winner"] == "guest" and evs[-1].params["outcome"] == "push"
    assert "慌不择路" not in evs[-1].fact_text
    assert w.status("player", "injured") is None  # 没有敌意的人只是把人拦回去
    # 挡这一下用掉了（动作收尾清掉 impeded）：对方不再挡，下一回就走得开，不必再掷
    assert w.status("player", "impeded") is None and not danger.blockers(w.s, "player")
    before = w.s.rng_counter
    w.act("player", "go", exit="yard")
    assert w.c("player").loc == "yard" and w.s.rng_counter == before


def test_go_after_block_can_slip_past(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    w.act("guest", "block", target="player")
    dice(1)
    evs = w.act("player", "go", exit="yard")
    assert w.c("player").loc == "yard" and evs[0].outcome == "ok"


def test_contested_go_through_edge_exit(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    """被追着的人照样能走出地图（flee 不许走图外的出口，go 许）：成了离图，不成被追上。"""
    w.fx([{"move": ["guest", "yard"]}, {"move": ["fighter", "yard"]}])
    f = w.c("fighter")
    f.agenda = Agenda(goal="k", steps=[Step(id="k1", text="k", do={"verb": "restrain", "target": "guest"})])
    assert w.build("guest", "flee", exit="out_road") is None
    dice(100)
    w.act("guest", "go", exit="out_road")
    assert w.c("guest").onstage and w.status("guest", "bound") is not None
    w.c("guest").statuses.clear()
    dice(1)
    w.act("guest", "go", exit="out_road")
    assert not w.c("guest").onstage


def test_go_past_merely_hostile_person_is_free(w: World) -> None:
    _rel(w, "fighter", "player").affinity = -50
    assert danger.hostiles_toward(w.s, "player") and not danger.blockers(w.s, "player")
    a = w.build("player", "go", exit="yard")
    assert a is not None and "夺路" not in a.desc
    before = w.s.rng_counter
    evs = w.act("player", "go", exit="yard")
    assert w.s.rng_counter == before and w.c("player").loc == "yard"
    assert [e.verb for e in evs] == ["go", "arrive"]


def test_go_not_contested_by_restrained_or_unseeing_pursuer(w: World) -> None:
    _hunter(w)
    w.c("fighter").statuses.append(Status(kind="bound", source="guest"))
    assert not danger.blockers(w.s, "player")
    w.c("fighter").statuses.clear()
    w.c("player").statuses.append(Status(kind="hidden", source="beam"))
    assert not danger.blockers(w.s, "player")  # 看不见的人拦不住
    before = w.s.rng_counter
    w.act("player", "go", exit="yard")
    assert w.s.rng_counter == before and w.c("player").loc == "yard"


def test_npc_go_under_pursuit_also_rolls(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    f = w.c("fighter")
    f.base_power = 6
    f.agenda = Agenda(goal="k", steps=[Step(id="k1", text="k", do={"verb": "restrain", "target": "guest"})])
    dice(100)
    evs = w.act("guest", "go", exit="yard")
    assert w.c("guest").loc == "hall" and evs[-1].outcome == "fail"
    assert w.status("guest", "bound") is not None  # 要拿人的追上了就捆


# ---------------------------------------------------------------- 2. 图外的死


def test_offmap_poison_death_is_unseen_and_keeps_belongings(w: World) -> None:
    w.fx([{"add_status": ["guest", "poisoned_x", {"ticks": 3}]}, {"leave": "guest"}])
    p = w.c("player")
    mood0 = dict(p.mood)
    mem0 = len(p.memory)
    n0 = w.s.next_event_id
    evs = timeflow.advance_time(w.s, w.defs, 4)
    g = w.c("guest")
    assert not is_alive(g) and not g.onstage
    assert not [e for e in evs if e.loc == "hall" and e.witnesses]
    assert not [e for e in w.s.events_tail if e.id >= n0 and e.verb == "die"]
    assert p.mood == mood0 and len(p.memory) == mem0
    for iid in ("antidote", "token"):
        assert w.s.items[iid].holder == "guest" and iid in g.inventory
    assert perception.player_view(w.s, w.defs, evs).events == []
    assert invariants.check(w.s, w.defs) == []


def test_offmap_injury_and_binding_are_unseen(w: World) -> None:
    w.fx([{"leave": "guest"}])
    for eff in ({"injure": ["guest", 1]}, {"add_status": ["guest", "bound"]}, {"kill": "guest"}):
        evs = w.fx([eff])
        assert evs == [], eff
    assert not is_alive(w.c("guest")) and w.s.items["token"].holder == "guest"


def test_offmap_death_bookkeeping_still_done(w: World) -> None:
    from tianlong.kernel.state import Promise

    w.c("girl").following = "guest"
    w.s.promises["P9"] = Promise(id="P9", frm="guest", to="player", text="x", deadline=999)
    w.fx([{"leave": "guest"}])
    w.fx([{"kill": "guest"}])
    assert w.s.promises["P9"].status == "broken" and w.c("girl").following is None


def test_offmap_death_with_explicit_emit_is_still_narrated(w: World) -> None:
    w.fx([{"leave": "guest"}])
    evs = w.fx([{"kill": "guest"}, {"emit": {"text": "消息传来", "loc": "hall"}}])
    assert len(evs) == 1 and any(k.get("k") == "death" for k in evs[0].kinds)


def test_onmap_death_still_seen_and_drops_belongings(w: World) -> None:
    evs = w.fx([{"kill": "guest"}])
    assert [e.verb for e in evs] == ["die"] and "player" in evs[0].witnesses
    assert w.s.items["token"].holder is None and w.s.items["token"].loc == "hall"


# ---------------------------------------------------------------- 3. 动了人就藏不住


def _hide(w: World, cid: str = "player") -> None:
    w.c(cid).statuses.append(Status(kind="hidden", source="rack"))


@pytest.mark.parametrize(
    ("verb", "params"),
    [
        ("restrain", {"target": "fighter"}),
        ("push", {"target": "fighter"}),
        ("block", {"target": "fighter"}),
        ("give", {"target": "fighter", "item": "rope"}),
        ("show", {"target": "fighter", "item": "rope"}),
    ],
)
def test_hidden_actor_touching_someone_is_revealed(w: World, verb: str, params: dict) -> None:
    w.c("player").base_power = 3
    _hide(w)
    evs = w.act("player", verb, **params)
    assert w.status("player", "hidden") is None
    assert "fighter" in evs[-1].witnesses
    assert perception.visible_to(w.c("fighter"), w.c("player"))
    assert w.has("guest", "examine", target="player")  # 别人又能冲着他来了


def test_hidden_actor_release_and_use_on_target_reveal(w: World) -> None:
    w.c("guest").statuses.append(Status(kind="bound", source="fighter"))
    _hide(w)
    evs = w.act("player", "release", target="guest")
    assert w.status("player", "hidden") is None and "guest" in evs[-1].witnesses
    _hide(w)
    w.c("guest").statuses.append(Status(kind="injured", level=1))
    evs = w.act("player", "help", target="guest")
    assert w.status("player", "hidden") is None and "guest" in evs[-1].witnesses
    _hide(w)
    evs = w.act("player", "use", item="rations", target="guest")
    assert w.status("player", "hidden") is None and "guest" in evs[-1].witnesses


@pytest.mark.parametrize(
    ("verb", "params"),
    [
        ("look", {}),
        ("examine", {"target": "fighter"}),
        ("drop", {"item": "rope"}),
        ("push", {"feature": "rack"}),
        ("use", {"item": "rations"}),
        ("wait", {"ticks": 1}),
    ],
)
def test_hidden_actor_quiet_solitary_action_stays_hidden(w: World, verb: str, params: dict) -> None:
    w.s.locations["hall"].features["rack"].push = [{"set_flag": ["rack_pushed", True]}]
    _hide(w)
    evs = w.act("player", verb, **params)
    assert w.status("player", "hidden") is not None
    assert all(set(e.witnesses) <= {"player"} for e in evs)


def test_hidden_actor_loud_event_reveals(w: World) -> None:
    _hide(w)
    evs = w.fx([{"emit": {"actor": "player", "verb": "crash", "text": "⟦player⟧撞翻了兵器架", "loud": True}}])
    assert w.status("player", "hidden") is None
    assert evs[0].witnesses.get("fighter") == "saw"


def test_hidden_steal_fail_is_witnessed_success_is_not(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    from tianlong.kernel import resolve

    _hide(w)
    dice(1)
    evs = resolve.R(w.s, w.defs, _steal_action(w), w.c("player"), "player", None).r_steal()
    assert w.status("player", "hidden") is not None and set(evs[-1].witnesses) == {"player"}
    assert w.s.items["token"].holder == "player"  # 得手：没有人看见，人还藏着
    w.fx([{"give": ["player", "guest", "token"]}])
    _hide(w)
    t0 = _rel(w, "guest", "player").trust
    dice(100)
    evs = resolve.R(w.s, w.defs, _steal_action(w), w.c("player"), "player", None).r_steal()
    assert evs[-1].outcome == "fail" and evs[-1].witnesses.get("guest") == "saw"
    assert w.status("player", "hidden") is None  # 被当场抓住：藏不住了，苦主亲眼看见是谁
    assert _rel(w, "guest", "player").trust == t0 - 30


def _steal_action(w: World) -> actions.ActionInst:
    params = {"target": "guest", "item": "token"}
    return actions.ActionInst(
        actor="player",
        verb="steal",
        params=params,
        cost=1,
        desc="偷",
        signature=actions.signature("steal", params),
    )


# ---------------------------------------------------------------- 4. 没有救人就没有救命之恩


def _ta(w: World, a: str, b: str) -> tuple[int, int, int]:
    r = w.c(a).rel.get(b)
    owes = w.c(a).owes.get(b, 0)
    return (r.trust, r.affinity, owes) if r is not None else (0, 0, owes)


def test_captor_releasing_own_prisoner_earns_nothing(w: World) -> None:
    f = w.c("fighter")
    f.statuses.append(Status(kind="asleep", until=w.s.tick + 50))
    base = _ta(w, "fighter", "player")
    for _ in range(3):
        w.act("player", "restrain", target="fighter")
        evs = w.act("player", "release", target="fighter")
        assert not any(k.get("k") == "rescue" for e in evs for k in e.kinds)
    assert _ta(w, "fighter", "player") == base
    assert w.status("fighter", "bound") is None


def test_subordinate_or_superior_release_is_no_rescue(w: World) -> None:
    g = w.c("guest")
    base = _ta(w, "guest", "player")
    w.c("player").superior = "fighter"
    g.statuses.append(Status(kind="bound", source="fighter"))
    w.act("player", "release", target="guest")  # 手下放了头领拿的人
    assert _ta(w, "guest", "player") == base
    w.c("player").superior = None
    w.c("fighter").superior = "player"
    g.statuses.append(Status(kind="captive", source="fighter"))
    w.act("player", "release", target="guest")  # 头领放了手下拿的人
    assert _ta(w, "guest", "player") == base


def test_third_party_release_is_still_a_rescue(w: World) -> None:
    g = w.c("guest")
    t0, a0, o0 = _ta(w, "guest", "player")
    g.statuses.append(Status(kind="bound", source="fighter"))
    w.act("player", "release", target="guest")
    assert _ta(w, "guest", "player") == (t0 + 30, a0 + 25, o0 + 1)
    # 别人扣着的人，自己又加了一道绳子再放开：仍是一次救，而且只有一次
    g.statuses.append(Status(kind="captive", source="fighter"))
    g.statuses.append(Status(kind="bound", source="player"))
    w.act("player", "release", target="guest")
    assert _ta(w, "guest", "player") == (t0 + 60, a0 + 50, o0 + 2)
    assert not w.has("player", "release", target="guest")
    g.statuses.append(Status(kind="bound", source=None))  # 不知是谁捆的：算别人的
    w.act("player", "release", target="guest")
    assert _ta(w, "guest", "player")[2] == o0 + 3


def test_help_cannot_be_repeated_on_tended_target(w: World) -> None:
    w.fx([{"injure": ["guest", 3]}])
    t0, a0, o0 = _ta(w, "guest", "player")
    w.act("player", "help", target="guest")
    assert _ta(w, "guest", "player") == (t0 + 30, a0 + 25, o0 + 1)
    assert w.status("guest", "tended") is not None
    assert w.build("player", "help", target="guest") is None and not w.has("player", "help", target="guest")
    # 昏过去了还是可以唤醒
    w.c("guest").statuses.append(Status(kind="unconscious"))
    assert w.build("player", "help", target="guest") is not None


def test_use_cure_on_own_victim_is_no_rescue(w: World) -> None:
    w.fx([{"give": ["guest", "player", "antidote"]}])
    w.c("guest").statuses.append(Status(kind="poisoned_slow", until=w.s.tick + 100, source="player"))
    base = _ta(w, "guest", "player")
    evs = w.act("player", "use", item="antidote", target="guest")
    assert not any(k.get("k") == "rescue" for e in evs for k in e.kinds)
    assert _ta(w, "guest", "player")[2] == base[2]
    assert w.status("guest", "poisoned_slow") is None


def test_use_cure_on_others_victim_is_a_rescue(w: World) -> None:
    w.fx([{"give": ["guest", "player", "antidote"]}])
    w.c("fighter").statuses.append(Status(kind="poisoned_slow", until=w.s.tick + 100, source="guest"))
    o0 = _ta(w, "fighter", "player")[2]
    evs = w.act("player", "use", item="antidote", target="fighter")
    assert any(k.get("k") == "rescue" for e in evs for k in e.kinds)
    assert _ta(w, "fighter", "player")[2] == o0 + 1


# ---------------------------------------------------------------- 5. 判不出守没守的许诺


def test_promise_without_do_closes_silently_at_deadline(w: World) -> None:
    w.say("player", "promise", ["guest"], gist="保证不把这事说出去")
    (p,) = w.s.promises.values()
    assert p.do is None and p.status == "open" and p.deadline is not None
    t0 = _rel(w, "guest", "player").trust
    w.s.replan_queue.clear()
    timeflow.advance_time(w.s, w.defs, p.deadline - w.s.tick + 1)
    assert p.status != "open"
    assert _rel(w, "guest", "player").trust == t0
    assert not [m for m in w.c("guest").memory if "没有做到" in m.text]
    assert "guest" not in w.s.replan_queue


def test_promise_with_do_still_breaks_at_deadline(w: World) -> None:
    w.say("player", "promise", ["guest"], gist="我去前院", promise_do={"verb": "go", "exit": "yard"})
    (p,) = w.s.promises.values()
    t0 = _rel(w, "guest", "player").trust
    timeflow.advance_time(w.s, w.defs, p.deadline - w.s.tick + 1)
    assert p.status == "broken" and _rel(w, "guest", "player").trust == t0 - 20
    assert [m for m in w.c("guest").memory if "没有做到" in m.text]


# ---------------------------------------------------------------- 6. 侥幸不死，可再逃一次


def _lucky_kill(w: World, victim: str = "guest") -> None:
    w.s.flags["__lucky__"] = True
    w.act("fighter", "attack", target=victim, intent="kill")
    w.s.flags["__lucky__"] = False


def test_saved_survivor_may_flee_once(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    _lucky_kill(w)
    g = w.c("guest")
    assert actions.injury_level(g) == 3 and w.status("guest", "reprieve") is not None
    assert w.has("guest", "flee", exit="yard")
    assert not w.has("guest", "go", exit="yard") and not w.has("guest", "follow", target="player")
    assert checks.flee_chance(w.s, w.defs, g, [w.c("fighter")]) < 50  # 重伤的减值照算
    dice(100)
    w.act("guest", "flee", exit="yard")
    assert g.loc == "hall" and w.status("guest", "reprieve") is None
    assert not w.has("guest", "flee", exit="yard")  # 只这一次


def test_saved_survivor_flee_success_and_expiry(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    _lucky_kill(w)
    dice(1)
    w.act("guest", "flee", exit="yard")
    assert w.c("guest").loc == "yard" and w.status("guest", "reprieve") is None
    w2 = World()
    _lucky_kill(w2)
    timeflow.advance_time(w2.s, w2.defs, 3)
    assert w2.status("guest", "reprieve") is None and not w2.has("guest", "flee", exit="yard")


def test_saved_player_may_flee_once(w: World) -> None:
    _lucky_kill(w, "player")
    assert actions.injury_level(w.c("player")) == 3
    assert w.has("player", "flee", exit="yard") and not w.has("player", "go", exit="yard")


def test_saved_attacker_keeps_reprieve_after_his_own_action(w: World) -> None:
    """输的是出手的人（D-047：守方想取他的命）：这一下动作收尾时不能把刚得的机会抹掉。"""
    f = w.c("fighter")
    f.base_power = 6
    f.agenda = Agenda(
        goal="k", steps=[Step(id="k1", text="k", do={"verb": "attack", "target": "girl", "intent": "kill"})]
    )
    w.s.flags["__lucky__"] = True
    evs = w.act("girl", "attack", target="fighter")
    w.s.flags["__lucky__"] = False
    assert evs[-1].params["outcome"] == "saved" and evs[-1].params["winner"] == "fighter"
    assert actions.injury_level(w.c("girl")) == 3 and w.status("girl", "reprieve") is not None
    assert w.status("girl", "alerted") is None  # 示警照旧在自己这一步收尾时用掉
    assert w.has("girl", "flee", exit="yard")
