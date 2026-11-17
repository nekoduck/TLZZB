"""复查 D-347 到 D-366 后的回归修补（内核一侧）：被追上就是追者出了这一手、闯出口只和拦着的人较量。"""

from __future__ import annotations

import pytest

from tianlong.kernel import actions, checks, danger, rng
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


def _hunter(w: World, verb: str = "attack", intent: str | None = "kill", target: str = "player") -> None:
    f = w.c("fighter")
    f.base_power = 6
    do = {"verb": verb, "target": target}
    if intent:
        do["intent"] = intent
    f.agenda = Agenda(
        goal="k",
        steps=[Step(id="k1", text="k", do=do), Step(id="k2", text="w", do={"verb": "wait"}, takes=8)],
    )


# ---------------------------------------------------------------- 1. 被追上：追者这一手已经出过了


@pytest.mark.parametrize("verb", ["go", "flee"])
def test_caught_by_killer_spends_his_strike(w: World, dice, verb: str) -> None:  # type: ignore[no-untyped-def]
    """走不脱被追上，下场按追者的打算取（6.4）：那就是他这一步的出手，不能在同一回合里再来一次。"""
    _hunter(w)
    dice(100, 100)  # 逃不脱；侥幸也没有
    evs = w.act("player", verb, exit="yard")
    assert evs[-1].outcome == "fail" and evs[-1].params["outcome"] == "buffered"
    assert is_alive(w.c("player")) and actions.injury_level(w.c("player")) == 3
    f = w.c("fighter")
    assert f.agenda.steps[0].state == "done"  # 这一手出过了
    assert not danger.intends_kill(w.s, f, "player") and not danger.blockers(w.s, "player")
    # 他若再要下手，得是另起的打算：那才是 6.10 说的「再犯」
    assert not w.has("fighter", "wait", ticks=1) or danger.current_step(f).id == "k2"


def test_caught_by_captor_spends_his_step(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    _hunter(w, verb="restrain", intent=None, target="guest")
    dice(100)
    evs = w.act("guest", "go", exit="yard")
    assert evs[-1].params["outcome"] == "restrain" and w.status("guest", "bound") is not None
    assert w.c("fighter").agenda.steps[0].state == "done"


def test_caught_by_mere_blocker_leaves_agenda_alone(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    """只是挡着的人（没有冲他来的打算）把人拦回去：他自己的那一步不动。"""
    g = w.c("guest")
    g.agenda = Agenda(goal="g", steps=[Step(id="g1", text="w", do={"verb": "wait"}, takes=8)])
    w.act("guest", "block", target="player")
    dice(100)
    evs = w.act("player", "go", exit="yard")
    assert evs[-1].params["winner"] == "guest" and evs[-1].params["outcome"] == "push"
    assert g.agenda.steps[0].state == "pending"


# ---------------------------------------------------------------- 4. 闯出口只和拦着的人较量


def test_contested_go_ignores_bound_enemy_in_the_room(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    """D-347：拦着的人只认 ``danger.blockers``。屋里捆着的仇家既不压低成功率，也不是把人拦下的那个。"""
    _rel(w, "fighter", "player").affinity = -50
    w.c("fighter").base_power = 6
    w.c("fighter").statuses.append(Status(kind="bound", source="guest"))
    w.act("guest", "block", target="player")
    assert [o.id for o in danger.blockers(w.s, "player")] == ["guest"]
    want = checks.flee_chance(w.s, w.defs, w.c("player"), danger.blockers(w.s, "player"))
    assert want == 62
    dice(100)
    evs = w.act("player", "go", exit="yard")
    assert evs[-1].outcome == "fail"
    assert evs[-1].params["chance"] == want and evs[-1].params["winner"] == "guest"
    assert evs[-1].params["outcome"] == "push" and w.status("player", "injured") is None


def test_contested_go_ignores_idle_enemy_in_the_room(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    """仇家没被捆着、可眼下并没有冲他来的打算：同样不算拦着的人。"""
    _rel(w, "fighter", "player").affinity = -50
    w.c("fighter").base_power = 6
    w.act("guest", "block", target="player")
    dice(100)
    evs = w.act("player", "go", exit="yard")
    assert evs[-1].params["chance"] == 62 and evs[-1].params["winner"] == "guest"
    assert w.status("player", "injured") is None


def test_flee_ignores_bound_or_unseeing_pursuer(w: World, dice) -> None:  # type: ignore[no-untyped-def]
    """逃（flee）的追者同此：被捆着的、看不见他的人追不上来。"""
    _rel(w, "fighter", "player").affinity = -50
    w.c("fighter").base_power = 6
    w.c("fighter").statuses.append(Status(kind="bound", source="guest"))
    assert w.has("player", "flee", exit="yard")  # 有敌意的人在场：照旧可以逃
    dice(100)
    evs = w.act("player", "flee", exit="yard")
    assert evs[-1].outcome == "fail" and "winner" not in evs[-1].params  # 没人追：只是没逃开
    assert w.status("player", "injured") is None
