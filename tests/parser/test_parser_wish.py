"""/心愿 → wish 牵挂（方案 8.4）。"""

from __future__ import annotations

from typing import Any

from tianlong import parser
from tianlong.kernel import effects
from tianlong.llm import JevError

from .conftest import Scene


async def test_wish_maps_to_people_and_arts(scene: Scene) -> None:
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
    w = await parser.map_wish(scene.llm, scene.state, scene.defs, "找到钟灵（她在梁上）")
    assert w["refs"] == ["zhong_ling"]
    assert "梁上" not in w["text"]


async def test_wish_without_llm_and_without_matches(scene: Scene) -> None:
    w = await parser.map_wish(None, scene.state, scene.defs, "发一笔大财")
    assert w == {"kind": "wish", "text": "发一笔大财", "refs": [], "weight": 3}
    w = await parser.map_wish(None, scene.state, scene.defs, "")
    assert w["text"] and w["refs"] == []
