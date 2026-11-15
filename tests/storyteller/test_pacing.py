"""节奏与机会牌（方案 8.5）。"""

from __future__ import annotations

from typing import Any

import pytest

from tianlong.kernel import timeflow
from tianlong.llm import JevError
from tianlong.storyteller import opportunities, pacing
from tianlong.storyteller.pacing import pace, record_turn

from .conftest import calls

SEVEN = {"mountain_rain", "patrol", "herb_gatherer", "master_message", "rumor", "windfall", "hunger_fatigue"}


def _stall(w, turns: int = 7) -> None:
    for _ in range(turns):
        record_turn(w.s, w.defs, [])


def test_seed_cards_present(w):
    assert set(opportunities.cards(w.defs)) == SEVEN
    for card in opportunities.cards(w.defs).values():
        assert card.get("text") and "cooldown" in card


def test_tension_reading_and_window(w):
    assert pacing.tension_now(w.s, w.defs) == 0
    w.open_bout()
    assert pacing.tension_now(w.s, w.defs) >= 10
    w.fx([{"add_status": ["player", "poisoned_duanchang", {"ticks": 100}]}])
    from tianlong.storyteller import threads

    threads.update(w.s, w.defs, [])
    reading = pacing.tension_now(w.s, w.defs)
    assert reading >= 30
    for _ in range(8):
        record_turn(w.s, w.defs, [])
    assert len(w.s.storyteller["tension_hist"]) == pacing.WINDOW
    assert pacing.tension(w.s) == pytest.approx(reading)


def test_stall_detection(w):
    record_turn(w.s, w.defs, [])
    assert w.s.storyteller["quiet"] == 0
    _stall(w, 6)
    assert w.s.storyteller["quiet"] == 6 and pacing.stalled(w.s)
    # 获知新事实就不算停滞
    w.fx([{"learn": ["player", "F_shennong_siege", 1.0, "told:ma_wude"]}])
    record_turn(w.s, w.defs, [])
    assert w.s.storyteller["quiet"] == 0 and not pacing.stalled(w.s)


async def test_no_card_without_stall(llm, w):
    record_turn(w.s, w.defs, [])
    assert await pace(llm, w.s, w.defs) == []
    assert not calls(llm, "J6")


async def test_stall_fires_first_eligible_card(llm, w):
    _stall(w)
    elig = opportunities.eligible(w.s, w.defs)
    assert elig
    first = elig[0].card_id
    evs = await pace(llm, w.s, w.defs)
    assert evs and all(e.source == "storyteller" for e in evs)
    assert w.s.storyteller["last_card"] == first
    assert w.s.storyteller["cards_today"] == 1 and w.s.storyteller["quiet"] == 0
    j6 = calls(llm, "J6")
    assert len(j6) == 1
    crit = j6[0]["request"]["questions"]["card"]["criteria"]
    assert list(crit)[-1] == pacing.NONE and first in crit
    assert not w.s.violations


async def test_cards_per_day_limit(llm, w):
    fired = []
    for _ in range(3):
        _stall(w)
        evs = await pace(llm, w.s, w.defs)
        if evs:
            fired.append(w.s.storyteller["last_card"])
    assert len(fired) == llm.settings.limits.cards_per_day == 2
    assert opportunities.eligible(w.s, w.defs)  # 还有牌，只是今天出够了
    assert len(set(fired)) == 2  # 冷却：同一张不连着出
    # 第二天又可以出
    timeflow.advance_time(w.s, w.defs, 96)
    _stall(w)
    assert await pace(llm, w.s, w.defs)


async def test_no_card_when_tension_high(llm, w):
    _stall(w)
    w.fx([{"teller": ["tension", 80]}, {"teller": ["quiet", 20]}])
    assert await pace(llm, w.s, w.defs) == []
    assert not calls(llm, "J6")


async def test_j6_failure_means_no_card(llm, w, monkeypatch):
    async def boom(*args: Any, **kwargs: Any):
        raise JevError("down")

    monkeypatch.setattr(llm.jev, "ask", boom)
    _stall(w)
    assert await pace(llm, w.s, w.defs) == []
    assert "last_card" not in w.s.storyteller


async def test_j6_may_choose_none(llm, w, monkeypatch):
    real = llm.jev.ask

    async def none_please(kind, state, questions, *, hints=None, escalated=False):
        return await real(kind, state, questions, hints={"card": pacing.NONE}, escalated=escalated)

    monkeypatch.setattr(llm.jev, "ask", none_please)
    _stall(w)
    assert await pace(llm, w.s, w.defs) == []


async def test_rules_only_route_picks_by_weight(llm, w):
    llm.settings.route.cards = "rules_only"
    _stall(w)
    evs = await pace(llm, w.s, w.defs)
    assert evs and w.s.storyteller["last_card"] in SEVEN
    assert not calls(llm, "J6")


def _fire(w, cid: str):
    b = next((x for x in opportunities.eligible(w.s, w.defs) if x.card_id == cid), None)
    assert b is not None, (cid, [x.card_id for x in opportunities.eligible(w.s, w.defs)])
    ok, evs = opportunities.fire(w.s, w.defs, b)
    assert ok and evs, w.s.violations
    return b, evs


def test_rain_brings_people_from_nearby(w):
    w.fx([{"move": ["player", "mountain_path"]}])
    b, evs = _fire(w, "mountain_rain")
    assert w.s.chars[b.roles["first"]].loc == "mountain_path"
    assert any(e.verb == "rain" for e in evs)
    assert any(e.witnesses.get("player") == "saw" for e in evs)


def test_rain_not_indoors(w):
    assert "mountain_rain" not in {b.card_id for b in opportunities.eligible(w.s, w.defs)}


def test_patrol_comes_close(w):
    w.fx([{"move": ["player", "mountain_path"]}])
    w.fx([{"move": ["sn_guard_3", "shennong_camp"]}, {"move": ["sn_guard_4", "shennong_camp"]}])
    b, _ = _fire(w, "patrol")
    scout = w.s.chars[b.roles["scout"]]
    assert scout.loc == "mountain_path"
    if "scout2" in b.roles:
        assert set(w.s.chars[b.roles["scout2"]].roles) & set(scout.roles)


def test_herb_gatherer_meets_player(w):
    w.fx([{"move": ["player", "ferry_bridge"]}])
    _fire(w, "herb_gatherer")
    hg = w.s.chars["herb_gatherer_1"]
    assert hg.loc == "ferry_bridge" and "F_route_wanjie" in hg.beliefs


def test_master_message_only_while_master_alive_on_mountain(w):
    ids = lambda: {b.card_id for b in opportunities.eligible(w.s, w.defs)}  # noqa: E731
    assert "master_message" not in ids()  # 就在东家身边
    w.fx([{"move": ["player", "jianhu_gate"]}])
    assert "master_message" in ids()
    b, evs = _fire(w, "master_message")
    assert w.s.chars[b.roles["messenger"]].loc == "jianhu_gate"
    assert not w.s.threads["card:master_call"].closed
    assert "jianhu_hall" in w.s.threads["card:master_call"].refs
    w.fx([{"kill": "ma_wude"}])
    w.s.storyteller.pop("card_last", None)
    assert "master_message" not in ids()


def test_master_message_not_when_master_left_mountain(w):
    w.fx([{"move": ["player", "jianhu_gate"]}, {"move": ["ma_wude", "ferry_bridge"]}])
    assert "master_message" not in {b.card_id for b in opportunities.eligible(w.s, w.defs)}


def test_rumor_teaches_an_unwitnessed_event(w):
    # 山下出了事，玩家不在场；后来有个见过的人来到身边
    w.fx([{"injure": ["sn_guard_1", 2, "sikong_xuan"]}, {"emit": {"actor": "sikong_xuan", "verb": "beat",
          "text": "⟦sikong_xuan⟧把⟦sn_guard_1⟧打伤了", "kinds": [{"k": "harmed", "who": "sn_guard_1"}]}}])
    fid = next(f for f in w.s.facts if f.startswith("E"))
    w.fx([{"move": ["sn_guard_2", "jianhu_hall"]}])
    b, evs = _fire(w, "rumor")
    assert b.roles["news"] == fid and b.roles["teller"] == "sn_guard_2"
    assert w.s.chars["player"].beliefs[fid].source == "told:sn_guard_2"
    assert "⟦sikong_xuan⟧把⟦sn_guard_1⟧打伤了" in evs[0].fact_text


def test_windfall_each_item_once(w):
    got = set()
    for _ in range(3):
        w.s.storyteller.pop("card_last", None)
        _fire(w, "windfall")
        got |= {i for i in ("chopper", "extra_rations", "primer") if w.s.items[i].in_world}
    assert got == {"chopper", "extra_rations", "primer"}
    for i in got:
        assert w.s.items[i].loc == "jianhu_hall" and w.s.items[i].holder is None
    w.s.storyteller.pop("card_last", None)
    assert "windfall" not in {b.card_id for b in opportunities.eligible(w.s, w.defs)}


def test_hunger_card_deepens_the_body(w):
    _fire(w, "hunger_fatigue")
    p = w.s.chars["player"]
    assert {s.kind for s in p.statuses} & {"hungry", "exhausted"}
