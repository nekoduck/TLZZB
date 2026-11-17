"""真实 content/ 上的回归（wave 3 的 K1、K2、K3、K9、K10）：底线、请求裁决、同门、看不见的东西。

只断言规则必然的结果，不依赖剧情的时间表。
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from tianlong.content.loader import load
from tianlong.content.validate import validate
from tianlong.kernel import actions, effects, mood, speech
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, Request, WorldState

CONTENT = Path(__file__).resolve().parents[2] / "content"


@cache
def _base() -> tuple[Defs, WorldState]:
    return load(CONTENT)


def world() -> tuple[Defs, WorldState]:
    defs, st = _base()
    return defs, st.model_copy(deep=True)


def fx(st: WorldState, defs: Defs, effs: list[dict]) -> None:
    effects.apply_effects(st, defs, effs, Ctx(), source="rule")


def sigs(st: WorldState, defs: Defs, actor: str) -> set[str]:
    return {a.signature for a in actions.legal_actions(st, defs, actor)}


def attack(target: str, intent: str) -> str:
    return actions.signature("attack", {"target": target, "intent": intent})


def verdict(st: WorldState, defs: Defs, frm: str, to: str, verb: str, **params: str) -> str:
    r = Request(id="Rx", frm=frm, to=to, action={"verb": verb, "params": params}, action_class="effort")
    speech.score_request(st, defs, r)
    return r.verdict


def test_content_still_validates() -> None:
    defs, st = world()
    assert validate(defs, st) == []


def test_no_kill_bottom_line_leaves_ordinary_attacks() -> None:
    """K1：「不杀人」「不跟人拼命」只挡下杀手，开局时照样可以寻常动手。"""
    defs, st = world()
    for cid in ("duan_yu", "ma_wude"):
        mine = sigs(st, defs, cid)
        assert attack("gong_guangjie", "injure") in mine, cid
        assert attack("player", "injure") in mine, cid
        assert not any(s.startswith("attack:intent=kill") for s in mine), cid


def test_eshen_spares_the_powerless_only_from_death() -> None:
    """K1：不杀无力还手的人——可以伤他，不能杀他；有身手的人照杀。"""
    defs, st = world()
    fx(st, defs, [{"enter": ["nanhai_eshen", "jianhu_hall"]}])
    mine = sigs(st, defs, "nanhai_eshen")
    assert attack("player", "injure") in mine and attack("duan_yu", "injure") in mine
    assert attack("player", "kill") not in mine and attack("duan_yu", "kill") not in mine
    assert attack("zuo_zimu", "kill") in mine and attack("zuo_zimu", "injure") in mine


def test_other_bottom_lines_still_bite() -> None:
    defs, st = world()
    fx(st, defs, [{"enter": ["zhong_wanchou", "jianhu_hall"]}, {"enter": ["gan_baobao", "jianhu_hall"]}])
    mine = sigs(st, defs, "zhong_wanchou")
    assert attack("gan_baobao", "injure") not in mine and attack("gan_baobao", "kill") not in mine
    assert attack("duan_yu", "injure") in mine
    # 段誉不拜师学打人的功夫：学剑不在清单里（哪怕有人肯教）
    assert not any(s.startswith("study:art=wuliang_sword") for s in sigs(st, defs, "duan_yu"))


def test_teach_request_verdicts() -> None:
    """K2：本门剑法不传外人是 never；别的本事、传给本门弟子都不撞底线。"""
    defs, st = world()
    assert verdict(st, defs, "player", "zuo_zimu", "teach", art="wuliang_sword", target="player") == "never"
    assert verdict(st, defs, "player", "zuo_zimu", "teach", art="wuliang_sword") == "never"  # 没写对象：教我
    assert verdict(st, defs, "player", "zuo_zimu", "teach", art="yijing", target="player") != "never"
    assert verdict(st, defs, "gong_guangjie", "zuo_zimu", "teach", art="wuliang_sword",
                   target="gong_guangjie") != "never"


def test_attack_request_verdicts() -> None:
    """K2：请段誉出手教训人不是 never；请他杀人才是。"""
    defs, st = world()
    assert verdict(st, defs, "player", "duan_yu", "attack", target="gong_guangjie", intent="injure") != "never"
    assert verdict(st, defs, "player", "duan_yu", "attack", target="gong_guangjie") != "never"
    assert verdict(st, defs, "player", "duan_yu", "attack", target="gong_guangjie", intent="kill") == "never"
    # 说话的底线：家世不对外人说
    fact = next(fid for fid, f in st.facts.items() if "duan_family" in f.tags)
    assert verdict(st, defs, "player", "duan_yu", "say", act="tell", fact=fact) == "never"
    assert verdict(st, defs, "player", "duan_yu", "say", act="tell") != "never"


def test_fellow_guests_are_not_same_sect() -> None:
    """K3：同是来客不算同门；同一门派、同一家才把关系抬到相熟。"""
    defs, st = world()
    p, dy, mw = st.chars["player"], st.chars["duan_yu"], st.chars["ma_wude"]
    assert set(p.roles) & set(dy.roles)  # 都是来客
    assert not mood.same_sect(defs, p, dy, st)
    assert not mood.same_sect(defs, dy, mw, st)
    assert mood.same_sect(defs, st.chars["zuo_zimu"], st.chars["gong_guangjie"], st)
    assert mood.same_sect(defs, st.chars["gan_baobao"], st.chars["zhong_ling"], st)
    # 段誉与马五德互不相识：仍是陌生
    assert mood.band(defs, st, "duan_yu", "ma_wude") == "stranger"
    # 主仆、师徒仍然算数：伙计与东家
    assert mood.close_roles(defs, p, mw, st)


def test_unseen_carried_items_cannot_be_stolen() -> None:
    """K9：别人怀里的东西，没看见过就不在清单里；细看过那人之后才有。"""
    defs, st = world()
    carried = [
        (it.id, it.holder)
        for it in st.items.values()
        if it.in_world and it.holder and it.holder != "player" and st.chars[it.holder].loc == "jianhu_hall"
        and not it.props.get("visible") and "mount" not in it.tags
    ]
    assert carried
    mine = sigs(st, defs, "player")
    for iid, holder in carried:
        assert actions.signature("steal", {"item": iid, "target": holder}) not in mine
    iid, holder = next((i, h) for i, h in carried if "hidden" not in {s.kind for s in st.chars[h].statuses})
    fx(st, defs, [{"mark": ["examined", f"char:{holder}"]}])
    assert actions.signature("steal", {"item": iid, "target": holder}) in sigs(st, defs, "player")


def test_edge_exit_reads_cleanly() -> None:
    """K10：通往图外的出口，描述里不露原始 id。"""
    defs, st = world()
    seen = 0
    for lid, loc in st.locations.items():
        for e in loc.exits:
            if not e.edge:
                continue
            fx(st, defs, [{"move": ["player", lid]}])
            a = actions.build(st, defs, "player", "go", {"exit": e.to})
            if a is None:
                continue
            seen += 1
            assert a.desc.startswith("离开这一带")
            from tianlong.kernel import refs

            assert e.to not in refs.render(a.desc, st, defs, "player")
    assert seen >= 1
