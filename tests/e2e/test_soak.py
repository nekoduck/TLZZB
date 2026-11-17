"""小规模的随机试探（方案 11.2 的缩小版）：真实的 content/ 里随机选选项、混入各种自由输入与命令，回合主循环不倒、不变量成立。"""

from __future__ import annotations

import random

from tianlong.config import Settings
from tianlong.kernel import invariants

from .conftest import header_of, new_session, options_of, text_of

FREE = (
    "我四下看看", "我仔细听听周围的动静", "我在这里等上一个时辰", "我歇息两个时辰", "我在这里等上一天",
    "我掏出手机报警", "（我成了天下第一）", "我对身边的人说：借问一声，出了什么事", "我一拳打向身边的人",
    "我练一练本事", "我要离开这一带", "是", "不", "/状态", "/牵挂", "/年谱", "/人物", "/物品", "/本事", "/回顾",
    "我吃点干粮", "我躲起来", "我跟着前面的人", "/心愿 我想学一门厉害的功夫", "我问他：你是谁", "/存档 试探",
)  # fmt: skip


async def test_random_inputs_never_break_the_loop(real_settings: Settings) -> None:
    s = await new_session(real_settings, seed=11, debug=True)
    g = s.game
    rng = random.Random(11)
    kinds: dict[str, int] = {}
    for i in range(70):
        raw = rng.choice("一二三") if rng.random() < 0.6 else rng.choice(FREE)
        evs = await s.send(raw)
        st = s.state
        rec = g.last_turn
        assert rec is not None
        kinds[rec.kind] = kinds.get(rec.kind, 0) + 1
        assert invariants.check(st, g.defs) == [], (i, raw)
        if st.ended is not None:
            # 收场了（走出地图、死了）：从检查点重来，接着试
            evs = await s.send("/读档 检查点")
            assert s.state.ended is None
            continue
        if rec.kind == "turn":
            assert header_of(evs) is not None and text_of(evs).strip(), (i, raw)
            if g._can_act():
                opts = options_of(evs)
                assert opts is not None and len(opts) == 3 and len(set(opts)) == 3, (i, raw, opts)
    assert kinds.get("turn", 0) >= 30 and kinds.get("command", 0) >= 3 and kinds.get("reply", 0) >= 3
    assert (g.run_dir / "transcript.md").stat().st_size > 5000
    await s.close()
