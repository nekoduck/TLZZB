"""8.3：前提不成立的一步作废，当事人在这个时辰里重新打算；新打算只出自规则生成的候选。"""

from __future__ import annotations

from typing import Any

import pytest

from tianlong.kernel import timeflow
from tianlong.llm import JevError, LLMUnavailable, make_llm
from tianlong.llm.jev import ChoiceAnswer, NoulAnswer
from tianlong.llm.schemas import ReplanOut
from tianlong.world import clock, goals, replan
from tianlong.world.common import REPLAN_FLAG

from .conftest import fx


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[goals.Candidate]]:
    """记下 process_queue 里给每个人生成的候选。"""
    seen: dict[str, list[goals.Candidate]] = {}
    real = goals.candidates

    def spy(state: Any, defs: Any, cid: str, **kw: Any) -> list[goals.Candidate]:
        out = real(state, defs, cid, **kw)
        seen[cid] = out
        return out

    monkeypatch.setattr(goals, "candidates", spy)
    return seen


def _bad_step(cid: str, at: str) -> dict:
    return {"step": [cid, {"id": "t_doomed", "text": "去做一件做不成的事", "at": at, "do": {"verb": "wait"}, "pre": {"flag": "never_set"}, "takes": 1}]}


async def test_void_step_replans_within_the_hour(llm, world, recorded):
    defs, state = world
    mw = state.chars["mu_wanqing"]  # 在她的住处，离场
    fx(state, defs, [_bad_step("mu_wanqing", "mu_house")])
    assert mw.agenda.steps[mw.agenda.cursor].id == "t_doomed"
    timeflow.advance_time(state, defs, 8)
    await clock.tick(llm, state, defs)
    assert "mu_wanqing" in recorded
    cands = recorded["mu_wanqing"]
    assert not any(c.keep for c in cands)  # 一步作废触发的重新打算，不给「照旧」
    assert mw.agenda.status == "active"
    assert mw.agenda.steps and all(s.origin == "replan" for s in mw.agenda.steps)
    assert mw.agenda.goal in {c.goal for c in cands}
    assert "mu_wanqing" not in state.replan_queue
    assert state.flags[REPLAN_FLAG]["mu_wanqing"][0] == state.tick // 8


async def test_replan_never_yields_a_goal_outside_the_candidates(llm, world, recorded):
    defs, state = world
    cids = [c.id for c in state.chars.values() if c.tier in ("soul", "named") and c.onstage and c.id != state.player]
    fx(state, defs, [{"agenda": [cid, None]} for cid in cids])
    replan.queue(state, defs, cids)
    await replan.process_queue(llm, state, defs)
    for cid in cids:
        ag = state.chars[cid].agenda
        assert ag is not None, cid
        assert ag.goal in {c.goal for c in recorded[cid]}, cid
        if ag.status == "active":
            assert all(s.origin == "replan" for s in ag.steps)


async def test_bogus_j3_answer_falls_back_to_rules(llm, world, recorded, monkeypatch):
    defs, state = world

    async def bogus(kind: str, text: Any, questions: Any, **kw: Any) -> dict:
        return {
            "goal": ChoiceAnswer({"type": "choice", "choice": "zzz", "confidence": 1.0, "probabilities": {"zzz": 1.0}}),
            "needs_thought": NoulAnswer({"type": "noul", "noul": 0.0}),
        }

    monkeypatch.setattr(llm.jev, "ask", bogus)
    fx(state, defs, [{"agenda": ["zuo_zimu", None]}])
    replan.queue(state, defs, ["zuo_zimu"])
    await replan.process_queue(llm, state, defs)
    assert state.chars["zuo_zimu"].agenda.goal == recorded["zuo_zimu"][0].goal


async def test_bogus_c5_answer_falls_back_to_rules(settings, tmp_path, world, recorded, monkeypatch):
    defs, state = world
    s2 = settings.model_copy(deep=True)
    s2.route.replan = "claude_only"
    llm = make_llm(s2, mock=True, run_dir=tmp_path / "r2")

    async def bogus(*a: Any, **kw: Any) -> tuple:
        return ReplanOut(goal="G99", steps=["G99.x.1"], inner="……"), None

    monkeypatch.setattr(llm.claude, "call_json", bogus)
    fx(state, defs, [{"agenda": ["xin_shuangqing", None]}])
    replan.queue(state, defs, ["xin_shuangqing"])
    await replan.process_queue(llm, state, defs)
    assert state.chars["xin_shuangqing"].agenda.goal == recorded["xin_shuangqing"][0].goal


async def test_llm_errors_fall_back(llm, world, recorded, monkeypatch):
    defs, state = world

    async def broken_jev(*a: Any, **kw: Any) -> dict:
        raise JevError("down")

    async def broken_claude(*a: Any, **kw: Any) -> tuple:
        raise LLMUnavailable("no key")

    monkeypatch.setattr(llm.jev, "ask", broken_jev)
    monkeypatch.setattr(llm.claude, "call_json", broken_claude)
    fx(state, defs, [{"agenda": ["rong_ziju", None]}])
    replan.queue(state, defs, ["rong_ziju"])
    await replan.process_queue(llm, state, defs)
    assert state.chars["rong_ziju"].agenda.goal == recorded["rong_ziju"][0].goal


async def test_jev_error_escalates_to_c5(llm, world, recorded, monkeypatch):
    defs, state = world
    calls: list[str] = []

    async def broken_jev(*a: Any, **kw: Any) -> dict:
        raise JevError("down")

    real = llm.claude.call_json

    async def spy(kind: str, *a: Any, **kw: Any) -> Any:
        calls.append(kind)
        return await real(kind, *a, **kw)

    monkeypatch.setattr(llm.jev, "ask", broken_jev)
    monkeypatch.setattr(llm.claude, "call_json", spy)
    fx(state, defs, [{"agenda": ["gong_guangjie", None]}])
    replan.queue(state, defs, ["gong_guangjie"])
    await replan.process_queue(llm, state, defs)
    assert calls == ["C5"]
    ag = state.chars["gong_guangjie"].agenda
    assert ag.goal in {c.goal for c in recorded["gong_guangjie"]}


async def test_rules_only_makes_no_model_calls(llm, world):
    defs, state = world
    fx(state, defs, [{"agenda": ["zuo_zimu", None]}])
    replan.queue(state, defs, ["zuo_zimu"])
    before = llm.recorder.count
    await replan.process_queue(llm, state, defs, rules_only=True)
    assert llm.recorder.count == before
    assert state.chars["zuo_zimu"].agenda is not None


async def test_keep_current_agenda_when_an_event_triggers(llm, world, recorded):
    defs, state = world
    dy = state.chars["duan_yu"]
    goal, steps = dy.agenda.goal, [s.id for s in dy.agenda.steps]
    replan.queue(state, defs, ["duan_yu"])  # 出了事（不是一步作废）：当前一步的前提仍成立
    await replan.process_queue(llm, state, defs)
    cands = recorded["duan_yu"]
    assert cands[0].keep and cands[0].id == goals.KEEP_ID
    # J3 的选项里，照旧那一项带「（你本来要做的）」
    j3 = [r for r in llm.recorder.records if r["kind"] == "J3"][-1]
    crit = j3["request"]["questions"]["goal"]["criteria"]
    assert crit[goals.KEEP_ID].startswith("（你本来要做的）")
    assert dy.agenda.goal == goal and [s.id for s in dy.agenda.steps] == steps


async def test_keep_offered_for_a_step_not_started_yet(llm, world):
    """前提写的是动手那一刻的条件：还没开始做的一步，前提此刻不成立也照旧（例如「服了毒，往渡口赶」）。"""
    defs, state = world
    dy = state.chars["duan_yu"]
    fx(state, defs, [{"step_state": ["duan_yu", "dy_watch_bout", "done"]}, {"step_state": ["duan_yu", "dy_plead_shennong", "done"]}])
    state.replan_queue.clear()
    st = dy.agenda.steps[dy.agenda.cursor]
    assert st.id == "dy_leave_camp"
    assert goals.keep_allowed(state, defs, dy)
    fx(state, defs, [{"progress": ["duan_yu", 3]}, {"move": ["duan_yu", st.at]}])
    assert not goals.keep_allowed(state, defs, dy)  # 已经在做了，前提却不成立：不再照旧


async def test_no_keep_after_a_void(llm, world, recorded):
    defs, state = world
    dy = state.chars["duan_yu"]
    fx(state, defs, [{"step_state": ["duan_yu", "dy_watch_bout", "void"]}])
    assert replan.classify_trigger(state, dy) == "void"
    await replan.process_queue(llm, state, defs)
    assert not any(c.keep for c in recorded["duan_yu"])
    assert all(s.origin == "replan" for s in dy.agenda.steps)


async def test_waiting_when_no_rule_covers(llm, world):
    defs, state = world
    empty = defs.model_copy(update={"goals": {}})
    fx(state, defs, [{"agenda": ["rong_ziju", None]}])
    replan.queue(state, empty, ["rong_ziju"])
    await replan.process_queue(llm, state, empty)
    ag = state.chars["rong_ziju"].agenda
    assert ag.status == "waiting" and ag.waiting_for
    from tianlong.kernel import invariants

    assert "rong_ziju" not in invariants.liveness(state, defs)


async def test_at_most_once_per_hour(llm, world):
    defs, state = world
    fx(state, defs, [{"agenda": ["rong_ziju", None]}])
    replan.queue(state, defs, ["rong_ziju"])
    await replan.process_queue(llm, state, defs)
    first = state.chars["rong_ziju"].agenda.model_dump()
    replan.queue(state, defs, ["rong_ziju"])
    await replan.process_queue(llm, state, defs)
    assert state.chars["rong_ziju"].agenda.model_dump() == first
    assert "rong_ziju" in state.replan_queue  # 留到下个时辰
    fx(state, defs, [])
    timeflow.advance_time(state, defs, 8)
    await replan.process_queue(llm, state, defs)
    assert "rong_ziju" not in state.replan_queue


async def test_candidates_cover_the_rule_table(world):
    """规则表的每一行在合适的处境下都生得出候选。"""
    defs, state = world

    def rules_for(cid: str) -> set[str]:
        return {c.rule for c in goals.candidates(state, defs, cid)}

    # 任何 × 无事
    assert {"idle", "wait"} <= rules_for("rong_ziju")
    # survive：中毒 → 求解药（知道解药在谁身上）、找下毒的人、找懂药的人；被擒 → 脱身、求情
    fx(state, defs, [{"add_status": ["duan_yu", "poisoned_duanchang", {"ticks": 600, "source": "sikong_xuan"}]}, {"learn": ["duan_yu", "F_duanchang_antidote_sikong", 1.0, "told"]}])
    assert {"survive_ask_holder", "survive_poisoner", "survive_healer"} <= rules_for("duan_yu")
    fx(state, defs, [{"add_status": ["zhong_ling", "captive", {"source": "sikong_xuan"}]}])
    assert {"survive_escape", "survive_plead"} <= rules_for("zhong_ling")
    # protect：朋友被擒 → 救人、拿东西去换（钟万仇知道解药在药柜里）
    fx(state, defs, [{"add_status": ["sikong_xuan", "poisoned_diao", {"ticks": 600}]}])
    zw = rules_for("zhong_wanchou")
    assert "protect_free" in zw
    assert "protect_ransom" in zw
    # clan_honor：山被围
    fx(state, defs, [{"set_flag": ["siege_on", True]}])
    assert "honor_hold_sect" in rules_for("zuo_zimu")
    # obey：主上在别处
    assert "obey_greet" in rules_for("sn_chief_b")
    # grudge：仇人在图内
    fx(state, defs, [{"enter": ["rui_popo", "highroad"]}])
    assert "grudge_hunt" in rules_for("rui_popo") or "grudge_ambush" in rules_for("rui_popo")
    # curiosity / greed：听说有宝物
    fx(state, defs, [{"learn": ["ma_wude", "F_zhong_ling_birth", 1.0, "told"]}])
    assert "seek_treasure" in rules_for("ma_wude") or "ask_around" in rules_for("ma_wude")
    # get_stronger：手里有秘籍
    fx(state, defs, [{"spawn": ["silk_scroll", "gong_guangjie"]}])
    assert "study_manual" in rules_for("gong_guangjie")
    # 候选至多 12 个，含照旧过日子与等一等再说
    for cid in ("duan_yu", "zhong_wanchou", "zuo_zimu"):
        cs = goals.candidates(state, defs, cid, allow_keep=True)
        assert len(cs) <= goals.MAX_CANDIDATES
        assert {"idle", "wait"} <= {c.rule for c in cs}


def test_every_drive_in_content_has_rules(world):
    defs, state = world
    kinds = {r["drive"] if isinstance(r["drive"], str) else k for r in goals.rules(defs) for k in ([r["drive"]] if isinstance(r["drive"], str) else r["drive"])}
    used = {goals.drive_kind(d)[0] for c in state.chars.values() for d in c.drives}
    assert used <= kinds, used - kinds
    table_rows = {"protect", "survive", "clan_honor", "obey", "curiosity", "greed", "grudge", "get_stronger", "any"}
    assert table_rows <= kinds


async def test_refused_request_is_not_repeated(llm, world):
    defs, state = world
    fx(state, defs, [{"add_status": ["duan_yu", "poisoned_duanchang", {"ticks": 600, "source": "sikong_xuan"}]}, {"learn": ["duan_yu", "F_duanchang_antidote_sikong", 1.0, "told"]}, {"move": ["duan_yu", "shennong_camp"]}])
    rules = {c.rule for c in goals.candidates(state, defs, "duan_yu")}
    assert "survive_ask_holder" in rules
    from tianlong.kernel.state import Request

    state.requests["R900"] = Request(
        id="R900", frm="duan_yu", to="sikong_xuan", action={"verb": "give", "params": {"item": "duanchang_antidote", "target": "duan_yu"}},
        action_class="costly", status="refused", created=state.tick,
    )
    rules = {c.rule for c in goals.candidates(state, defs, "duan_yu")}
    assert "survive_ask_holder" not in rules
