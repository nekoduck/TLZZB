"""抉择：pick_reactors、decide_one / decide_all、order、退让与校验（方案 4.3、7.3、7.6）。"""

from __future__ import annotations

from typing import Any

import pytest

from tianlong import minds
from tianlong.kernel import actions, resolve
from tianlong.kernel.state import Speech
from tianlong.llm import STAR_PREFIX, JevError, LLMUnavailable, make_llm
from tianlong.llm.schemas import Decision, SpeechOut
from tianlong.minds import decide, talk
from tianlong.minds.types import MindDecision

from .conftest import fresh, open_bout, with_route


def _j2a_requests(llm: Any, kind: str = "J2a") -> list[dict]:
    return [r["request"] for r in llm.recorder.records if r.get("kind") == kind]


def _player_says(defs: Any, state: Any, sp: Speech) -> list:
    w = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    assert w is not None
    return resolve.apply(state, defs, w, sp, source="player")


# ---------------------------------------------------------------- reactors & order


def test_pick_reactors_at_the_bout_puts_participants_first(world):
    defs, state = world
    open_bout(defs, state)
    actors = minds.pick_reactors(state, defs, [])
    assert actors[:2] == ["gong_guangjie", "duan_yu"]  # 情境里的两个角色位，initiative 高的在前
    assert len(actors) <= minds.MAX_REACTORS
    assert state.player not in actors
    hall = state.chars[state.player].loc
    assert all(state.chars[a].loc == hall for a in actors)
    # 没有 ★、也与本轮无关的路人不进来
    assert "wl_disciple_2" not in actors


def test_pick_reactors_includes_the_addressee_and_respects_limit(world):
    defs, state = world
    evs = _player_says(defs, state, Speech(act="greet", to=["xin_shuangqing"]))
    actors = minds.pick_reactors(state, defs, evs, limit=3)
    assert len(actors) == 3
    assert actors[0] == "xin_shuangqing"


def test_pick_reactors_skips_the_unconscious(world):
    defs, state = world
    open_bout(defs, state)
    resolve.run_effects(state, defs, [{"add_status": ["duan_yu", "unconscious", {}]}], source="rule")
    assert "duan_yu" not in minds.pick_reactors(state, defs, [])


def test_order_participants_first_then_initiative(world):
    defs, state = world
    open_bout(defs, state)
    ds = [
        MindDecision(actor=a, action=actions.build(state, defs, a, "wait", {"ticks": 1}))
        for a in ["ma_wude", "zuo_zimu", "duan_yu", "gong_guangjie", "rong_ziju"]
    ]
    out = [d.actor for d in minds.order(state, ds, [])]
    assert out[:2] == ["gong_guangjie", "duan_yu"]  # 当事人（情境角色），龚的 initiative 更高
    rest = out[2:]
    inits = [state.chars[a].initiative for a in rest]
    assert inits == sorted(inits, reverse=True)


def test_apply_decision_downgrades_illegal_action_to_wait(world):
    defs, state = world
    sid = open_bout(defs, state)
    laugh = actions.build(state, defs, "duan_yu", "setpiece", {"situation": sid, "name": "laugh"})
    assert laugh is None  # 还没认输，笑不出来
    feint = actions.build(state, defs, "gong_guangjie", "setpiece", {"situation": sid, "name": "feint_win"})
    resolve.run_effects(state, defs, [{"close": sid}], source="rule")
    d = MindDecision(actor="gong_guangjie", action=feint)
    evs = minds.apply_decision(state, defs, d)
    assert any("no longer legal" in v for v in state.violations)
    assert all(e.verb != "feint_win" for e in evs)


# ---------------------------------------------------------------- J2 with the mock


async def test_mock_gong_picks_his_star_setpiece(world, llm):
    defs, state = world
    open_bout(defs, state)
    d = await minds.decide_one(llm, state, defs, "gong_guangjie")
    assert d.via == "jev"
    assert d.action.verb == "setpiece" and d.action.params["name"] == "feint_win"
    # 送给 Jev 的选项：★ 的那项带前缀，排第一；有「什么也不做」
    req = _j2a_requests(llm)[-1]
    crit = req["questions"]["action"]["criteria"]
    first_id, first_text = next(iter(crit.items()))
    assert first_id == d.action.id and first_text.startswith(STAR_PREFIX)
    assert "none" in crit
    assert "【你是谁】龚光杰" in req["state"]


async def test_mock_chain_feint_laugh_slap(world, llm):
    defs, state = world
    open_bout(defs, state)
    seen: list[str] = []
    events: list = []
    for _ in range(6):
        actors = minds.pick_reactors(state, defs, events)
        ds = await minds.decide_all(llm, state, defs, actors, events=events)
        events = []
        for d in minds.order(state, ds, events):
            events += minds.apply_decision(state, defs, d)
        seen += [e.verb for e in events]
        if state.flags.get("slapped"):
            break
    assert seen.index("feint_win") < seen.index("laugh") < seen.index("slap")


async def test_decide_all_is_deterministic(llm, settings):
    out = []
    for _ in range(2):
        defs, state = fresh()
        open_bout(defs, state)
        lm = make_llm(settings, mock=True, run_dir=None)
        actors = minds.pick_reactors(state, defs, [])
        ds = await minds.decide_all(lm, state, defs, actors)
        out.append(([(d.actor, d.action.signature, d.via) for d in ds], state.rng_counter))
    assert out[0] == out[1]


async def test_star_speech_goes_through_j2b(world, llm):
    defs, state = world
    open_bout(defs, state)
    resolve.run_effects(
        state,
        defs,
        [
            {"set_flag": ["guest_laughed", True]},
            {"step_state": ["gong_guangjie", "gg_bout", "done"]},
        ],
        source="rule",
    )
    d = await minds.decide_one(llm, state, defs, "gong_guangjie")
    assert d.speech is not None
    assert d.speech.act == "taunt" and d.speech.to == ["duan_yu"]
    assert d.speech.gist
    assert any(r.get("kind") == "J2b" for r in llm.recorder.records)
    evs = minds.apply_decision(state, defs, d)
    assert any(e.verb == "say" for e in evs)
    assert state.chars["gong_guangjie"].agenda.steps[1].state == "done"


# ---------------------------------------------------------------- bottom lines & replies


async def test_bottom_line_action_never_offered(world, llm):
    defs, state = world
    open_bout(defs, state)
    # 段誉的底线：不下杀手
    acts = actions.legal_actions(state, defs, "duan_yu")
    assert not any(a.verb == "attack" and a.params.get("intent") == "kill" for a in acts)
    d = await minds.decide_one(llm, state, defs, "duan_yu")
    assert not (d.action.verb == "attack" and d.action.params.get("intent") == "kill")
    crit = _j2a_requests(llm)[-1]["questions"]["action"]["criteria"]
    assert not any("下杀手" in t for t in crit.values())


def test_bottom_line_speech_is_dropped(world):
    defs, state = world
    from tianlong.kernel import speech as kspeech

    c = state.chars["ma_wude"]
    opts = kspeech.speech_options(state, defs, "ma_wude")
    assert talk.compose(state, defs, c, opts, act="insult", to="zuo_zimu") is None  # 不辱骂左子穆
    sp = talk.compose(state, defs, c, opts, act="insult", to="gong_guangjie")
    assert sp is not None and sp.to == ["gong_guangjie"] and sp.gist


def _no_verdict_request(defs: Any, state: Any) -> str:
    sp = Speech(act="request", to=["zuo_zimu"], ask_action={"verb": "follow", "params": {"target": "player"}})
    evs = _player_says(defs, state, sp)
    rid = evs[0].speech.request
    assert state.requests[rid].verdict == "no"
    return rid


async def test_no_verdict_can_never_be_agreed(world, llm, monkeypatch):
    defs, state = world
    rid = _no_verdict_request(defs, state)

    def hinted(state_: Any, defs_: Any, m: Any) -> dict:
        return {"act": "agree", f"reply_{rid}": "agree", "to": "player", "stance": "warm", "whisper": 0.0}

    monkeypatch.setattr(decide, "j2b_hints", hinted)
    d = await minds.decide_one(llm, state, defs, "zuo_zimu", events=list(state.events_tail[-1:]))
    assert d.speech is not None
    assert d.speech.act == "refuse" and d.speech.request == rid
    # J2b 的答复题只列裁决允许的答复
    j2b = _j2a_requests(llm, "J2b")[-1]["questions"]
    assert list(j2b[f"reply_{rid}"]["criteria"]) == ["refuse"]
    assert "agree" not in j2b["act"]["criteria"]
    minds.apply_decision(state, defs, d)
    assert state.requests[rid].status == "refused"


def test_compose_and_kernel_turn_agree_into_refuse(world):
    defs, state = world
    from tianlong.kernel import speech as kspeech

    rid = _no_verdict_request(defs, state)
    c = state.chars["zuo_zimu"]
    opts = kspeech.speech_options(state, defs, "zuo_zimu")
    sp = talk.compose(state, defs, c, opts, act="agree", replies={rid: "agree"})
    assert sp is not None and sp.act == "refuse"
    # 内核那一道闸：硬说「答应」也改判为拒绝
    w = actions.build(state, defs, "zuo_zimu", "wait", {"ticks": 1})
    resolve.apply(state, defs, w, Speech(act="agree", to=["player"], request=rid), source="mind")
    assert state.requests[rid].status == "refused"


async def test_may_verdict_reply_is_agree_under_mock(world, llm):
    defs, state = world
    sp = Speech(act="request", to=["ma_wude"], ask_action={"verb": "wait", "params": {}}, action_class="trivial")
    evs = _player_says(defs, state, sp)
    rid = evs[0].speech.request
    assert state.requests[rid].verdict == "may"
    d = await minds.decide_one(llm, state, defs, "ma_wude", events=evs)
    assert d.speech is not None and d.speech.act == "agree" and d.speech.to == ["player"]


# ---------------------------------------------------------------- escalation & fallbacks


async def test_important_moment_escalates_to_c2(world, llm):
    defs, state = world
    # 冲着左子穆的「冒险」请求：要紧，改交 C2（Mock 的回答与规则一致）
    sp = Speech(
        act="request",
        to=["zuo_zimu"],
        ask_action={"verb": "attack", "params": {"target": "gong_guangjie", "intent": "injure"}},
    )
    evs = _player_says(defs, state, sp)
    rid = evs[0].speech.request
    assert state.requests[rid].action_class == "risky"
    d = await minds.decide_one(llm, state, defs, "zuo_zimu", events=evs)
    assert d.important and d.escalated and d.via == "claude"
    c2 = [r for r in llm.recorder.records if str(r.get("kind", "")).startswith("C2")]
    assert c2 and c2[-1]["kind"] == "C2_soul" and c2[-1]["escalated"]
    assert d.speech is not None and d.speech.act == "refuse"


async def test_c2_illegal_action_falls_back_to_star(world, settings, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    lm = make_llm(with_route(settings, decide_soul="claude_only"), mock=True, run_dir=None)

    async def bad_c2(*a: Any, **k: Any) -> tuple:
        sp = SpeechOut(act="insult", to=["duan_yu"], stance="mocking", gist="x", fact_refs=[], request_ref=None,
                       whisper=False)
        return Decision(inner="乱来", action="a99", speech=sp, display="冷笑"), None

    monkeypatch.setattr(lm.claude, "call_json", bad_c2)
    d = await minds.decide_one(lm, state, defs, "gong_guangjie")
    assert d.via == "rule"
    assert d.action.verb == "setpiece" and d.action.params["name"] == "feint_win"
    assert d.speech is None
    assert any("not on the list" in n for n in d.notes)


async def test_c2_answer_is_validated(world, settings, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    lm = make_llm(with_route(settings, decide_soul="claude_only"), mock=True, run_dir=None)

    async def c2(*a: Any, **k: Any) -> tuple:
        sp = SpeechOut(act="taunt", to=["duan_yu", "nobody"], stance="mocking", gist="笑对方是个书呆子",
                       fact_refs=["F_not_known_to_him"], request_ref=None, whisper=False)
        return Decision(inner="这小子欠教训，非让他出丑不可，" * 3, action="none", speech=sp, display="冷笑一声，满脸不屑"), None

    monkeypatch.setattr(lm.claude, "call_json", c2)
    d = await minds.decide_one(lm, state, defs, "gong_guangjie")
    assert d.via == "claude" and d.action.verb == "wait"
    assert d.speech is not None and d.speech.to == ["duan_yu"] and d.speech.fact_refs == []
    assert d.speech.gist.startswith("笑对方是个书呆子")
    assert len(d.inner) <= decide.INNER_MAX and len(d.display) <= decide.DISPLAY_MAX


async def test_llm_failure_falls_back_to_rule(world, llm, monkeypatch):
    defs, state = world
    open_bout(defs, state)

    async def jev_down(*a: Any, **k: Any) -> Any:
        raise JevError("timeout")

    async def claude_down(*a: Any, **k: Any) -> Any:
        raise LLMUnavailable("no key")

    monkeypatch.setattr(llm.jev, "ask", jev_down)
    monkeypatch.setattr(llm.claude, "call_json", claude_down)
    ds = await minds.decide_all(llm, state, defs, ["gong_guangjie", "duan_yu"])
    g, y = ds
    assert g.via == "rule" and g.action.params.get("name") == "feint_win"
    assert y.via == "rule" and y.action.verb == "wait" and y.speech is None
    assert any("J2a failed" in n for n in g.notes) and any("C2 failed" in n for n in g.notes)


async def test_no_keys_at_all_still_decides(world, settings):
    defs, state = world
    open_bout(defs, state)
    lm = make_llm(settings, mock=False, run_dir=None)  # 没有任何密钥
    d = await minds.decide_one(lm, state, defs, "gong_guangjie")
    assert d.via == "rule" and d.action.params.get("name") == "feint_win"


async def test_escalation_failure_keeps_the_j2_answer(world, llm, monkeypatch):
    defs, state = world
    sp = Speech(
        act="request",
        to=["zuo_zimu"],
        ask_action={"verb": "attack", "params": {"target": "gong_guangjie", "intent": "injure"}},
    )
    evs = _player_says(defs, state, sp)

    async def claude_down(*a: Any, **k: Any) -> Any:
        raise LLMUnavailable("no key")

    monkeypatch.setattr(llm.claude, "call_json", claude_down)
    d = await minds.decide_one(llm, state, defs, "zuo_zimu", events=evs)
    assert d.via == "jev" and d.escalated
    assert d.speech is not None and d.speech.act == "refuse"


async def test_rules_only_route_makes_no_calls(world, settings):
    defs, state = world
    open_bout(defs, state)
    lm = make_llm(with_route(settings, decide_soul="rules_only", decide_named="rules_only"), mock=True, run_dir=None)
    ds = await minds.decide_all(lm, state, defs, minds.pick_reactors(state, defs, []))
    assert lm.recorder.count == 0
    assert all(d.via in ("rule", "routine") for d in ds)
    assert ds[0].action.params.get("name") == "feint_win"


async def test_claude_first_falls_back_to_jev(world, settings, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    lm = make_llm(with_route(settings, decide_soul="claude_first"), mock=True, run_dir=None)

    async def claude_down(*a: Any, **k: Any) -> Any:
        raise LLMUnavailable("no key")

    monkeypatch.setattr(lm.claude, "call_json", claude_down)
    d = await minds.decide_one(lm, state, defs, "gong_guangjie")
    assert d.via == "jev" and d.action.params.get("name") == "feint_win"


async def test_extras_use_routines(world, llm):
    defs, state = world
    d = await minds.decide_one(llm, state, defs, "wl_disciple_2")
    assert d.via == "routine"
    assert llm.recorder.count == 0


@pytest.mark.parametrize("actor", ["zuo_zimu", "xin_shuangqing"])
async def test_named_and_soul_use_their_c2_kind(world, settings, monkeypatch, actor):
    defs, state = world
    lm = make_llm(with_route(settings, decide_soul="claude_only", decide_named="claude_only"), mock=True,
                  run_dir=None)
    await minds.decide_one(lm, state, defs, actor)
    kind = lm.recorder.records[-1]["kind"]
    assert kind == ("C2_soul" if state.chars[actor].tier == "soul" else "C2_named")
    system = lm.recorder.records[-1]["request"]["system"]
    text = str(system)
    assert "你要以一个人的身份" in text and "<人物卡>" in text


# ---------------------------------------------------------------- real-shaped Jev answers


def _fake_jev(choice_probs: dict[str, float], speak: float = 0.0, needs: float = 0.0):
    from tianlong.llm.jev import wrap_answers

    async def ask(kind: str, state: Any, questions: dict, *, hints: Any = None, escalated: bool = False):
        out: dict[str, Any] = {}
        for qid, q in questions.items():
            if q["type"] == "choice":
                keys = list(q["criteria"])
                probs = {k: choice_probs.get(k, 0.0) for k in keys} if qid == "action" else {keys[0]: 1.0}
                top = max(probs, key=lambda k: probs[k])
                out[qid] = {"type": "choice", "choice": top, "confidence": probs[top], "probabilities": probs}
            elif qid == "speak":
                out[qid] = {"type": "noul", "noul": speak}
            elif qid == "needs_thought":
                out[qid] = {"type": "noul", "noul": needs}
            else:
                out[qid] = {"type": "noul", "noul": 0.0}
        return wrap_answers(out)

    return ask


async def test_unsure_jev_escalates_to_c2(world, llm, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    monkeypatch.setattr(llm.jev, "ask", _fake_jev({"a01": 0.45, "a02": 0.40, "a03": 0.15}))
    d = await minds.decide_one(llm, state, defs, "gong_guangjie")
    assert d.escalated and d.via == "claude"


async def test_needs_thought_escalates(world, llm, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    monkeypatch.setattr(llm.jev, "ask", _fake_jev({"a01": 1.0}, needs=0.9))
    d = await minds.decide_one(llm, state, defs, "gong_guangjie")
    assert d.escalated and d.via == "claude"


async def test_offscreen_does_not_escalate_when_merely_unsure(world, llm, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    monkeypatch.setattr(llm.jev, "ask", _fake_jev({"a01": 0.45, "a02": 0.40, "a03": 0.15}))
    before = state.rng_counter
    d = await minds.decide_one(llm, state, defs, "gong_guangjie", offscreen=True)
    assert not d.escalated and d.via == "jev"
    assert d.action.id in ("a01", "a02", "a03")
    assert state.rng_counter == before + 1  # 三项都过了 sample_floor：掷一次 kernel.rng


async def test_sampling_ignores_options_below_floor(world, llm, monkeypatch):
    defs, state = world
    open_bout(defs, state)
    # 头名把握足：不升级；第二名低于 sample_floor，不会被取到，也不掷骰
    monkeypatch.setattr(llm.jev, "ask", _fake_jev({"a02": 0.9, "a03": 0.1}))
    before = state.rng_counter
    d = await minds.decide_one(llm, state, defs, "gong_guangjie")
    assert d.via == "jev" and d.action.id == "a02"
    assert state.rng_counter == before
