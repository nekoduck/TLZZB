"""记忆检索（方案 5.6）与 J2 状态、人物卡块的渲染（方案 7.3、7.6）。"""

from __future__ import annotations

import pytest

from tianlong.kernel.state import MemoryItem, Promise
from tianlong.minds import memory as mmem
from tianlong.minds import render

from .conftest import open_bout


def _m(tick: int, sal: int, ents: list[str], text: str = "", kind: str = "saw", event: int | None = None):
    return MemoryItem(tick=tick, kind=kind, text=text or f"m{tick}", entities=ents, salience=sal, event=event)


def test_score_formula():
    m = _m(tick=0, sal=6, ents=["a", "b", "x"])
    now = 16  # 两个时辰
    got = mmem.score(m, now, {"a", "b", "c", "d"})
    assert got == pytest.approx(0.98**2 + 0.6 + 2 * 2 / 4)
    assert mmem.score(m, now, set()) == pytest.approx(0.98**2 + 0.6)


def test_retrieve_ranks_by_recency_importance_relevance(world):
    defs, state = world
    c = state.chars["gong_guangjie"]
    now = state.tick
    c.memory = [
        _m(now - 96, 2, ["nobody"], "old trivial"),  # 旧、轻、无关
        _m(now - 8, 9, ["zz"], "recent grave"),  # 新、重
        _m(now - 80, 3, ["duan_yu", "jianhu_hall"], "relevant"),  # 旧、轻、相关
        _m(now, 1, [], "just now"),  # 最新、最轻
    ]
    got = mmem.retrieve(state, "gong_guangjie", ["duan_yu", "jianhu_hall"], k=3)
    texts = [m.text for m in got]
    assert texts[0] == "relevant"  # 2 × 2/2 = 2 的相关分压过一切
    assert set(texts) == {"relevant", "recent grave", "just now"}
    assert "old trivial" not in texts
    # 排除本轮的事件
    c.memory.append(_m(now, 10, ["duan_yu"], "this round", event=999))
    got = mmem.retrieve(state, "gong_guangjie", ["duan_yu"], k=6, exclude_events=[999])
    assert "this round" not in [m.text for m in got]


def test_retrieve_must_carry_reflection_and_promise(world):
    defs, state = world
    c = state.chars["gong_guangjie"]
    now = state.tick
    c.memory = [_m(now - 200, 1, ["duan_yu"], "老的反思", kind="reflection")]
    c.memory += [_m(now - i, 9, ["zz"], f"filler{i}") for i in range(10)]
    c.memory.append(_m(now - 150, 1, ["duan_yu"], "新的反思", kind="reflection"))
    state.promises["P9"] = Promise(id="P9", frm="gong_guangjie", to="zuo_zimu", text="比完剑去后山练功")
    got = mmem.retrieve(state, "gong_guangjie", ["zz"], k=2)
    texts = [m.text for m in got]
    assert texts[0] == "新的反思"  # 关于在场者的最新一条反思，必带
    assert "老的反思" not in texts
    assert any("比完剑去后山练功" in t for t in texts)  # 自己未了的承诺，必带
    assert len(got) == 2 + 2


def test_new_since_reflection(world):
    defs, state = world
    c = state.chars["duan_yu"]
    c.memory = [_m(1, 3, []), _m(2, 3, []), _m(3, 3, [], kind="reflection"), _m(4, 3, []), _m(5, 3, [])]
    assert [m.tick for m in mmem.new_since_reflection(c)] == [4, 5]


def test_state_text_sections_and_length(world):
    defs, state = world
    open_bout(defs, state)
    text = render.state_text(state, defs, "zhong_ling")
    heads = ["【你是谁】", "【此刻】", "【在场】", "【刚才】", "【你记得】", "【你的状态】", "【你的打算】", "【冲你来的请求】"]
    pos = [text.index(h) for h in heads]
    assert pos == sorted(pos)
    assert text.startswith("【你是谁】钟灵")
    assert "场面紧张" in text
    assert "藏身暗处" in text
    assert len(text) <= render.STATE_MAX
    # 不给数字：关系与身手只用档位文字
    assert "trust" not in text and "affinity" not in text


def test_state_text_uses_the_viewers_names(world):
    defs, state = world
    # 龚光杰认得师父和同门，不认得那个茶商伙计（玩家）
    text = render.state_text(state, defs, "gong_guangjie")
    assert "左子穆（" in text
    assert state.chars[state.player].unknown_alias in text
    assert state.chars[state.player].name not in text
    # 自己写成「你」
    from tianlong.minds.render import render_for

    assert render_for("⟦gong_guangjie⟧拔剑", state, defs, "gong_guangjie") == "你拔剑"


def test_state_text_trims_memories_to_fit(world):
    defs, state = world
    c = state.chars["gong_guangjie"]
    long = "长" * 58
    mems = [_m(state.tick - i, 9, ["duan_yu"], long + str(i)) for i in range(30)]
    text = render.state_text(state, defs, "gong_guangjie", memories=mems)
    assert len(text) <= render.STATE_MAX
    assert c.memory == []  # 渲染不改状态


def test_state_text_shows_recent_events_and_judgements(world):
    from tianlong.kernel import actions, resolve
    from tianlong.kernel.state import Speech

    defs, state = world
    w = actions.build(state, defs, state.player, "wait", {"ticks": 1})
    evs = resolve.apply(state, defs, w, Speech(act="tell", to=["ma_wude"], fact_refs=["F_tea_gift"]), source="player")
    text = render.state_text(state, defs, "ma_wude", evs)
    recent = text.split("【刚才】")[1].split("【你记得】")[0]
    assert "对你说" in recent


def test_card_block(world):
    defs, state = world
    block = render.card_block(defs, "gong_guangjie", state=state)
    assert block.startswith("<人物卡>") and block.endswith("</人物卡>")
    assert "你是龚光杰" in block and "底线" in block and "不当着众人求饶" in block
    assert "说话习惯" in block and "从前遇到这类处境时的做法" in block
