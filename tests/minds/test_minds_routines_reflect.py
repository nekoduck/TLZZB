"""路人的作息（方案 9.4）与夜间反思（方案 7.10）。"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import actions, resolve
from tianlong.kernel.state import MemoryItem
from tianlong.llm import LLMUnavailable
from tianlong.minds import reflect, routines

from .conftest import REPO


def _routine_names() -> set[str]:
    import yaml

    opening = yaml.safe_load((REPO / "content" / "opening.yaml").read_text(encoding="utf-8"))
    return {e["routine"] for e in opening.get("extras") or [] if e.get("routine")}


def _move(defs: Any, state: Any, who: str, loc: str) -> None:
    resolve.run_effects(state, defs, [{"move": [who, loc]}], source="rule")


def test_every_routine_name_is_defined(world):
    defs, _ = world
    names = _routine_names()
    assert names >= {"shennong_guard", "wuliang_disciple", "wanjie_servant", "herb_gatherer", "merchant"}
    assert names <= set(routines.table(defs))


def test_every_extra_gets_a_legal_action_alone_and_with_the_player(world):
    defs, state = world
    extras = [c for c in state.chars.values() if c.tier == "extra" and c.routine]
    assert {c.routine for c in extras} == _routine_names()
    for c in extras:
        for with_player in (False, True):
            if with_player:
                _move(defs, state, state.player, c.loc)
            d = routines.act(state, defs, c.id)
            assert d.via == "routine" and d.actor == c.id
            assert actions.is_legal(state, defs, d.action) is not None, (c.id, d.action.signature)
            if d.speech is not None:
                assert d.speech.gist


def test_shennong_guard_questions_an_outsider_once(world):
    defs, state = world
    _move(defs, state, state.player, "shennong_camp")
    d = routines.act(state, defs, "sn_guard_1")
    assert d.speech is not None and d.speech.act == "ask" and d.speech.to == [state.player]
    assert "喝问" in d.speech.gist
    resolve.apply(state, defs, d.action, d.speech, source="mind")
    again = routines.act(state, defs, "sn_guard_1")
    assert again.speech is None or again.speech.to != [state.player] or again.speech.act != "ask"


def test_guard_blocks_whoever_struck_an_ally(world):
    defs, state = world
    _move(defs, state, state.player, "shennong_camp")
    a = actions.build(state, defs, state.player, "attack", {"target": "sn_guard_2", "intent": "injure"})
    assert a is not None
    resolve.apply(state, defs, a, source="player")
    d = routines.act(state, defs, "sn_guard_1")
    assert d.action.verb == "block" and d.action.params["target"] == state.player


def test_merchant_greets_then_gossips(world):
    defs, state = world
    _move(defs, state, state.player, "ferry_bridge")
    d = routines.act(state, defs, "merchant_1")
    assert d.speech is not None and d.speech.act == "greet"
    resolve.apply(state, defs, d.action, d.speech, source="mind")
    # 让客商知道一件玩家不知道的传闻
    resolve.run_effects(state, defs, [{"learn": ["merchant_1", "F_shennong_siege", 1.0, "saw"]}], source="rule")
    d2 = routines.act(state, defs, "merchant_1")
    assert d2.speech is not None and d2.speech.act == "tell" and d2.speech.fact_refs == ["F_shennong_siege"]
    resolve.apply(state, defs, d2.action, d2.speech, source="mind")
    d3 = routines.act(state, defs, "merchant_1")
    assert d3.speech is None or d3.speech.fact_refs != ["F_shennong_siege"]


def test_extra_flees_from_a_hostile(world):
    defs, state = world
    _move(defs, state, state.player, "pine_wood")
    resolve.run_effects(state, defs, [{"rel": ["player", "herb_gatherer_1", "affinity", -60]}], source="rule")
    d = routines.act(state, defs, "herb_gatherer_1")
    assert d.action.verb == "flee"


def test_extra_takes_its_situation_setpiece(world):
    defs, state = world
    _move(defs, state, state.player, "jianhu_gate")
    resolve.run_effects(state, defs, [{"set_flag": ["bout_running", False]}], source="rule")
    resolve.run_effects(
        state, defs, [{"open": ["challenge", "jianhu_gate", {"keeper": "wl_disciple_1", "intruder": "player"}]}],
        source="situation",
    )
    assert any(s.kind == "challenge" for s in state.situations.values())
    d = routines.act(state, defs, "wl_disciple_1")
    assert d.action.verb == "setpiece" and d.action.params["name"] == "question"
    resolve.apply(state, defs, d.action, d.speech, source="mind")
    d2 = routines.act(state, defs, "wl_disciple_1")
    assert d2.action.verb == "setpiece" and d2.action.params["name"] == "let_pass"


# ---------------------------------------------------------------- C6


def _fill(state: Any, cid: str, n: int, about: str = "duan_yu") -> None:
    c = state.chars[cid]
    for i in range(n):
        c.memory.append(
            MemoryItem(tick=state.tick, kind="saw", text=f"⟦{about}⟧做了第{i}件事", entities=[about], salience=3 + i % 5)
        )


async def test_nightly_writes_reflections_via_effects(world, llm):
    defs, state = world
    _fill(state, "zuo_zimu", 8)
    _fill(state, "gong_guangjie", 7)  # 不够 8 条
    assert [c.id for c in reflect.due(state)] == ["zuo_zimu"]
    await reflect.nightly(llm, state, defs)
    z = state.chars["zuo_zimu"]
    assert z.memory[-1].kind == "reflection"
    assert "duan_yu" in z.memory[-1].entities
    assert z.rel["duan_yu"].impression
    assert all(m.kind != "reflection" for m in state.chars["gong_guangjie"].memory)
    c6 = [r for r in llm.recorder.records if r.get("kind") == "C6"]
    assert len(c6) == 1
    # 反思过了，要再攒满 8 条才会再来
    assert reflect.due(state) == []
    _fill(state, "zuo_zimu", 8)
    assert [c.id for c in reflect.due(state)] == ["zuo_zimu"]


async def test_nightly_validates_model_output(world, llm, monkeypatch):
    from tianlong.llm.schemas import Impression, Reflection, ReflectOut

    defs, state = world
    _fill(state, "zuo_zimu", 8)

    async def c6(*a: Any, **k: Any) -> tuple:
        out = ReflectOut(
            reflections=[Reflection(text=f"想法{i}", about=["duan_yu", "nobody"], salience=7) for i in range(5)],
            impressions=[
                Impression(char="zuo_zimu", text="自己"),
                Impression(char="nobody", text="?"),
                Impression(char="gong_guangjie", text="毛躁"),
            ],
        )
        return out, None

    monkeypatch.setattr(llm.claude, "call_json", c6)
    await reflect.nightly(llm, state, defs)
    z = state.chars["zuo_zimu"]
    refl = [m for m in z.memory if m.kind == "reflection"]
    assert len(refl) == reflect.MAX_REFLECTIONS
    assert all(m.entities == ["duan_yu"] for m in refl)
    assert z.rel["gong_guangjie"].impression == "毛躁"
    assert "nobody" not in z.rel and z.rel.get("zuo_zimu") is None


async def test_nightly_skips_silently_on_failure(world, llm, monkeypatch):
    defs, state = world
    _fill(state, "zuo_zimu", 9)
    before = len(state.chars["zuo_zimu"].memory)

    async def down(*a: Any, **k: Any) -> Any:
        raise LLMUnavailable("no key")

    monkeypatch.setattr(llm.claude, "call_json", down)
    assert await reflect.nightly(llm, state, defs) == []
    assert len(state.chars["zuo_zimu"].memory) == before


async def test_dead_souls_do_not_reflect(world, llm):
    defs, state = world
    _fill(state, "zuo_zimu", 8)
    resolve.run_effects(state, defs, [{"kill": "zuo_zimu"}], source="rule")
    assert all(c.id != "zuo_zimu" for c in reflect.due(state))
