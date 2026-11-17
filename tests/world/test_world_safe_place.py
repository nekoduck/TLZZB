"""P6：两个互相敌视的人各自要躲时不再挑中同一处（原来一同躲到最近的地方，到了又互相躲回来，来回几日）。"""

from __future__ import annotations

from tianlong.world import goals

from .conftest import fx

A, B = "gan_guanghao", "zuo_zimu"  # id 小的先挑
START = "ferry_bridge"


def _feud(state, defs, loc: str = START) -> None:
    fx(state, defs, [
        {"move": [A, loc]},
        {"move": [B, loc]},
        {"rel": [A, B, "affinity", -80]},
        {"rel": [B, A, "affinity", -80]},
    ])
    for cid in (A, B):
        state.chars[cid].agenda = None
    assert state.chars[A].loc == state.chars[B].loc == loc


def test_mutual_foes_fleeing_at_once_pick_different_places(world):
    defs, state = world
    _feud(state, defs)
    a, b = state.chars[A], state.chars[B]
    pa = goals._safe_place(state, defs, a, avoid=B)
    pb = goals._safe_place(state, defs, b, avoid=A)
    assert pa is not None and pb is not None
    assert pa != pb, (pa, pb)
    assert START not in (pa, pb)
    # 确定性：同一局面再问一遍，答案不变
    assert goals._safe_place(state, defs, a, avoid=B) == pa
    assert goals._safe_place(state, defs, b, avoid=A) == pb
    # id 小的那个照旧取他最近的去处；让路的是另一个
    assert goals._safe_place(state, defs, a, avoid=B, _yield=False) == pa


def test_the_place_a_foe_is_heading_for_is_avoided(world):
    defs, state = world
    _feud(state, defs)
    a, b = state.chars[A], state.chars[B]
    pb = goals._safe_place(state, defs, b, avoid=A)
    first = goals._safe_place(state, defs, a, avoid=B)
    # 对头已经定了要去某处（打算里这一步的 at）：不往那里躲
    step = {"id": "t_run", "text": "躲一躲", "at": first, "do": {"verb": "wait"}, "takes": 8}
    fx(state, defs, [{"step": [B, step]}])
    assert goals._heading(state, b) == first
    second = goals._safe_place(state, defs, a, avoid=B)
    assert second is not None and second != first
    assert pb is not None


def test_after_parting_they_do_not_run_back_together(world):
    """各自躲到了不同的地方之后，眼前没有对头：谁也不必再躲，更不会一同折回原处。"""
    defs, state = world
    _feud(state, defs)
    a, b = state.chars[A], state.chars[B]
    pa = goals._safe_place(state, defs, a, avoid=B)
    pb = goals._safe_place(state, defs, b, avoid=A)
    fx(state, defs, [{"move": [A, pa]}, {"move": [B, pb]}])
    assert a.loc != b.loc
    from tianlong.kernel import danger

    assert not danger.hostiles_toward(state, A) and not danger.hostiles_toward(state, B)


def test_ties_are_broken_per_character_not_by_place_id(world):
    defs, state = world
    lids = sorted(state.locations)[:6]
    orders = {cid: sorted(lids, key=lambda lid, cid=cid: goals._tie(cid, lid)) for cid in (A, B, "duan_yu", "ma_wude")}
    assert len({tuple(o) for o in orders.values()}) > 1  # 各人的取舍不同
    assert goals._tie(A, lids[0]) == goals._tie(A, lids[0])  # 只看 id，可重放
