"""不变量与随机试探（方案 11.2）：默认每个种子 200 回合，3 个种子。

全量（20 个种子 × 2,000 回合）用 ``scripts/fuzz.py --seeds 20 --turns 2000`` 跑，结果记在 docs/acceptance.md。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.kernel.state import TICKS_PER_DAY
from tianlong.testing import FuzzConfig, fuzz_seed, load_free_texts
from tianlong.testing.monitor import plateau

TURNS = 200
SEEDS = (1, 2, 3)


def test_free_text_samples_come_from_the_eval_datasets() -> None:
    texts = load_free_texts()
    assert len(texts) >= 300  # 归类集 300 条，加上防作弊集的输入
    assert all(t.strip() and not t.startswith("/") for t in texts)
    assert any("（" in t for t in texts)  # 括号里写结果的作弊样例也混在里面


@pytest.mark.parametrize("seed", SEEDS)
async def test_random_play_keeps_the_world_sound(tmp_path: Path, seed: int) -> None:
    rep = await fuzz_seed(tmp_path, seed, FuzzConfig(turns=TURNS, sample_every=20))
    assert rep.turns == TURNS, rep.problems()
    # 三路输入都用上了；多数输入真的走了一回合
    assert rep.channels.get("option", 0) >= 40 and rep.channels.get("legal", 0) >= 40 and rep.channels.get("text", 0) >= 30
    assert rep.kinds.get("turn", 0) >= TURNS // 2, rep.kinds
    # 1 游戏从不抛异常；6.11 的不变量一次都不失败（直接查，加上 violations 里没有回滚）
    assert rep.exceptions == []
    assert rep.invariant_failures == []
    assert rep.rollbacks == []
    # 2 世界不停摆：每个走完的游戏日都有非玩家引发的事件，而且有不在玩家眼前发生的；
    #   没有哪个有魂、有名角色无事可做超过一日
    assert rep.days_full >= 1, rep.line()
    assert rep.quiet_days == []
    assert rep.min_day_others is not None and rep.min_day_others >= 1
    assert rep.min_day_elsewhere is not None and rep.min_day_elsewhere >= 1
    assert rep.stalls == []
    assert max(rep.longest_idle.values(), default=0) <= TICKS_PER_DAY
    # 出自原著的步骤与情境，前提不成立时没有产生过事件
    assert rep.ghosts == []
    # 3 内存与存档不失控：每人记忆不超过 200 条，事件尾巴不超过 200 条；存档在几 MB 之内
    assert rep.size_problems == []
    assert rep.max_memory <= 200 and rep.max_tail <= 200
    assert 0 < rep.max_save_bytes < 4 * 1024 * 1024, rep.max_save_bytes
    assert len(rep.curve) >= TURNS // 20
    assert rep.ok, rep.problems()


def test_plateau_tells_a_flat_curve_from_a_growing_one() -> None:
    flat = [{"turn": t, "save_bytes": 1000 + (t % 3)} for t in range(0, 400, 20)]
    grow = [{"turn": t, "save_bytes": 1000 + 10 * t} for t in range(0, 400, 20)]
    assert plateau(flat)["ratio"] < 1.01 and abs(plateau(flat)["slope_per_1000"]) < 50
    assert plateau(grow)["ratio"] > 1.5 and plateau(grow)["slope_per_1000"] > 9000
