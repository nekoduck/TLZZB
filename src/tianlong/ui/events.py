"""游戏与界面之间的界面事件（ARCHITECTURE 第 8 节）。

``game.Game.start()`` 与 ``Game.turn(raw)`` 产出这些事件，``ui`` 只认它们。本文件不依赖本包任何其他模块，
``game`` 可以放心 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NoticeLevel = Literal["info", "warn"]
EndReason = Literal["closed", "dead", "left"]


@dataclass(frozen=True, slots=True)
class Header:
    """一回合的抬头：〔第一日 辰时 · 某处〕。"""

    clock: str
    place: str


@dataclass(frozen=True, slots=True)
class Text:
    """叙述的一段流式文字（已过检查）。可以断在句子中间。"""

    piece: str


@dataclass(frozen=True, slots=True)
class Break:
    """一段叙述结束。"""


@dataclass(frozen=True, slots=True)
class Options:
    """三个选项的文字，按一、二、三排。"""

    items: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Notice:
    """戏外提示：预算、存档成功、命令输出。"""

    text: str
    level: NoticeLevel = "info"


@dataclass(frozen=True, slots=True)
class Ended:
    """收场；后事随后以 Text 流出。"""

    reason: EndReason


UIEvent = Header | Text | Break | Options | Notice | Ended

# ---- 界面上固定的几句话 ----

FREE_INPUT_HINT = "也可以直接写下你想说的话、想做的事："
PROMPT = "> "
NUMERALS = "一二三四五六七八九十"
ENDED_TITLE = "—— 收场 ——"
ENDED_TEXT: dict[str, str] = {
    "closed": "你搁下了笔，这一局到此为止。",
    "dead": "你死了。这一局到此为止。",
    "left": "你走出了这片山水，这一局到此为止。",
}
WARN_PREFIX = "【注意】"


def numeral(i: int) -> str:
    """第 i 个选项（从 0 数）的序号：一、二、三……十一、十二。"""
    n = i + 1
    if n <= 10:
        return NUMERALS[n - 1]
    if n < 20:
        return "十" + NUMERALS[n - 11]
    return str(n)


def header_text(h: Header) -> str:
    return f"〔{h.clock} · {h.place}〕" if h.place else f"〔{h.clock}〕"


def option_prefix(i: int) -> str:
    return f"{numeral(i)}、"
