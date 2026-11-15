"""终端文字的排版：东亚宽字符的显示宽度、按宽度折行、把界面事件排成纯文本。

不依赖 rich，``game`` 可以用 ``plain_text`` 把一回合的界面事件拼成「玩家看到的全文」写进 transcript。
``ui/render.py`` 的 ``Renderer`` 用同一套排版，再交给 rich 上色或直接写进文本流。
"""

from __future__ import annotations

import io
import unicodedata
from collections.abc import Callable, Iterable

from tianlong.ui.events import (
    ENDED_TEXT,
    ENDED_TITLE,
    FREE_INPUT_HINT,
    PROMPT,
    WARN_PREFIX,
    Break,
    Ended,
    Header,
    Notice,
    Options,
    Text,
    UIEvent,
    header_text,
    option_prefix,
)

TAB = "    "
# 不放在行首的标点（避头）：折行时允许它们挂在行尾的预留格里
NO_LINE_START = frozenset("，。、；：？！）」』】〕》〉…—·．,.;:?!)]}”’％%")


def char_width(ch: str, *, ambiguous: int = 1) -> int:
    """一个字符在终端里占几格：宽字符与全角为 2，组合符与控制符为 0，其余为 1。

    East Asian Width 为 A（歧义）的字符按 ``ambiguous`` 计，多数现代终端按 1 显示。
    """
    if not ch:
        return 0
    o = ord(ch)
    if o < 32 or 0x7F <= o < 0xA0:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    if eaw == "A":
        return ambiguous
    return 1


def text_width(text: str, *, ambiguous: int = 1) -> int:
    """一行文字的显示宽度（不含换行）。"""
    return sum(char_width(ch, ambiguous=ambiguous) for ch in text if ch != "\n")


def wrap(text: str, width: int, *, hang: int = 0, ambiguous: int = 1) -> list[str]:
    """按显示宽度折行，不拆开宽字符；续行缩进 ``hang`` 格。返回各行（不含换行符）。"""
    out = io.StringIO()
    w = LineWriter(lambda s, _style: out.write(s), width, ambiguous=ambiguous)
    w.hang = hang
    w.write(text)
    return out.getvalue().split("\n")


Emit = Callable[[str, str | None], None]


class LineWriter:
    """按显示宽度折行的写手。``emit(text, style)`` 收到的文字里已插好换行。

    - ``width`` 为空时不折行。
    - 普通字符写到 ``width - 2`` 格就换行；避头的标点可以挂进最后两格，免得落在下一行的行首。
    - ``hang`` 是当前这一行折下去的续行缩进，遇到换行即清零。
    """

    def __init__(self, emit: Emit, width: int | None = None, *, ambiguous: int = 1) -> None:
        self.emit = emit
        self.width = width
        self.ambiguous = ambiguous
        self.col = 0
        self.hang = 0

    def write(self, text: str, style: str | None = None) -> None:
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", TAB)
        if not self.width:
            for ch in text:
                if ch == "\n":
                    self.col = 0
                    self.hang = 0
                else:
                    self.col += char_width(ch, ambiguous=self.ambiguous)
            self.emit(text, style)
            return
        hard = max(4, self.width)
        soft = hard - 2 if hard > 12 else hard
        buf: list[str] = []
        for ch in text:
            if ch == "\n":
                buf.append("\n")
                self.col = 0
                self.hang = 0
                continue
            w = char_width(ch, ambiguous=self.ambiguous)
            limit = hard if ch in NO_LINE_START else soft
            if w and self.col > self.hang and self.col + w > limit:
                buf.append("\n" + " " * self.hang)
                self.col = self.hang
                if ch == " ":
                    continue
            buf.append(ch)
            self.col += w
        self.emit("".join(buf), style)


class EventFormatter:
    """把界面事件排成文字（ARCHITECTURE 8 的版式）：

    ```text
    〔第一日 辰时 · 某地〕

    叙述……

    一、……
    二、……
    三、……

    也可以直接写下你想说的话、想做的事：
    ```

    段落之间空一行；连续的空行合并成一行。``style`` 是给 rich 的样式名，纯文本输出时忽略。
    """

    def __init__(self, writer: LineWriter) -> None:
        self.w = writer
        self.state = "start"  # start / inline / line（行首，上一行有字）/ blank（行首，上一行是空行）

    # ---- 底层 ----
    def write(self, text: str, style: str | None = None) -> None:
        if not text:
            return
        self.w.write(text, style)
        for ch in text:
            self._advance(ch)

    def _advance(self, ch: str) -> None:
        if ch == "\n":
            self.state = "line" if self.state == "inline" else "blank"
        else:
            self.state = "inline"

    def ensure_newline(self) -> None:
        if self.state == "inline":
            self.write("\n")

    def ensure_blank(self) -> None:
        self.ensure_newline()
        if self.state == "line":
            self.write("\n")

    # ---- 事件 ----
    def feed(self, ev: UIEvent) -> None:
        if isinstance(ev, Header):
            self.ensure_blank()
            self.write(header_text(ev), "header")
            self.write("\n\n")
        elif isinstance(ev, Text):
            self.write(self._normalize_piece(ev.piece), "text")
        elif isinstance(ev, Break):
            self.ensure_blank()
        elif isinstance(ev, Options):
            self.ensure_blank()
            for i, item in enumerate(ev.items):
                prefix = option_prefix(i)
                self.write(prefix, "option_num")
                self.w.hang = text_width(prefix, ambiguous=self.w.ambiguous)
                self.write(" ".join(str(item).split()), "option")
                self.write("\n")
            if ev.items:
                self.write("\n")
            self.write(FREE_INPUT_HINT, "hint")
            self.write("\n")
        elif isinstance(ev, Notice):
            self.ensure_blank()
            text = str(ev.text).strip("\n")
            if ev.level == "warn":
                self.write(WARN_PREFIX + text, "warn")
            else:
                self.write(text, "notice")
            self.write("\n")
        elif isinstance(ev, Ended):
            self.ensure_blank()
            self.write(ENDED_TITLE, "ended")
            self.write("\n")
            self.write(ENDED_TEXT.get(ev.reason, ENDED_TEXT["closed"]), "ended")
            self.write("\n\n")

    def echo_input(self, raw: str) -> None:
        """把玩家的输入照屏幕上的样子记下（非终端模式下回显，transcript 里也这样写）。"""
        self.ensure_newline()
        self.write(PROMPT + raw, "input")
        self.write("\n")

    def finish(self) -> None:
        self.ensure_newline()

    def _normalize_piece(self, piece: str) -> str:
        piece = (piece or "").replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n\n" in piece:
            piece = piece.replace("\n\n\n", "\n\n")
        if self.state in ("blank", "start"):
            piece = piece.lstrip("\n")
        elif self.state == "line" and piece.startswith("\n\n"):
            piece = piece[1:]
        return piece


def plain_text(events: Iterable[UIEvent], *, width: int | None = None, ambiguous: int = 1) -> str:
    """把一串界面事件排成纯文本（不上色；``width`` 为空时不折行）。transcript 用它。"""
    out = io.StringIO()
    fmt = EventFormatter(LineWriter(lambda s, _style: out.write(s), width, ambiguous=ambiguous))
    for ev in events:
        fmt.feed(ev)
    fmt.finish()
    return out.getvalue()
