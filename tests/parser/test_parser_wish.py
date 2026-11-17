"""/心愿 → wish 牵挂（方案 8.4）。"""

from __future__ import annotations

from typing import Any

from tianlong import parser
from tianlong.kernel import effects, perception, refs
from tianlong.llm import JevError

from .conftest import Scene


async def test_wish_maps_to_people_and_arts(scene: Scene) -> None:
    effects.apply_effects(scene.state, scene.defs, [{"know_term": "wuliang_sword"}], source="rule")  # 听说过这门剑法
    w = await parser.map_wish(scene.llm, scene.state, scene.defs, "我想拜左掌门为师，学会无量剑法")
    assert w["kind"] == "wish" and w["weight"] == 3
    assert "zuo_zimu" in w["refs"] and "wuliang_sword" in w["refs"]
    assert w["text"] == "我想拜左掌门为师，学会无量剑法"
    effects.apply_effects(scene.state, scene.defs, [{"thread": w}], source="rule")
    t = next(t for t in scene.state.threads.values() if t.kind == "wish")
    assert t.refs == w["refs"] and not t.closed


async def test_wish_falls_back_to_name_matching(scene: Scene) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise JevError("down")

    scene.llm.jev.ask = boom  # type: ignore[method-assign]
    effects.apply_effects(scene.state, scene.defs, [{"know_term": "zhong_ling"}], source="rule")  # 听人说起过这个名字
    w = await parser.map_wish(scene.llm, scene.state, scene.defs, "找到钟灵（她在梁上）")
    assert w["refs"] == ["zhong_ling"]
    assert "梁上" not in w["text"]


async def test_wish_without_llm_and_without_matches(scene: Scene) -> None:
    w = await parser.map_wish(None, scene.state, scene.defs, "发一笔大财")
    assert w == {"kind": "wish", "text": "发一笔大财", "refs": [], "weight": 3}
    w = await parser.map_wish(None, scene.state, scene.defs, "")
    assert w["text"] and w["refs"] == []


async def test_wish_does_not_bind_what_the_character_cannot_refer_to(scene: Scene) -> None:
    """2.3、8.4：玩家从书里知道、角色没见过没听说过的名字，心愿照记，但不对到任何人和物上。"""
    from tianlong.parser import wish as wish_mod

    st, df = scene.state, scene.defs
    girl = st.chars["zhong_ling"]
    assert girl.loc == st.chars[st.player].loc and "zhong_ling" not in st.known_terms  # 就躲在梁上，他不知道
    for text in ("我要找到钟灵", "我要去谷底石洞", "我要拿到闪电貂", "我要找到梁上那少女", "想去看看一个石洞"):
        assert not perception.referable(st)("zhong_ling")
        cands = wish_mod.wish_candidates(st, df, text)
        assert not {"zhong_ling", "valley_cave", "lightning_marten"} & set(cands), text
        for llm in (None, scene.llm):  # 按名字的退路、Jev 的路都一样
            w = await parser.map_wish(llm, st, df, text)
            assert w["text"] == text and w["refs"] == [], (text, w)
    # 没听说过的本事、只是名字沾边的人，也不捎带上
    w = await parser.map_wish(None, st, df, "我想拜左掌门为师，学会无量剑法")
    assert w["refs"] == ["zuo_zimu"]


async def test_wish_binds_a_visible_stranger_only_by_what_the_character_calls_him(scene: Scene) -> None:
    """眼前那个还不知名的人：写他的真名对不上（角色不知道那是他），照角色眼里的叫法写才对得上。"""
    st, df = scene.state, scene.defs
    assert "duan_yu" not in st.known_terms
    shown = refs.display_name(st, df, "duan_yu", st.player)
    assert shown != st.chars["duan_yu"].name
    w = await parser.map_wish(None, st, df, "我想结识" + st.chars["duan_yu"].name)
    assert w["refs"] == []
    w = await parser.map_wish(None, st, df, "我想结识" + shown)
    assert w["refs"] == ["duan_yu"]


async def test_wish_binding_follows_the_kernel_referable_rule(scene: Scene) -> None:
    """心愿的候选只用内核那一份判断（``perception.referable``）：按名字对得上、角色却说不出的不进候选，知道名字后才进。"""
    from tianlong.parser import wish as wish_mod

    st, df = scene.state, scene.defs
    text = "我要找到钟灵，去谷底石洞看看"
    assert not {"zhong_ling", "valley_cave"} & set(wish_mod.wish_candidates(st, df, text))
    effects.apply_effects(st, df, [{"know_term": "zhong_ling"}], source="rule")
    ok = perception.referable(st)
    assert ok("zhong_ling") and not ok("valley_cave")
    cands = wish_mod.wish_candidates(st, df, text)
    assert "zhong_ling" in cands and "valley_cave" not in cands
    assert all(ok(e) or e in df.arts for e in cands)

