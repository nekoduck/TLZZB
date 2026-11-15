"""不耗时的戏内回话（方案 4.2：diegetic_reply；4.3 J1 的最后一道退让；2.4 恍惚）。

只用通用的话，不提人名、地名。玩家的原话不回显。
"""

from __future__ import annotations

import zlib

UNCLEAR = "你张了张嘴，一时却不知该怎么说、怎么做才好。不妨说得明白些。"
ONLY_STAGE = "你在心里把这一幕想了一遍，可眼前什么也没有发生。说说你想做什么、想说什么吧。"
ABSURD = "你脑中一阵恍惚，仿佛想起了什么这世上没有的东西；定了定神，那念头便散了。"
NOBODY = "四下里没有人听你说话。"
META = "（想看命令，输入 /帮助；存档用 /存档 名字，读档用 /读档 名字；想收笔，输入 /收笔。）"
NO_SUCH_PERSON = "你四下望了望，眼前并没有这个人。"
NO_SUCH_THING = "你四下看了看，眼前并没有这样东西。"
NO_SUCH_WAY = "你辨了辨方向，从这里去不了那里。"
WHICH = "你一时拿不定主意：到底冲着哪一个、哪一样？"
CANNOT = "此刻你做不到这件事。"
BOUND = "你手脚被缚，动弹不得，只能说说话、等一等，或者拼命挣一挣。"
TOO_HURT = "你伤得太重，连挪动一下都难。"
CAPTIVE = "有人看着你，走不脱。"
TENSE = "眼下这光景，哪容你静下心来做这件事。"
HOSTILE_NEAR = "身边有人虎视眈眈，这时候可歇不得。"
NOT_HELD = "对方好端端的，用不着这样。"
ALREADY_HAVE = "那东西本就在你身上。"

_UNCLEAR_VARIANTS = (
    UNCLEAR,
    "你心里转了几个念头，却理不出个头绪来。不妨说得明白些。",
    "话到嘴边，你又犹豫了：到底想做什么？不妨说得明白些。",
)


def unclear_for(text: str) -> str:
    """按输入挑一句（确定性的，不动随机数）。"""
    return _UNCLEAR_VARIANTS[zlib.crc32((text or "").encode("utf-8")) % len(_UNCLEAR_VARIANTS)]
