"""方案 6.4 的判定公式与 6.5 的冲突。"""

from __future__ import annotations

import pytest

from tianlong.kernel import checks, conflict, growth, rng
from tianlong.kernel.state import Status

from .conftest import World


@pytest.fixture
def dice(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    seq: list[int] = []

    def roll(state):  # type: ignore[no-untyped-def]
        state.rng_counter += 1
        return seq.pop(0) if seq else 50

    monkeypatch.setattr(rng, "roll", roll)
    return lambda *v: seq.__setitem__(slice(None), list(v))


def test_clamp() -> None:
    assert checks.clamp(120) == 95 and checks.clamp(-3) == 5 and checks.clamp(40) == 40


def test_flee_formula(w: World) -> None:
    p, f = w.c("player"), w.c("fighter")
    # 50 + 0 - 12 × (2 - 0 - 1) = 38
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 38
    assert checks.flee_chance(w.s, w.defs, p, [f], unguarded=True) == 53
    f.statuses.append(Status(kind="impeded"))
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 53  # 被牵制 +15
    f.statuses.clear()
    p.statuses.append(Status(kind="injured", level=2))
    p.statuses.append(Status(kind="exhausted"))
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 5  # 38 - 30 - 10 = -2，保底 5
    p.statuses = [Status(kind="injured", level=1)]
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 23
    p.statuses.clear()
    w.s.locations["hall"].terrain = 10
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 48
    p.arts["cloud_steps"] = 1
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 68  # 轻功 +20
    w.s.items["rope"].props = {"flee_mod": -5}
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 63  # 物品的 flee_mod
    w.s.items["rope"].props = {}
    # 追者高出 4 以上且没人牵制：封顶 10
    f.base_power = 5
    p.arts.clear()
    assert checks.flee_chance(w.s, w.defs, p, [f]) == 10
    f.statuses.append(Status(kind="impeded"))
    assert checks.flee_chance(w.s, w.defs, p, [f]) > 10


def test_hide_formula(w: World) -> None:
    p = w.c("player")
    assert checks.hide_chance(w.s, w.defs, p, 20, 0, False) == 65
    assert checks.hide_chance(w.s, w.defs, p, 20, 2, True) == 25
    w.s.tick = 0  # 子时：夜里 +15
    assert checks.hide_chance(w.s, w.defs, p, 20, 0, False) == 80


def test_steal_formula(w: World) -> None:
    p, g, f = w.c("player"), w.c("guest"), w.c("fighter")
    assert checks.steal_chance(w.s, w.defs, p, g) == 30
    g.statuses.append(Status(kind="asleep"))
    assert checks.steal_chance(w.s, w.defs, p, g) == 55
    g.statuses.clear()
    g.rel["player"].trust = 50
    assert checks.steal_chance(w.s, w.defs, p, g) == 40
    assert checks.steal_chance(w.s, w.defs, p, f) == 10  # 30 - 10 × 2


def test_climb_and_fall_formulas(w: World) -> None:
    p = w.c("player")
    assert checks.climb_chance(w.s, w.defs, p) == 75
    w.s.flags["rain"] = True
    assert checks.climb_chance(w.s, w.defs, p) == 65
    del w.s.flags["rain"]
    p.inventory.remove("rope")
    w.s.items["rope"].holder = None
    w.s.items["rope"].loc = "hall"
    assert checks.climb_chance(w.s, w.defs, p) == 30
    p.base_power = 2
    assert checks.climb_chance(w.s, w.defs, p) == 50
    assert checks.fall_survive_chance(w.s, w.defs, p, "cliff") == 35 + 10 + 20
    assert checks.fall_survive_chance(w.s, w.defs, p, "hall") == 45
    assert checks.fall_survive_chance(w.s, w.defs, w.c("guest"), "cliff") == 95  # 封顶


def test_death_save_has_no_floor(w: World) -> None:
    p = w.c("player")
    p.luck = 0
    assert checks.death_save_chance(p) == 0
    p.luck = 20
    assert checks.death_save_chance(p) == 40


def test_rolls_are_1_to_100(w: World) -> None:
    vals = {rng.roll(w.s) for _ in range(2000)}
    assert min(vals) == 1 and max(vals) == 100
    assert w.s.rng_counter == 2000


def test_pick_weighted_deterministic(w: World) -> None:
    a = [rng.pick_weighted(w.s, {"x": 1, "y": 3, "z": 0}) for _ in range(200)]
    assert set(a) == {"x", "y"} and a.count("y") > a.count("x")
    w2 = World()
    b = [rng.pick_weighted(w2.s, {"x": 1, "y": 3, "z": 0}) for _ in range(200)]
    assert a == b


# ---------------------------------------------------------------- 6.5


def test_stronger_wins_weaker_always_loses(w: World) -> None:
    f = conflict.fight(w.s, w.defs, w.c("player"), w.c("fighter"), "injure")
    assert f.winner == "fighter" and f.chance is None
    assert f.outcome == "push"  # 守方对玩家没有敌意：推开


def test_restrained_defender_loses(w: World) -> None:
    w.c("fighter").statuses.append(Status(kind="bound"))
    f = conflict.fight(w.s, w.defs, w.c("player"), w.c("fighter"), "injure")
    assert f.winner == "player" and f.outcome == "injure"


def test_equal_power_helpers_and_items(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    p, g = w.c("player"), w.c("guest")
    dice(50)
    f = conflict.fight(w.s, w.defs, p, g, "injure")
    assert f.chance == 50 and f.winner == "player"
    w.fx([{"rel": ["fighter", "guest", "affinity", -60]}])  # 雷猛与书生为敌：算玩家的帮手
    dice(60)
    f = conflict.fight(w.s, w.defs, p, g, "injure")
    assert f.chance == 60 and f.winner == "player"
    g.statuses.append(Status(kind="impeded"))
    assert conflict.fight(w.s, w.defs, p, g, "injure").chance == 70
    # 物品的 power_mod 加在 power 上
    w.fx([{"give": ["fighter", "guest", "sword"]}])
    f = conflict.fight(w.s, w.defs, p, g, "injure")
    assert f.winner == "guest" and f.defender_power == 1


def test_kill_intent_death_save_and_buffer(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    f, g = w.c("fighter"), w.c("guest")
    dice(1)  # 侥幸：书生气运 20，成功率 40
    r = conflict.fight(w.s, w.defs, f, g, "kill")
    assert r.outcome == "saved"
    effs = conflict.outcome_effects(r)
    assert {"injure": ["guest", 3, "fighter"]} in effs
    dice(99)
    r = conflict.fight(w.s, w.defs, f, g, "kill")
    assert r.outcome == "kill"
    dice(99)
    r = conflict.fight(w.s, w.defs, f, w.c("player"), "kill")
    assert r.outcome == "buffered"  # 对玩家：同一来源第一次降级


def test_evade_and_absorb(w: World) -> None:
    g = w.c("guest")
    g.arts["cloud_steps"] = 2  # 小成：evade
    r = conflict.fight(w.s, w.defs, w.c("girl"), g, "injure")
    assert r.winner == "girl" and r.outcome == "push"
    r = conflict.fight(w.s, w.defs, w.c("fighter"), g, "injure")
    assert r.outcome == "injure"  # 带剑高出 3 级：躲不开
    w.fx([{"give": ["fighter", "hall", "sword"]}])
    r = conflict.fight(w.s, w.defs, w.c("fighter"), g, "injure")
    assert r.outcome == "push"  # 高出不到 3：至多被逼退
    # 吸取内力
    from tianlong.kernel.defs import ArtDef, ArtStage

    w.defs = w.defs.model_copy(deep=True)
    w.defs.arts["sucker"] = ArtDef(
        id="sucker", name="吸功", kind="inner", stages=[ArtStage(name="一", hours=1, effects={"absorb": 20})]
    )
    g.arts["sucker"] = 1
    r = conflict.fight(w.s, w.defs, w.c("fighter"), g, "injure")
    assert r.absorbed == 10 and r.absorber == "guest"
    w.fx(conflict.outcome_effects(r))
    assert g.neili == 10 and w.c("fighter").neili == 0


def test_defender_wins_and_uses_own_intent(w: World) -> None:
    w.fx([{"rel": ["fighter", "player", "affinity", -60]}])
    r = conflict.fight(w.s, w.defs, w.c("player"), w.c("fighter"), "injure")
    assert r.winner == "fighter" and r.outcome == "injure"
    w.c("fighter").agenda.steps[0].do = {"verb": "attack", "target": "player", "intent": "kill"}
    assert conflict.defender_intent(w.s, w.c("fighter"), w.c("player")) == "kill"


def test_power_uses_growth(w: World) -> None:
    assert growth.power(w.s, w.defs, "fighter") == 2
    assert conflict.effective_power(w.s, w.defs, w.c("fighter")) == 3  # 长剑 power_mod 1


def test_lucky_flag_makes_checks_succeed(w):
    from tianlong.kernel import checks

    w.s.flags[checks.LUCKY_FLAG] = True
    assert all(checks.roll_against(w.s, "flee", 5).ok for _ in range(30))
