"""T1 的回归：真实 content/、Mock，三个种子各玩 60 回合，玩家看到的字里不再出现那几种不通的句子。

查的是模板的退路（Mock 下叙述、选项、后事都走模板）——模型失败时玩家看到的就是这些字。
"""

from __future__ import annotations

import re

import pytest

from tianlong.config import Settings
from tianlong.ui.events import Notice, Options, Text

from .conftest import new_session

FREE = (
    "我四下看看",
    "我仔细听听周围的动静",
    "我对身边的人说：这场比剑当真好看",
    "我在这里等上半个时辰",
    "我问问旁边的人这里是什么地方",
    "我找个角落歇息一个时辰",
    "我仔细打量周围的人",
    "/牵挂",
    "/年谱",
)

# 出现过的坏句子（docs/ISSUES_wave3.md 的 T1），逐字不许再有
BAD = (
    "试着你",  # 选项：「试着你报上来意」
    "喝令你离开",  # 选项：「低声劝……喝令你离开」
    "你说明自己的来意",  # 选项里带主语
    "不肯情境",
    "答应情境",
    "情境",  # 动词的内部名字不该漏给玩家
    "自称你",  # 「打了个招呼：自称你」
    "起了一场事",  # 年谱的通用句
    "之间的事还没完",  # 「了结了的：……之间的事还没完」
    "答应了：答应",
    "问：问",
    "请求：劝对方",
    "答应你的请求",  # 牵挂里的许诺：写答应了的那件事
    "⟦",
)
DUP = re.compile(r"([^，。；：「」\n]{4,})，又\1")  # 「向你打听……，又向你打听……」
OPTION_BAD_HEADS = ("你", "试着你")


def _epilogue_lists(text: str) -> tuple[list[str], list[str]]:
    closed: list[str] = []
    opened: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if para.startswith("了结了的："):
            closed = para[len("了结了的："):].rstrip("。").split("；")
        elif para.startswith("还没了结的："):
            body = para[len("还没了结的："):].split("。这些事后来如何")[0]
            opened = body.split("；")
    return closed, opened


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_sixty_turns_show_no_known_glitch(real_settings: Settings, seed: int) -> None:
    s = await new_session(real_settings, seed=seed)
    st = s.state
    played = 0
    i = 0
    while played < 60 and st.ended is None and i < 200:
        raw = "一二三"[(i * 7 + seed) % 3] if i % 4 else FREE[(i // 4 + seed) % len(FREE)]
        i += 1
        turn0 = st.turn
        await s.send(raw)
        played += st.turn - turn0
    assert played >= 40, played  # 这局确实走了下去
    if st.ended is None:
        await s.send("/收笔")
    assert st.ended is not None

    narration: list[str] = []
    options: list[str] = []
    notices: list[str] = []
    for _raw, evs in s.steps:
        narration.append("".join(e.piece for e in evs if isinstance(e, Text)))
        for e in evs:
            if isinstance(e, Options):
                options += list(e.items)
            elif isinstance(e, Notice):
                notices.append(e.text)
    # 一屏叙述里同一句话不写两遍（说话的事按模板句写了，核对时不能又当成「没写到」在段末补一遍）
    for text in narration[:-1]:
        sents = [x for x in re.split(r"(?<=[。！？」])", text) if len(x.strip()) >= 6]
        assert len(sents) == len(set(sents)), ([x for x in sents if sents.count(x) > 1], text)
    shown = [*narration, *options, *notices]
    for text in shown:
        for bad in BAD:
            assert bad not in text, (bad, text)
        m = DUP.search(text)
        assert m is None, (m.group(0) if m else "", text)
    assert options and any(n.startswith("【牵挂】") for n in notices) and any("【年谱】" in n for n in notices)
    for o in options:
        assert not o.startswith(OPTION_BAD_HEADS), o
        if "劝" in o:
            # 劝的是别人自己的一招（「放你过去」可以劝；冲着自己来的「喝令你……」不会给）
            assert o.startswith(("低声劝", "悄悄劝")) and "喝令你" not in o, o
    # 后事：了结了的与还没了结的不打架，也不重复
    closed, opened = _epilogue_lists(narration[-1])
    assert opened or closed or "收" in narration[-1]
    assert len(set(closed)) == len(closed), closed
    assert not set(closed) & set(opened), (closed, opened)
    assert not any("还没完" in c for c in closed), closed
    await s.close()
