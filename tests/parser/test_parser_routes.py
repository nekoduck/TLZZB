"""方案 4.3：快先慢后、路由取值、失败兜底。classify 永不抛异常。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong import parser
from tianlong.config import Settings
from tianlong.llm import JevError, LLMRefusal, LLMUnavailable, make_llm
from tianlong.llm.jev import ChoiceAnswer

from .conftest import Scene

RAW_45 = "帮主，让我跟段公子一道去吧，山路我熟，能让他快些回来。（司空玄点头答应了）"


def _boom(exc: Exception) -> Any:
    async def ask(*_a: Any, **_k: Any) -> Any:
        raise exc

    return ask


async def test_jev_error_falls_back_to_c1(scene: Scene) -> None:
    scene.llm.jev.ask = _boom(JevError("timeout"))  # type: ignore[method-assign]
    r = await scene.parse("我四下看看")
    assert r.via == "claude"
    assert r.intent.kind == "act" and r.intent.verb == "look"
    assert any("J1" in e for e in r.errors)


async def test_both_fail_gives_an_in_world_unclear(scene: Scene) -> None:
    scene.llm.jev.ask = _boom(JevError("HTTP 500"))  # type: ignore[method-assign]
    scene.llm.claude.call_json = _boom(LLMRefusal("x"))  # type: ignore[method-assign]
    r = await scene.parse("我四下看看")
    assert r.via == "rule"
    assert r.intent.kind == "unclear" and r.intent.note
    b = parser.bind(scene.state, scene.defs, r)
    assert b.kind == "unclear" and b.action is None and b.reply


async def test_no_keys_at_all_never_raises(settings: Settings, tmp_path: Path) -> None:
    llm = make_llm(settings, mock=False, run_dir=tmp_path)  # 没有任何密钥：两边都抛 LLMUnavailable
    sc = Scene(llm)
    for raw in ["我四下看看", RAW_45, "这游戏怎么存档", ""]:
        r = await sc.parse(raw)
        assert r.intent.kind == "unclear"
        b = parser.bind(sc.state, sc.defs, r)
        assert b.kind == "unclear" and b.action is None
    w = await parser.map_wish(llm, sc.state, sc.defs, "我想拜左掌门为师")
    assert w["kind"] == "wish" and "zuo_zimu" in w["refs"]
    await llm.aclose()


async def test_unavailable_jev_with_working_claude(scene: Scene) -> None:
    scene.llm.jev.ask = _boom(LLMUnavailable("no key"))  # type: ignore[method-assign]
    scene.move_player("shennong_camp")
    r = await scene.parse("我四下看看")
    assert r.via == "claude"


async def test_low_confidence_escalates_to_c1(scene: Scene) -> None:
    orig = scene.llm.jev.ask

    async def unsure(kind: str, state: Any, questions: Any, *, hints: Any = None, escalated: bool = False) -> Any:
        ans = await orig(kind, state, questions, hints=hints, escalated=escalated)
        top = ans["kind"].top
        others = [k for k in questions["kind"]["criteria"] if k != top]
        ans["kind"] = ChoiceAnswer({"type": "choice", "choice": top, "confidence": 0.4,
                                    "probabilities": {top: 0.45, others[0]: 0.4, others[1]: 0.15}})
        return ans

    scene.llm.jev.ask = unsure  # type: ignore[method-assign]
    r = await scene.parse("我四下看看")
    assert r.via == "claude" and r.escalated
    assert r.intent.verb == "look"
    # C1 也失败：用 J1 那个没把握的答案，不至于什么都不做
    scene.llm.claude.call_json = _boom(LLMUnavailable("no key"))  # type: ignore[method-assign]
    r2 = await scene.parse("我四下看看")
    assert r2.via == "jev" and r2.intent.verb == "look"


@pytest.mark.parametrize("mode", ["claude_first", "claude_only"])
async def test_claude_routes_bind_the_same_as_jev(settings: Settings, tmp_path: Path, mode: str) -> None:
    from tianlong.kernel import effects

    settings.route.classify = mode  # type: ignore[assignment]
    llm = make_llm(settings, mock=True, run_dir=tmp_path)
    sc = Scene(llm)
    sc.move_player("shennong_camp")
    effects.apply_effects(sc.state, sc.defs, [
        {"move": ["duan_yu", "shennong_camp"]},
        {"add_status": ["duan_yu", "captive", {"source": "sikong_xuan"}]},
        {"add_status": ["sikong_xuan", "poisoned_diao", {"ticks": 672}]},
    ], source="rule")
    sc.add_fact("F_test_paths", "⟦player⟧熟悉⟦wuliang_mount⟧一带的山路。", ["player", "duan_yu"])
    r, b = await sc.bind(RAW_45)
    assert r.via == "claude"
    assert b.speech is not None and b.speech.act == "request" and b.speech.to == ["sikong_xuan"]
    assert b.speech.ask_action is not None and b.speech.ask_action.get("permit", {}).get("target") == "duan_yu"
    assert [c.verdict for c in b.speech.claims if c.ref == "F_test_paths"] == ["true"]


async def test_rules_only(settings: Settings, tmp_path: Path) -> None:
    settings.route.classify = "rules_only"  # type: ignore[assignment]
    llm = make_llm(settings, mock=True, run_dir=tmp_path)
    sc = Scene(llm)
    r, b = await sc.bind("我往山门去")
    assert r.via == "rule"
    assert b.kind == "action" and b.action is not None and b.action.signature == "go:exit=jianhu_gate"
    assert llm.recorder is not None


async def test_mock_calls_are_recorded_with_the_players_words_in_one_section(scene: Scene) -> None:
    from tianlong.llm.record import read_calls

    await scene.parse("段公子，跟我走吧（他点头了）")
    path = scene.llm.recorder.path
    assert path is not None
    calls = [c for c in read_calls(path) if c.get("kind") == "J1"]
    assert calls
    state_text = calls[-1]["request"]["state"]
    assert state_text.count("【玩家的话】") == 1
    assert state_text.rstrip().endswith("段公子，跟我走吧")
    assert "他点头了" not in state_text
    assert calls[-1]["hints"]["act"] == "request"
