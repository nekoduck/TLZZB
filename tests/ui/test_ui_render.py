"""界面事件的排版与渲染：东亚宽字符、折行、纯文本模式（方案 10.6；ARCHITECTURE 8）。"""

from __future__ import annotations

import io

from rich.console import Console

from tianlong.ui.events import (
    FREE_INPUT_HINT,
    Break,
    Ended,
    Header,
    Notice,
    Options,
    Text,
    UIEvent,
    header_text,
    numeral,
)
from tianlong.ui.render import Renderer, char_width, plain_text, text_width, wrap
from tianlong.ui.text import NO_LINE_START


def _plain(events: list[UIEvent], width: int | None = 40) -> str:
    out = io.StringIO()
    r = Renderer(plain=True, out=out, width=width)
    r.render_all(events)
    r.finish()
    return out.getvalue()


def test_char_width() -> None:
    assert char_width("汉") == 2
    assert char_width("「") == 2 and char_width("。") == 2 and char_width("，") == 2
    assert char_width("Ａ") == 2  # 全角
    assert char_width("a") == 1 and char_width(" ") == 1
    assert char_width("́") == 0  # 组合符
    assert char_width("‍") == 0  # 零宽连接符
    assert char_width("\x07") == 0
    assert char_width("😀") == 2
    assert char_width("·") == 1 and char_width("·", ambiguous=2) == 2  # 歧义宽度可调
    assert text_width("第一日 辰时") == 11
    assert text_width("ab汉字") == 6


def test_wrap_never_splits_or_overflows() -> None:
    text = "满厅的目光一下子都扎了过来，上首那位掌门人的脸沉了下去。abc def 你扭头一看。"
    for width in (8, 11, 17, 20, 33):
        lines = wrap(text, width)
        assert "".join(lines).replace(" ", "") == text.replace(" ", "")  # 只会丢掉折到行首的空格
        for line in lines:
            assert text_width(line) <= width
        for line in lines[1:]:
            assert not line or line[0] not in NO_LINE_START, (width, lines)


def test_wrap_hang_indent() -> None:
    lines = wrap("一二三四五六七八九十" * 2, 14, hang=4)
    assert lines[0] == "一二三四五六"
    assert all(line.startswith("    ") for line in lines[1:])
    assert all(text_width(line) <= 14 for line in lines)


def test_header_and_numerals() -> None:
    assert header_text(Header("第一日 辰时", "剑湖宫 练武厅")) == "〔第一日 辰时 · 剑湖宫 练武厅〕"
    assert header_text(Header("第一日 辰时", "")) == "〔第一日 辰时〕"
    assert [numeral(i) for i in range(4)] == ["一", "二", "三", "四"]
    assert numeral(10) == "十一"


SEQ: list[UIEvent] = [
    Header("第一日 辰时", "练武厅"),
    Text("厅上两人斗剑正酣。"),
    Text("忽听得身旁一声笑。"),
    Break(),
    Options(["扯一扯他的衣袖。", "抬头找一找。", "悄悄往厅门口挪两步。"]),
]


def test_plain_mode_exact_output() -> None:
    assert _plain(SEQ, width=80) == (
        "〔第一日 辰时 · 练武厅〕\n"
        "\n"
        "厅上两人斗剑正酣。忽听得身旁一声笑。\n"
        "\n"
        "一、扯一扯他的衣袖。\n"
        "二、抬头找一找。\n"
        "三、悄悄往厅门口挪两步。\n"
        "\n"
        f"{FREE_INPUT_HINT}\n"
    )


def test_plain_mode_narrow_width_wraps_wide_chars() -> None:
    out = _plain(SEQ, width=16)
    assert out == (
        "〔第一日 辰时 ·\n"
        "练武厅〕\n"
        "\n"
        "厅上两人斗剑正\n"
        "酣。忽听得身旁\n"
        "一声笑。\n"
        "\n"
        "一、扯一扯他的\n"
        "    衣袖。\n"
        "二、抬头找一找。\n"
        "三、悄悄往厅门\n"
        "    口挪两步。\n"
        "\n"
        "也可以直接写下\n"
        "你想说的话、想\n"
        "做的事：\n"
    )
    for line in out.splitlines():
        assert text_width(line) <= 16


def test_streaming_pieces_split_anywhere_give_same_output() -> None:
    whole = "满厅的目光一下子都扎了过来，上首那位掌门人的脸沉了下去。他咳了一声。"
    pieces = [whole[i : i + 3] for i in range(0, len(whole), 3)]
    a = _plain([Header("第一日 辰时", "厅"), Text(whole), Break()], width=20)
    b = _plain([Header("第一日 辰时", "厅"), *[Text(p) for p in pieces], Break()], width=20)
    assert a == b


def test_plain_text_matches_unwrapped_renderer() -> None:
    events = [*SEQ, Notice("已存档。"), Notice("预算快用完了", "warn"), Ended("dead"), Text("后事如此。")]
    assert plain_text(events) == _plain(events, width=None)
    text = plain_text(events)
    assert "\n已存档。\n" in text
    assert "【注意】预算快用完了" in text
    assert "—— 收场 ——\n你死了。" in text
    assert text.endswith("后事如此。\n")


def test_blank_lines_collapse() -> None:
    out = _plain(
        [Text("一段。"), Break(), Break(), Text("\n\n\n另一段。"), Break(), Header("第一日 巳时", "")]
    )
    assert out == "一段。\n\n另一段。\n\n〔第一日 巳时〕\n\n"


def test_options_without_items_still_show_hint() -> None:
    assert _plain([Options([])]) == f"{FREE_INPUT_HINT}\n"


def test_echo_input_line() -> None:
    out = io.StringIO()
    r = Renderer(plain=True, out=out, width=40)
    r.render_all(SEQ)
    r.echo_input("二")
    r.render(Header("第一日 辰时", "厅"))
    r.finish()
    assert f"{FREE_INPUT_HINT}\n> 二\n\n〔第一日 辰时 · 厅〕\n" in out.getvalue()


def test_rich_renderer_writes_text() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=40, force_terminal=False, color_system=None)
    r = Renderer(console)
    assert r.width == 39
    r.render_all([*SEQ, Notice("提示"), Ended("closed")])
    r.begin_wait()  # 非终端：不转圈，也不出错
    r.end_wait()
    r.finish()
    out = buf.getvalue()
    assert "〔第一日 辰时 · 练武厅〕" in out
    assert "一、扯一扯他的衣袖。" in out and FREE_INPUT_HINT in out
    assert "—— 收场 ——" in out
    for line in out.splitlines():
        assert text_width(line) <= 39


def test_max_width_caps_long_lines() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False, color_system=None)
    r = Renderer(console, max_width=30)
    r.render(Text("一二三四五六七八九十" * 6))
    r.finish()
    assert all(text_width(line) <= 30 for line in buf.getvalue().splitlines())
