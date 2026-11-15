"""端到端测试的夹具：与 tests/game 共用（小世界或真实的 content/，Mock 的模型，日志与存档写进临时目录）。"""

from __future__ import annotations

from game.conftest import (  # noqa: F401 — 夹具要在这里露出名字，pytest 才认
    CONTENT,
    MINI,
    REPO,
    Session,
    _no_keys,
    assert_full_turn,
    ended_of,
    header_of,
    kinds,
    make_settings,
    new_session,
    notices,
    options_of,
    plain_text,
    real_settings,
    settings,
    text_of,
)

# 一局里混着用的输入：选项、自由输入、不耗时的话、命令（方案 2.1 至 2.7）
SCRIPT = (
    "一", "我四下看看", "二", "我仔细听听周围的动静", "三", "我对身边的人说：这场比剑当真好看",
    "一", "我掏出手机报警", "二", "我在这里等上半个时辰", "一", "（我一掌打死了所有人）", "三",
    "我找个角落歇息一个时辰", "一", "二", "我问问旁边的人这里是什么地方", "三", "一", "/状态", "二", "一",
    "我四下看看", "三", "一", "我在这里等上一个时辰", "二", "一", "三", "我仔细打量周围的人", "一", "二",
    "三", "一", "/牵挂", "二", "我歇息两个时辰", "一", "三", "二", "我在这里等上两个时辰", "一", "二", "三",
    "我四下看看", "一", "二", "三", "一", "二", "我仔细听听周围的动静", "三", "一", "二", "三", "/年谱",
    "一", "二", "三", "一",
)  # fmt: skip
