"""方案 5.2：每一条谓词。"""

from __future__ import annotations

import pytest

from tianlong.kernel.predicates import PREDICATE_SHAPES, PredicateError, holds
from tianlong.kernel.state import Agenda, Ctx, Request, SituationState, Step

from .conftest import World


def H(w: World, p: object, **ctx: object) -> bool:
    return holds(w.s, w.defs, p, Ctx(**ctx))  # type: ignore[arg-type]


def test_literals_and_combinators(w: World) -> None:
    assert H(w, None) and H(w, True) and H(w, "true") and not H(w, False) and not H(w, "false")
    assert H(w, {"all": [True, {"alive": "player"}]})
    assert not H(w, {"all": [True, False]})
    assert H(w, {"any": [False, True]})
    assert not H(w, {"any": []})
    assert H(w, {"not": False})
    with pytest.raises(PredicateError):
        H(w, {"nonsense": 1})
    with pytest.raises(PredicateError):
        H(w, {"at": ["player"]})


def test_position(w: World) -> None:
    assert H(w, {"at": ["player", "hall"]})
    assert H(w, {"at": ["self", "hall"]}, self_id="fighter")
    assert H(w, {"at": ["$v", "hall"]}, roles={"v": "guest"})
    assert not H(w, {"at": ["$unbound", "hall"]})
    assert H(w, {"at": ["player", "guest"]})  # 地点参数可以写人：取他所在的地点
    assert H(w, {"same_loc": ["player", "fighter"]})
    w.c("fighter").loc = "yard"
    assert not H(w, {"same_loc": ["player", "fighter"]})
    assert H(w, {"onstage": "fighter"})
    w.c("fighter").onstage = False
    assert not H(w, {"onstage": "fighter"})
    assert not H(w, {"at": ["fighter", "yard"]})  # 离了图就不在任何地点


def test_items(w: World) -> None:
    assert H(w, {"has": ["player", "rope"]})
    assert not H(w, {"has": ["guest", "rope"]})
    assert H(w, {"item_at": ["old_manual", "yard"]})  # 含藏在景物里
    assert not H(w, {"item_at": ["rope", "hall"]})  # 拿在手里不算「在某地」
    assert not H(w, {"item_at": ["lucky_charm", "hall"]})


def test_knows_and_rel(w: World) -> None:
    assert H(w, {"knows": ["player", "F_bout"]})
    assert not H(w, {"knows": ["player", "F_girl_on_beam"]})
    from tianlong.kernel.state import Belief

    w.c("player").beliefs["F_girl_on_beam"] = Belief(confidence=0.5, source="told:guest", tick=0)
    assert not H(w, {"knows": ["player", "F_girl_on_beam"]})  # 低于 0.6 不算知道
    assert H(w, {"rel": ["player", "guest", "trust", ">=", 10]})
    assert not H(w, {"rel": ["player", "guest", "trust", ">=", 11]})
    assert H(w, {"rel": ["player", "fighter", "fear", "<=", 0]})  # 没写的按陌生人
    with pytest.raises(PredicateError):
        H(w, {"rel": ["player", "guest", "love", ">=", 1]})


def test_friend_and_others_here(w: World) -> None:
    assert H(w, {"friend_here": ["player", 15]})  # 玩家对白公子好感 15
    assert not H(w, {"friend_here": ["player", 30]})
    assert H(w, {"others_here": ["player", 3]})  # 躲着的少女也算在场
    assert not H(w, {"others_here": ["player", 4]})


def test_flags_with_local(w: World) -> None:
    assert H(w, {"flag": "bout_running"})
    assert H(w, {"flag": ["bout_running", True]})
    assert not H(w, {"flag": ["bout_running", False]})
    assert not H(w, {"flag": "missing"})
    w.s.flags["sit#1.done"] = True
    assert H(w, {"flag": ".done"}, situation="sit#1")
    assert not H(w, {"flag": ".done"}, situation="sit#2")


def test_status_alive_role_art_stat(w: World) -> None:
    assert H(w, {"status": ["girl", "hidden"]})
    assert not H(w, {"status": ["player", "hidden"]})
    assert H(w, {"alive": "guest"})
    assert not H(w, {"alive": "nobody"})
    assert H(w, {"role": ["fighter", "hall_sect"]})
    assert H(w, {"art": ["fighter", "hall_sword", 1]})
    assert not H(w, {"art": ["fighter", "hall_sword", 2]})
    assert H(w, {"stat": ["player", "literacy", ">=", 1]})
    assert H(w, {"stat": ["fighter", "power", ">=", 2]})
    assert H(w, {"stat": ["guest", "luck", "==", 20]})
    assert H(w, {"stat": ["player", "neili", "<=", 0]})


def test_time_step_situation_request(w: World) -> None:
    assert H(w, {"time_ge": 32}) and H(w, {"time_le": 32})
    assert not H(w, {"time_ge": 33})
    assert H(w, {"step": ["fighter", "f_watch", "pending"]})
    assert not H(w, {"step": ["fighter", "f_watch", "done"]})
    assert not H(w, {"step": ["player", "x", "pending"]})
    w.c("player").agenda = Agenda(
        goal="g", steps=[Step(id="p1", text="t", do={"verb": "wait"}, state="void")]
    )
    assert H(w, {"step": ["player", "p1", "void"]})
    assert not H(w, {"situation": "bout_laugh"})
    w.s.situations["bout_laugh#1"] = SituationState(
        id="bout_laugh#1", kind="bout_laugh", loc="hall", roles={}
    )
    assert H(w, {"situation": "bout_laugh"})
    w.s.requests["R1"] = Request(
        id="R1", frm="player", to="guest", action={"verb": "wait"}, action_class="trivial"
    )
    assert H(w, {"request": ["R1", "open"]})
    assert not H(w, {"request": ["R1", "agreed"]})


def test_every_predicate_key_is_tested() -> None:
    import inspect

    src = inspect.getsource(inspect.getmodule(test_every_predicate_key_is_tested))  # type: ignore[arg-type]
    for key in PREDICATE_SHAPES:
        assert f"'{key}'" in src or f'"{key}"' in src, key
