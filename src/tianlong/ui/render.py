"""把界面事件画到终端上（方案 10.1、10.6；ARCHITECTURE 8）。

- 抬头一行〔时辰 · 地点〕；
- 叙述按流式片段即到即印，按东亚宽字符的显示宽度自己折行，不把一个汉字劈成两半；
- 选项按一、二、三排，后面跟一句「也可以直接写下你想说的话、想做的事：」；
- ``plain=True`` 时不用 rich，直接写文本流（测试与管道输入用）。
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from tianlong.ui.events import Ended, Header, UIEvent
from tianlong.ui.text import EventFormatter, LineWriter, char_width, plain_text, text_width, wrap

__all__ = ["Renderer", "STYLES", "char_width", "text_width", "wrap", "plain_text", "DEFAULT_MAX_WIDTH"]

DEFAULT_MAX_WIDTH = 76  # 汉字一行太长不好读：显示宽度至多 76 格（38 个汉字）

STYLES: dict[str, str] = {
    "header": "bold cyan",
    "text": "",
    "option_num": "bold",
    "option": "",
    "hint": "dim",
    "notice": "grey62",
    "warn": "yellow",
    "ended": "bold magenta",
    "input": "dim",
}


class Renderer:
    """界面事件的渲染器。

    ``Renderer(console)``：用 rich 的 ``Console`` 上色输出。
    ``Renderer(plain=True, out=stream, width=n)``：纯文本，写进 ``out``（默认标准输出）。
    """

    def __init__(
        self,
        console: Any | None = None,
        *,
        plain: bool = False,
        out: TextIO | None = None,
        width: int | None = None,
        max_width: int = DEFAULT_MAX_WIDTH,
        ambiguous: int = 1,
    ) -> None:
        self.plain = plain
        self.max_width = max_width
        self._fixed_width = width
        self.console: Any | None = None
        self.out: TextIO | None = None
        if plain:
            self.out = out if out is not None else sys.stdout
        else:
            if console is None:
                from rich.console import Console

                console = Console(file=out, highlight=False, soft_wrap=True, emoji=False)
            self.console = console
        self.writer = LineWriter(self._emit, self._compute_width(), ambiguous=ambiguous)
        self.fmt = EventFormatter(self.writer)
        self._status: Any | None = None
        self.ended: Ended | None = None

    # ---- 宽度 ----
    def _terminal_width(self) -> int:
        if self.console is not None:
            return int(self.console.width)
        return shutil.get_terminal_size((80, 24)).columns

    def _compute_width(self) -> int:
        if self._fixed_width is not None:
            return max(8, int(self._fixed_width))
        # 留一格，免得写满整行时终端自己再折一次
        return max(8, min(self.max_width, self._terminal_width() - 1))

    @property
    def width(self) -> int:
        return self.writer.width or 0

    # ---- 输出 ----
    def _emit(self, text: str, style: str | None) -> None:
        if not text:
            return
        if self.console is not None:
            from rich.text import Text as RichText

            self.console.print(
                RichText(text, style=STYLES.get(style or "", "") or ""),
                end="",
                soft_wrap=True,
                highlight=False,
                markup=False,
                emoji=False,
            )
        else:
            assert self.out is not None
            self.out.write(text)
            if "\n" in text:
                self.out.flush()

    def render(self, ev: UIEvent) -> None:
        if isinstance(ev, Header) and self._fixed_width is None and self.writer.col == 0:
            self.writer.width = self._compute_width()  # 每回合开头按当前终端宽度重算
        if isinstance(ev, Ended):
            self.ended = ev
        self.fmt.feed(ev)
        self.flush()

    def render_all(self, events: Iterable[UIEvent]) -> None:
        for ev in events:
            self.render(ev)

    def echo_input(self, raw: str) -> None:
        """非终端模式下回显玩家的输入（屏幕上就是「> 输入」这一行）。"""
        self.fmt.echo_input(raw)
        self.flush()

    def finish(self) -> None:
        """收尾：停在行首。"""
        self.fmt.finish()
        self.flush()

    def flush(self) -> None:
        if self.out is not None:
            self.out.flush()
        elif self.console is not None:
            f = getattr(self.console, "file", None)
            if f is not None and hasattr(f, "flush"):
                f.flush()

    # ---- 等待第一段文字时的转圈 ----
    def begin_wait(self, text: str = "……") -> None:
        """在终端上转一个小圈，直到第一条界面事件到来。纯文本模式与非终端下什么也不做。"""
        if (
            self.console is None
            or self._status is not None
            or not getattr(self.console, "is_terminal", False)
        ):
            return
        try:
            self._status = self.console.status(text, spinner="dots")
            self._status.start()
        except Exception:  # 转圈只是装饰，出错就不转
            self._status = None

    def end_wait(self) -> None:
        if self._status is not None:
            try:
                self._status.stop()
            finally:
                self._status = None
