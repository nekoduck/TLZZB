"""方案 6.12 的成长与 6.9 的状态与时间。"""

from __future__ import annotations

from tianlong.kernel import growth, timeflow

from .conftest import World


def test_learnable_from_manual(w: World) -> None:
    w.fx([{"spawn": ["old_manual", "player"]}])
    evs = w.act("player", "study", item="old_manual", art="cloud_steps", hours=2)
    assert w.c("player").arts["cloud_steps"] == 1
    assert "有了长进" in evs[0].fact_text
    assert "cloud_steps" in w.s.known_terms


def test_requires_blocks_progress_with_reason(w: World) -> None:
    w.c("player").literacy = 0
    w.fx([{"spawn": ["old_manual", "player"]}])
    a = w.build("player", "study", item="old_manual", art="cloud_steps", hours=4)
    assert a is not None  # study 可以做
    evs = w.act("player", "study", item="old_manual", art="cloud_steps", hours=4)
    assert evs[0].outcome == "fail" and "不认得" in evs[0].fact_text
    assert w.c("player").art_progress.get("cloud_steps", 0) == 0 and "cloud_steps" not in w.c("player").arts


def test_teacher_speeds_up(w: World) -> None:
    c = w.c("player")
    assert growth.progress_for(c, 1, teacher=False) == 10
    assert growth.progress_for(c, 1, teacher=True) == 15
    c.aptitude = 3
    assert growth.progress_for(c, 2, teacher=False) == 28  # 8 × 1.75 × 2


def test_study_with_teacher_after_agreed_request(w: World) -> None:
    w.fx([{"role": ["player", "hall_sect", "add"]}])  # 成了同门：剑法降为「费事」
    evs = w.say(
        "player",
        "request",
        ["fighter"],
        ask_action={"verb": "teach", "params": {"art": "hall_sword", "target": "player"}},
    )
    r = w.s.requests[evs[0].speech.request]
    assert r.action_class == "effort" and r.verdict == "conditional"  # 同门至少相熟：50
    r.verdict = "may"
    w.say("fighter", "agree", ["player"], request=r.id)
    assert w.build("player", "study", target="fighter", art="hall_sword", hours=2) is not None
    w.act("player", "study", target="fighter", art="hall_sword", hours=1)
    assert w.c("player").art_progress["hall_sword"] == 15
    assert w.c("player").rel["fighter"].trust == 15  # 得人传授
    w.act("player", "study", target="fighter", art="hall_sword", hours=1)
    assert w.c("player").arts["hall_sword"] == 1
    assert growth.power(w.s, w.defs, "player") == 2


def test_sect_art_for_outsider_is_betrayal(w: World) -> None:
    evs = w.say(
        "player",
        "request",
        ["fighter"],
        ask_action={"verb": "teach", "params": {"art": "hall_sword", "target": "player"}},
    )
    assert w.s.requests[evs[0].speech.request].action_class == "betray"


def test_literacy_by_teaching(w: World) -> None:
    w.c("player").superior = "guest"
    w.act("guest", "teach", target="player", art="literacy", hours=12)
    w.act("guest", "teach", target="player", art="literacy", hours=1)
    assert w.c("player").literacy == 2  # 24 个时辰的功夫（有人教 ×1.5）


def test_neili_band_and_power() -> None:
    assert [growth.neili_band(n) for n in (0, 19, 20, 39, 40, 60, 80, 100)] == [0, 0, 1, 1, 2, 3, 4, 4]


def test_power_formula(w: World) -> None:
    p = w.c("player")
    assert growth.power(w.s, w.defs, p) == 0
    p.neili = 60  # 内力档 3，但一门 combat 都不会：减 1
    assert growth.power(w.s, w.defs, p) == 2
    p.arts["hall_sword"] = 1  # 入门 power 2；内力档不再减
    assert growth.power(w.s, w.defs, p) == 3
    p.arts["hall_sword"] = 2
    p.neili = 0
    assert growth.power(w.s, w.defs, p) == 3
    p.base_power = 5
    assert growth.power(w.s, w.defs, p) == 5
    # 内功升层加内力
    q = w.c("girl")
    w.fx([{"train": ["girl", "breath", 16]}])
    assert q.arts["breath"] == 1 and q.neili == 25 and growth.power(w.s, w.defs, q) == 1


def test_flee_bonus_evade_absorb(w: World) -> None:
    p = w.c("player")
    assert growth.flee_bonus(w.defs, p) == 0 and not growth.has_evade(w.defs, p)
    p.arts["cloud_steps"] = 2
    assert growth.flee_bonus(w.defs, p) == 40 and growth.has_evade(w.defs, p)
    assert growth.absorb_amount(w.defs, p) == 0


def test_manual_changing_hands_changes_who_can_learn(w: World) -> None:
    w.fx([{"spawn": ["old_manual", "player"]}])
    assert w.build("player", "study", item="old_manual", art="cloud_steps", hours=1) is not None
    assert w.build("guest", "study", item="old_manual", art="cloud_steps", hours=1) is None
    w.act("player", "give", item="old_manual", target="guest")
    assert w.build("player", "study", item="old_manual", art="cloud_steps", hours=1) is None
    w.act("guest", "study", item="old_manual", art="cloud_steps", hours=2)
    assert w.c("guest").arts["cloud_steps"] == 1 and "cloud_steps" not in w.c("player").arts


def test_first_stage_only_by_study(w: World) -> None:
    c = w.c("player")
    r = growth.study_amount(w.s, w.defs, c, "cloud_steps", 1, teacher=False)
    assert r.added == 10 and r.blocked is None
    c.arts["cloud_steps"] = 1
    r = growth.study_amount(w.s, w.defs, c, "cloud_steps", 1, teacher=False)
    assert r.added == 0 and r.blocked
    assert growth.practice_amount(w.s, w.defs, w.c("guest"), "cloud_steps", 1).blocked  # 没入门练不了


# ---------------------------------------------------------------- 6.9


def test_clock_text_and_time_of_day() -> None:
    assert timeflow.clock_text(32) == "第一日 辰时"
    assert timeflow.clock_text(0) == "第一日 子时"
    assert timeflow.clock_text(96 * 10 + 8 * 11) == "第十一日 亥时"
    assert timeflow.clock_text(96 * 20) == "第二十一日 子时"
    assert (
        timeflow.time_of_day(32) == "day"
        and timeflow.time_of_day(72) == "dusk"
        and timeflow.time_of_day(0) == "night"
    )
    assert timeflow.cn_num(672) == "六百七十二" and timeflow.cn_num(105) == "一百零五"


def test_poison_symptom_then_death(w: World) -> None:
    w.fx([{"add_status": ["guest", "poisoned_slow", {"ticks": 100, "source": "fighter"}]}])
    evs = timeflow.advance_time(w.s, w.defs, 3)
    assert not evs
    evs = timeflow.advance_time(w.s, w.defs, 1)  # 到期前一日
    assert [e.verb for e in evs] == ["symptom"]
    evs = timeflow.advance_time(w.s, w.defs, 96)
    assert w.status("guest", "dead") is not None
    assert any(e.verb == "die" for e in evs)
    assert w.status("guest", "dead").source == "fighter"


def test_poison_kills_player_without_buffer(w: World) -> None:
    w.fx([{"add_status": ["player", "poisoned_slow", {"ticks": 2}]}])
    timeflow.advance_time(w.s, w.defs, 2)
    assert w.status("player", "dead") is not None and w.s.ended == "dead"


def test_status_expiry_hunger_exhaustion(w: World) -> None:
    w.fx([{"add_status": ["player", "tended", {"ticks": 3}]}])
    timeflow.advance_time(w.s, w.defs, 3)
    assert w.status("player", "tended") is None
    timeflow.advance_time(w.s, w.defs, 61)
    assert w.status("player", "exhausted") is not None  # 连续清醒满 64 刻
    assert w.status("player", "hungry") is None
    timeflow.advance_time(w.s, w.defs, 32)
    assert w.status("player", "hungry") is not None  # 满 96 刻没吃东西
    assert w.status("guest", "hungry") is None  # 只算玩家
    w.act("player", "rest", hours=2)
    assert w.status("player", "exhausted") is None
    timeflow.advance_time(w.s, w.defs, 16)
    assert w.status("player", "asleep") is None and w.c("player").awake_since == w.s.tick


def test_mood_decays_ten_percent_per_hour(w: World) -> None:
    w.c("guest").mood["anger"] = 50
    timeflow.advance_time(w.s, w.defs, 7)
    assert w.c("guest").mood["anger"] == 50
    timeflow.advance_time(w.s, w.defs, 1)
    assert w.c("guest").mood["anger"] == 45
    timeflow.advance_time(w.s, w.defs, 16)
    assert w.c("guest").mood["anger"] == 36


def test_scheduled_from_opening(w: World) -> None:
    timeflow.advance_time(w.s, w.defs, 8)
    assert w.s.flags.get("bell_rang") is True and w.s.scheduled == []


# ---- defer_train：进度留给调用方按过去的时间记（D-360）


def _deferred(w: World, actor: str, verb: str, **params):  # noqa: ANN003, ANN202
    from tianlong.kernel import resolve

    a = w.build(actor, verb, **params)
    assert a is not None
    return resolve.apply(w.s, w.defs, a, None, source="player", defer_train=True)


def test_defer_train_study_records_nothing_up_front(w: World) -> None:
    w.fx([{"spawn": ["old_manual", "player"]}])
    c = w.c("player")
    evs = _deferred(w, "player", "study", item="old_manual", art="cloud_steps", hours=2)
    assert len(evs) == 1 and evs[0].outcome == "ok" and "长进" not in evs[0].fact_text
    assert "cloud_steps" not in c.arts and c.art_progress.get("cloud_steps", 0) == 0
    p = evs[0].params
    assert p["deferred"] is True and p["progress"] == 0 and p["level"] == 0
    assert (p["student"], p["art"], p["max_level"]) == ("player", "cloud_steps", 1)
    assert p["per_hour"] == growth.progress_for(c, 1, teacher=False) == 10
    assert p["hours"] == 2 and p["item"] == "old_manual"  # 动作自己的参数照旧在
    assert "cloud_steps" in w.s.known_terms  # 翻开书就知道它叫什么：这一条不等
    # 调用方照这几个数一点点记，记满时与一次记满的结果相同
    w.fx([{"train": [p["student"], p["art"], p["per_hour"] * 2, p["max_level"]]}])
    assert c.arts["cloud_steps"] == 1


def test_defer_train_practice_and_teach(w: World) -> None:
    c = w.c("player")
    w.fx([{"art": ["player", "cloud_steps", 1]}])
    evs = _deferred(w, "player", "practice", art="cloud_steps", hours=3)
    p = evs[0].params
    assert evs[0].outcome == "ok" and "长进" not in evs[0].fact_text
    assert c.art_progress.get("cloud_steps", 0) == 0 and c.arts["cloud_steps"] == 1
    assert p["deferred"] is True and (p["student"], p["art"], p["max_level"]) == ("player", "cloud_steps", None)
    assert p["per_hour"] == 10 and p["level"] == 1

    w.fx([{"superior": ["fighter", "player"]}])
    f = w.c("fighter")
    f.literacy = 0
    evs = _deferred(w, "player", "teach", target="fighter", art="literacy", hours=2)
    p = evs[0].params
    assert evs[0].outcome == "ok" and "长进" not in evs[0].fact_text
    assert f.literacy == 0 and f.art_progress.get("literacy", 0) == 0
    assert p["deferred"] is True and (p["student"], p["art"], p["max_level"]) == ("fighter", "literacy", None)
    assert p["per_hour"] == growth.progress_for(f, 1, teacher=True)
    assert any(k.get("k") == "taught" and k.get("who") == "fighter" for k in evs[0].kinds)  # 得人传授照记


def test_defer_train_blocked_study_is_unchanged(w: World) -> None:
    w.c("player").literacy = 0
    w.fx([{"spawn": ["old_manual", "player"]}])
    evs = _deferred(w, "player", "study", item="old_manual", art="cloud_steps", hours=4)
    assert evs[0].outcome == "fail" and "不认得" in evs[0].fact_text and "deferred" not in evs[0].params


def test_without_defer_train_progress_is_recorded_at_once(w: World) -> None:
    """角色自己的、离场的结算不传 defer_train：照旧在动手那一刻按吩咐的时辰数记满。"""
    w.fx([{"art": ["guest", "cloud_steps", 1]}])
    g = w.c("guest")
    evs = w.act("guest", "practice", art="cloud_steps", hours=2)
    assert evs[0].params["progress"] == growth.progress_for(g, 2, teacher=False) > 0
    assert "deferred" not in evs[0].params
    assert g.art_progress.get("cloud_steps", 0) == evs[0].params["progress"] or g.arts["cloud_steps"] == 2
