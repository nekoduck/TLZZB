"""确定性（同一种子 → 同样的结果）、小世界里的随机试探、apply 的性能。"""

from __future__ import annotations

import time

from tianlong.kernel import actions, invariants, predicates, resolve, rng, speech, timeflow
from tianlong.kernel.state import Speech

from .conftest import World

ACTORS = ("player", "fighter", "guest", "girl")


def _choose(w: World, items: list) -> object:  # type: ignore[type-arg]
    return items[int(rng.uniform(w.s) * len(items))]


def _speech(w: World, actor: str) -> Speech | None:
    opts = speech.speech_options(w.s, w.defs, actor)
    if not opts.acts or rng.roll(w.s) > 30:
        return None
    act = _choose(w, opts.acts)
    sp = Speech(act=act, to=[])  # type: ignore[arg-type]
    if act in ("tell", "warn") and opts.facts:
        sp.fact_refs = [_choose(w, opts.facts)]  # type: ignore[list-item]
    if act in ("request", "plead", "order"):
        sp.ask_action = {"verb": "follow", "params": {"target": actor}}
    if act in ("agree", "refuse", "bargain") and opts.requests:
        sp.request = opts.requests[0].request.id
    return sp


def play(seed: int, turns: int) -> World:
    """不经模型：每回合每人从合法清单里随机挑一个动作（偶尔说一句话），然后推进时间。"""
    w = World(seed)
    for _ in range(turns):
        if w.s.ended:
            break
        for d in w.defs.situations.values():  # 代替 world：条件成立的情境开场
            if (
                d.origin == "canon"
                and d.when is not None
                and not any(s.kind == d.id for s in w.s.situations.values())
            ):
                if predicates.holds(w.s, w.defs, d.when):
                    resolve.run_effects(w.s, w.defs, [{"open": [d.id]}], source="rule")
        cost = 1
        for actor in ACTORS:
            legal = actions.legal_actions(w.s, w.defs, actor, for_model=actor != "player")
            legal = [a for a in legal if "离开这一带" not in a.desc]  # 不走出地图，好多跑几回合
            if not legal:
                continue
            a = _choose(w, legal)
            sp = _speech(w, actor)
            if sp is not None and sp.act in speech.REPLY_ACTS and not sp.request:
                sp = None
            resolve.apply(w.s, w.defs, a, sp, source="player" if actor == "player" else "mind")  # type: ignore[arg-type]
            if actor == "player":
                cost = max(1, min(a.cost, 16))  # type: ignore[attr-defined]
        timeflow.advance_time(w.s, w.defs, cost)
        assert invariants.check(w.s, w.defs) == []
    return w


def test_same_seed_same_world() -> None:
    a = play(7, 60)
    b = play(7, 60)
    assert a.s.model_dump_json() == b.s.model_dump_json()
    c = play(8, 60)
    assert c.s.model_dump_json() != a.s.model_dump_json()


def test_random_play_keeps_invariants_without_rollbacks() -> None:
    for seed in range(4):
        w = play(seed, 120)
        rollbacks = [v for v in w.s.violations if "rollback" in v]
        assert rollbacks == [], rollbacks[:3]
        assert w.s.next_event_id > 100 or w.s.ended == "dead"


def test_apply_is_fast_after_500_events() -> None:
    w = World(3)
    from tianlong.kernel import events

    while w.s.next_event_id < 520:
        for cid in ACTORS:
            if w.c(cid).onstage:
                events.emit(
                    w.s,
                    w.defs,
                    actor=cid,
                    verb="x",
                    text=f"⟦{cid}⟧走动",
                    source="rule",
                    kinds=[events.kind("talk")],
                )
    assert all(len(w.c(c).memory) <= 200 for c in ACTORS)
    total = 0.0
    n = 0
    keep_alive = {"rest", "attack", "restrain", "use", "steal"}  # 让人一直醒着、活着，量的是寻常的结算
    for _ in range(60):
        for actor in ACTORS:
            legal = [
                a
                for a in actions.legal_actions(w.s, w.defs, actor)
                if a.verb not in keep_alive and "离开这一带" not in a.desc
            ]
            if not legal:
                continue
            a = _choose(w, legal)
            t0 = time.perf_counter()
            resolve.apply(w.s, w.defs, a, source="mind")  # type: ignore[arg-type]
            total += time.perf_counter() - t0
            n += 1
    avg_ms = total / n * 1000
    assert n >= 200
    assert avg_ms < 5.0, f"apply averages {avg_ms:.2f} ms"
