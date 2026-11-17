from __future__ import annotations

from tianlong.parser import strip_stage
from tianlong.parser.stage import MAX_CLEAN


def test_brackets_of_every_kind_are_removed_verbatim() -> None:
    clean, attempts = strip_stage("我把他打晕了（他晕倒了）")
    assert clean == "我把他打晕了"
    assert attempts == ["他晕倒了"]
    clean, attempts = strip_stage("你好(笑)【他答应了】[掌门大喜]*众人鼓掌*＊他跪下了＊！")
    assert clean == "你好！"
    assert attempts == ["笑", "他答应了", "掌门大喜", "众人鼓掌", "他跪下了"]


def test_nested_and_unclosed() -> None:
    clean, attempts = strip_stage("我走（心想（他不会发现））了")
    assert clean == "我走了"
    assert attempts == ["心想（他不会发现）"]
    clean, attempts = strip_stage("我等着（他终于来了")
    assert clean == "我等着"
    assert attempts == ["他终于来了"]
    clean, attempts = strip_stage("*他死了")
    assert clean == ""
    assert attempts == ["他死了"]


def test_stray_closers_and_whitespace() -> None:
    clean, attempts = strip_stage("  我）  四下  看看  ")
    assert clean == "我 四下 看看"
    assert attempts == []


def test_long_input_is_cut() -> None:
    clean, _ = strip_stage("看" * (MAX_CLEAN + 50))
    assert len(clean) == MAX_CLEAN
