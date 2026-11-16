"""探针与扫描器自己的测试：三条保证的检查不是摆设——故意造出幽灵事件、回弹与绕过内核的改动，都得被查出来。

直接对真实的 content/ 开局状态调用内核入口，不经回合主循环（很快）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.content.loader import load as load_content
from tianlong.kernel import resolve
from tianlong.kernel.state import Ctx
from tianlong.testing import Driver, Tracer, ghost_events, rebound_events
from tianlong.testing.driver import REPO
from tianlong.testing.trace import world_fingerprint


@pytest.fixture
def world() -> tuple:
    defs, state = load_content(REPO / "content", player_name="阿青", seed=1)
    return state, defs


EMIT = {"emit": {"actor": "self", "verb": "probe", "text": "⟦duan_yu⟧做了一件事"}}


def test_a_canon_step_run_while_its_pre_is_false_is_a_ghost(world: tuple) -> None:
    state, defs = world
    step = next(s for s in state.chars["duan_yu"].agenda.steps if s.id == "dy_study_scroll")
    assert step.origin == "canon" and step.state == "pending"  # 前提：帛卷在他手里——开局时不成立
    with Tracer() as tr:
        evs = resolve.run_effects(state, defs, [EMIT], Ctx(self_id="duan_yu"), source="agenda", step=step.id)
    assert evs and evs[0].step == step.id
    problems = ghost_events(evs, tr, defs)
    assert len(problems) == 1 and "dy_study_scroll" in problems[0] and "pre was false" in problems[0]


def test_a_canon_step_whose_pre_holds_is_not_a_ghost(world: tuple) -> None:
    state, defs = world
    step = state.chars["duan_yu"].agenda.steps[0]  # 在厅上看比剑：开局时前提成立
    with Tracer() as tr:
        evs = resolve.run_effects(state, defs, [EMIT], Ctx(self_id="duan_yu"), source="agenda", step=step.id)
    assert evs and ghost_events(evs, tr, defs) == []
    assert tr.step_checks[evs[0].id].ok


def test_a_canon_situation_opened_while_its_condition_is_false_is_a_ghost(world: tuple) -> None:
    state, defs = world
    assert defs.situations["diao_bite"].origin == "canon"  # 条件：钟灵被扣着、与司空玄同处一地——开局时不成立
    with Tracer() as tr:
        evs = resolve.settle_offscreen(state, defs, "diao_bite")
    assert evs, "the kernel settles whatever it is asked to: checking `when` is the caller's job"
    problems = ghost_events(evs, tr, defs)
    assert problems and all("diao_bite" in p and "condition was false" in p for p in problems)


def test_an_event_with_a_step_nobody_saw_enter_the_kernel_is_reported(world: tuple) -> None:
    state, defs = world
    evs = resolve.run_effects(state, defs, [EMIT], Ctx(self_id="duan_yu"), source="agenda", step="dy_watch_bout")
    with Tracer() as tr:  # 探针是事后才装上的：这条事件没有出处
        pass
    assert any("no kernel entry was seen" in p for p in ghost_events(evs, tr, defs))


def test_a_write_that_bypasses_the_kernel_is_a_rebound(world: tuple) -> None:
    state, defs = world
    with Tracer() as tr:
        resolve.run_effects(state, defs, [{"set_flag": ["probe_a", True]}], source="storyteller")
        assert tr.foreign == []
        state.chars["duan_yu"].loc = "valley_cave"  # 有人绕过内核，直接把他搬到了洞里
        state.items["silk_scroll"].feature = None
        resolve.run_effects(state, defs, [{"set_flag": ["probe_b", True]}], source="storyteller")
    assert len(tr.foreign) == 1
    assert any("duan_yu" in d for d in tr.foreign[0].diff) and any("silk_scroll" in d for d in tr.foreign[0].diff)
    assert any("outside the kernel" in p for p in rebound_events([], tr))


def test_rule_source_effects_that_move_the_world_are_a_rebound(world: tuple) -> None:
    state, defs = world
    with Tracer() as tr:
        evs = resolve.run_effects(state, defs, [{"move": ["duan_yu", "valley_cave"]}], source="rule")
        book = resolve.run_effects(state, defs, [{"set_flag": ["probe", True]}, {"replan": "duan_yu"}], source="rule")
    problems = rebound_events(evs + book, tr)
    assert any("outside bookkeeping" in p and "move" in p for p in problems)
    assert any("has source rule" in p for p in problems)  # 那条「来到」的事件本身也算
    assert not any("set_flag" in p or "replan" in p for p in problems)  # 记账的不算
    # 同样的效果，来源是情境或机会牌，就是世界自己的改动
    with Tracer() as tr:
        evs = resolve.run_effects(state, defs, [{"move": ["duan_yu", "jianhu_hall"]}], source="situation")
    assert rebound_events(evs, tr) == []


def test_only_changes_after_the_divergence_count(world: tuple) -> None:
    state, defs = world
    with Tracer() as tr:
        early = resolve.run_effects(state, defs, [{"move": ["duan_yu", "valley_cave"]}], source="rule")
        mark = state.next_event_id - 1
        state.tick += 8
        late = resolve.run_effects(state, defs, [{"move": ["duan_yu", "jianhu_hall"]}], source="rule")
    problems = rebound_events(early + late, tr, after_id=mark)
    assert problems and all(f"event {e.id} " not in p for p in problems for e in early)
    assert all("tick 32:" not in p for p in problems)


async def test_test_setup_and_debug_cheats_are_told_apart(tmp_path: Path) -> None:
    from tianlong.testing import make_settings

    with Tracer() as tr:
        drv = await Driver.new(make_settings(tmp_path), seed=1, debug=True)
        tr.attach(drv)
        moved = drv.fx([{"move": ["duan_yu", "jianhu_back"]}])  # 测试自己布的景
        await drv.debug("give rope duan_yu")  # 调试作弊
        await drv.close()
    assert moved and all(e.id in drv.cheat_ids for e in moved)
    assert rebound_events(drv.events, tr, cheat_ids=drv.cheat_ids) == []
    # 不告诉扫描器哪些是布景，它就照实报
    assert rebound_events(drv.events, tr, cheat_ids=()) != []


def test_the_fingerprint_sees_the_world_not_the_bookkeeping(world: tuple) -> None:
    state, defs = world
    fp = world_fingerprint(state)
    state.turn += 1
    state.rng_counter += 5
    state.replan_queue.append("duan_yu")
    assert world_fingerprint(state) == fp
    state.chars["duan_yu"].inventory.append("rope")
    assert world_fingerprint(state) != fp
