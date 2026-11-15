"""斜杠命令：命令表、解析，与只读画面（方案 2.7）。"""

from __future__ import annotations

import re

import pytest

from tianlong.kernel import resolve
from tianlong.kernel.state import Relation
from tianlong.ui import commands as C
from tianlong.ui.commands import COMMANDS, parse_command

DIGITS = re.compile(r"[0-9０-９]")
PLAN_27 = [
    "状态",
    "物品",
    "人物",
    "本事",
    "牵挂",
    "回顾",
    "年谱",
    "心愿",
    "存档",
    "读档",
    "收笔",
    "帮助",
    "退出",
]


def _fx(world, *effects: dict) -> None:
    defs, state = world
    resolve.run_effects(state, defs, list(effects), source="rule")


# ---------------------------------------------------------------- 命令表与解析


def test_command_table_covers_plan() -> None:
    for name in PLAN_27:
        assert name in COMMANDS, name
        kind, help_ = COMMANDS[name][0], COMMANDS[name][1]
        assert kind and help_
    assert COMMANDS["debug"].debug and COMMANDS["debug"].kind == "debug"
    assert not any(COMMANDS[n].debug for n in PLAN_27)
    kinds = {n: s.kind for n, s in COMMANDS.items()}
    assert {kinds[n] for n in ["状态", "物品", "人物", "本事", "回顾", "帮助"]} == {"view"}
    assert kinds["牵挂"] == kinds["年谱"] == "storyteller"
    assert kinds["存档"] == kinds["读档"] == "store"
    assert kinds["心愿"] == "wish" and kinds["收笔"] == "close" and kinds["退出"] == "exit"
    assert set(C.VIEWS) == {"状态", "物品", "人物", "本事", "回顾", "帮助"}


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("/状态", ("状态", "")),
        ("  /存档 第一局  ", ("存档", "第一局")),
        ("／读档　第一局", ("读档", "第一局")),  # 全角斜杠、全角空格
        ("/心愿 学会认字 再说", ("心愿", "学会认字 再说")),
        ("/心愿学会认字", ("心愿", "学会认字")),  # 不隔空格
        ("/存档甲", ("存档", "甲")),
        ("、帮助", ("帮助", "")),  # 输入法把斜杠打成顿号
        ("、存档 乙", ("存档", "乙")),
        ("/help", ("帮助", "")),
        ("/SAVE x", ("存档", "x")),
        ("/quit", ("退出", "")),
        ("/debug", ("debug", "")),
        ("/DEBUG", ("debug", "")),
        ("/回顾 5", ("回顾", "5")),
        ("/不存在的 命令", ("不存在的", "命令")),
        ("/", ("", "")),
        ("/foo", ("foo", "")),
    ],
)
def test_parse_command(raw: str, want: tuple[str, str]) -> None:
    assert parse_command(raw) == want


@pytest.mark.parametrize("raw", ["", "  ", "一", "2", "我想走", "、你好", "、", "去/来"])
def test_not_a_command(raw: str) -> None:
    assert parse_command(raw) is None
    assert not C.is_command(raw)


def test_help_lists_every_command() -> None:
    text = C.help_text()
    for name in PLAN_27:
        assert f"/{name}" in text
    assert "/debug" not in text
    assert "/debug" in C.help_text(debug=True)
    assert "不耗" in text
    assert C.render_help() == text
    assert "没有「/foo」" in C.unknown_command_text("foo")


# ---------------------------------------------------------------- /状态


def test_status_opening(world) -> None:
    defs, state = world
    text = C.render_view("状态", state, defs)
    assert text.startswith("【状态】")
    assert "第一日 辰时" in text
    loc = state.chars[state.player].loc
    assert state.locations[loc].name in text  # 玩家知道这个地名
    assert "无恙" in text
    assert "碎银" in text
    assert "马五德" in text  # 听命于东家


def test_status_body_injury_and_poison_in_words(world) -> None:
    defs, state = world
    _fx(world, {"injure": ["player", 2]}, {"add_status": ["player", "poisoned_duanchang", {"ticks": 50}]})
    text = C.render_status(state, defs)
    assert "钻心" in text  # 2 级伤
    assert "绞痛" in text  # 毒发前的征兆
    assert not DIGITS.search(text.replace("第一日", ""))


def test_status_role_labels_from_world_labels(world) -> None:
    defs, state = world
    labels = dict(defs.world.labels)
    labels["role:ma_caravan_hand"] = "商队伙计"
    defs2 = defs.model_copy(update={"world": defs.world.model_copy(update={"labels": labels})})
    assert "商队伙计" in C.render_status(state, defs2)
    assert C.role_label(state, defs, "surname:段") == "姓段"
    assert C.role_label(state, defs, "wuliang_east") == "无量剑东宗"  # 专名表
    assert C.role_label(state, defs, "no_such_role") is None


# ---------------------------------------------------------------- /物品


def test_items(world) -> None:
    defs, state = world
    text = C.render_items(state, defs)
    for name in ["干粮", "水囊", "火折子", "碎银", "样茶", "麻绳"]:
        assert name in text
    assert "此处看得见的" not in text
    _fx(world, {"give": ["player", state.chars[state.player].loc, "rope"]})
    text = C.render_items(state, defs)
    assert "此处看得见的：\n　麻绳" in text
    assert text.index("随身带着") < text.index("此处看得见的")


# ---------------------------------------------------------------- /人物


def test_people_uses_player_names_and_bands(world) -> None:
    defs, state = world
    text = C.render_view("人物", state, defs)
    lines = text.splitlines()
    assert lines[0] == "【人物】"
    # 玩家只知道他姓段：写叫法，不写真名
    assert "段公子" in text and state.chars["duan_yu"].name not in text
    # 藏在梁上的人没见过
    assert state.chars["zhong_ling"].name not in text
    assert state.chars["zhong_ling"].unknown_alias not in text
    # 相熟的人排在前面
    assert lines[1].startswith("马五德（在此）") and lines[2].startswith("段公子（在此）")
    assert "很有好感" in lines[1]
    assert not DIGITS.search(text)


def test_people_never_shows_numbers(world) -> None:
    defs, state = world
    for trust in range(-100, 101, 9):
        for aff in range(-100, 101, 13):
            for fear in (0, 25, 60, 95):
                t = C.attitude_text(Relation(trust=trust, affinity=aff, fear=fear))
                assert t and not DIGITS.search(t) and "-" not in t
    _fx(
        world,
        {"rel": ["duan_yu", "player", "affinity", -200]},
        {"rel": ["duan_yu", "player", "trust", -200]},
        {"rel": ["duan_yu", "player", "fear", 90]},
        {"rel": ["player", "zuo_zimu", "regard", 4]},
    )
    text = C.render_people(state, defs)
    assert not DIGITS.search(text)
    duan = next(line for line in text.splitlines() if line.startswith("段公子"))
    assert "信不过你" in duan and "厌恶你" in duan and "怕你" in duan
    zuo = next(line for line in text.splitlines() if line.startswith("左子穆"))
    assert "好手" in zuo


def test_people_dead_seen(world) -> None:
    defs, state = world
    _fx(world, {"kill": "wu_guangsheng"})
    text = C.render_people(state, defs)
    name = C._name(state, defs, "wu_guangsheng")
    assert f"{name}（已死）" in text


def test_people_empty(world) -> None:
    # 一个谁也没见过、身边也没人的玩家
    defs, state = world
    occupied = {c.loc for c in state.chars.values()}
    empty = next(lid for lid in state.locations if lid not in occupied)
    me = state.chars[state.player].model_copy(update={"rel": {}, "loc": empty})
    lonely = state.model_copy(update={"chars": {**state.chars, state.player: me}, "term_alias": {}})
    assert "谁也不认得" in C.render_people(lonely, defs)


# ---------------------------------------------------------------- /本事


def test_arts(world) -> None:
    defs, state = world
    text = C.render_view("本事", state, defs)
    assert "不会武功" in text and "全无" in text and "粗识文字" in text
    assert "行商的本事：会估价" in text
    _fx(world, {"train": ["player", "trade", 400]}, {"train": ["player", "literacy", 100]})
    text = C.render_arts(state, defs)
    assert "（往下一层" in text or "快到下一层" in text
    assert "学字" in text
    assert not DIGITS.search(text)
    _fx(world, {"train": ["player", "trade", 10_000]})
    assert "精于讲价（已到了头）" in C.render_arts(state, defs)


# ---------------------------------------------------------------- /回顾


def test_review(world) -> None:
    defs, state = world
    assert "还没有" in C.render_view("回顾", state, defs)
    turns = [f"第{i}回的文字" for i in "一二三四五"]
    text = C.render_view("回顾", state, defs, turns=turns)
    assert "第三回" in text and "第五回" in text and "第二回" not in text
    assert text.index("第三回") < text.index("第五回")
    assert "第一回" in C.render_view("回顾", state, defs, arg="5", turns=turns)
    assert "第四回" in C.render_view("回顾", state, defs, arg="二", turns=turns)
    assert "第三回" not in C.render_view("回顾", state, defs, arg="二", turns=turns)
    assert C.render_review(state, defs, turns, n=1).endswith("第五回的文字")
