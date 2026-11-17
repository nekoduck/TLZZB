"""「由我来」（方案 3.4 例三）：帮主中了毒、正要找人去取药时，玩家可以请缨。

候选是内容里的情境定义给的（poison_errand.yaml 的 volunteer 一节）；所求写成对方等一等，带 volunteer 说明，
内核照常裁决，对方答应了玩家就得到那个身份。
"""

from __future__ import annotations

from tianlong import parser
from tianlong.kernel import speech

from .conftest import Scene

LINE = "我对司空玄说：让我替段公子去万劫谷取解药，我愿意服下那包药"


def bitten(camp: Scene) -> Scene:
    """帮主中了毒，也知道解药在谁家：逼人取药的事眼下就要发生。"""
    camp.fx([{"learn": ["sikong_xuan", "F_diao_antidote_father", 1.0, "told"]}])
    return camp


async def test_no_volunteer_candidate_before_the_need(camp: Scene) -> None:
    cands = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", limit=50)
    assert not [c for c in cands if c.action.get("volunteer")]
    # 那时「替他」只有一个意思：放了他、换我（原来的候选还在）
    swap = [c for c in cands if c.type == "take_place_of"]
    assert swap and swap[0].action["verb"] == "release" and swap[0].action["swap"] == "player"


async def test_volunteer_candidate_when_the_chief_needs_a_messenger(camp: Scene) -> None:
    bitten(camp)
    cands = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", limit=50)
    vol = [c for c in cands if c.action.get("volunteer")]
    assert len(vol) == 1
    c = vol[0]
    assert c.type == "take_place_of" and c.slots == {"target": "duan_yu"} and c.cls == "effort"
    assert c.action["verb"] == "wait" and c.action["volunteer"] == {"situation": "poison_errand", "role": "messenger"}
    assert "⟦player⟧" in c.desc and "⟦duan_yu⟧" in c.desc
    assert speech.volunteer_spec(camp.state, camp.defs, "sikong_xuan", c.action) is not None
    # 「替段公子」已经有了着落：不再另列「放了他、换我」这一条（同类同对象只留一条）
    assert len([x for x in cands if x.type == "take_place_of" and x.slots.get("target") == "duan_yu"]) == 1
    # 这句话对上的就是它（没提段公子、只说「让我去」也对得上）
    ranked = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", text=LINE)
    assert ranked[0].action.get("volunteer")
    plain = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", text="帮主，让我去取解药吧")
    assert plain[0].action.get("volunteer")
    # 已经应下的人不再列这一条
    camp.fx([{"role": ["player", "errand_volunteer", "add"]}])
    again = parser.request_candidates(camp.state, camp.defs, "player", "sikong_xuan", limit=50)
    assert not [x for x in again if x.action.get("volunteer")]


async def test_volunteering_goes_through_the_kernel_and_grants_the_role(camp: Scene) -> None:
    bitten(camp)
    b, evs = await camp.play(LINE)
    assert b.speech is not None and b.speech.act == "request" and b.speech.ask_action is not None
    assert b.speech.ask_action.get("volunteer") == {"situation": "poison_errand", "role": "messenger"}
    r = next(r for r in camp.state.requests.values() if r.frm == "player" and r.to == "sikong_xuan")
    # 费事 × 陌生 30 分 + 正好帮到急需 30 分：可以答应，也可以不答应。不撞「解药没到手之前不放人质」的底线
    assert r.action_class == "effort" and r.leverage == 30 and r.verdict == "may", r
    assert "errand_volunteer" not in camp.state.chars["player"].roles  # 还没答应
    reply = speech.Speech(act="agree", to=["player"], request=r.id)
    from tianlong.kernel import actions, resolve

    wait = actions.build(camp.state, camp.defs, "sikong_xuan", "wait", {"ticks": 1})
    resolve.apply(camp.state, camp.defs, wait, reply, source="mind")
    assert r.status == "agreed" and "errand_volunteer" in camp.state.chars["player"].roles
