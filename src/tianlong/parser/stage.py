"""舞台说明的剥离（方案 2.4、7.3）。

括号（）()【】[]［］〔〕里的话与星号 * ＊ 之间的话一律剥掉，原样记进 ``attempts``。
它们不参与任何判断：玩家写的是意图，不是事实。
"""

from __future__ import annotations

import re

PAIRS: dict[str, str] = {
    "（": "）",
    "(": ")",
    "【": "】",
    "[": "]",
    "［": "］",
    "〔": "〕",
}
STARS = ("*", "＊")
MAX_CLEAN = 400  # 送进模型的话至多这么长；多出来的忽略（2.4）

_WS = re.compile(r"\s+")
_STRAY = re.compile(r"[（）()【】\[\]［］〔〕*＊]")


def _close(raw: str, i: int, opener: str) -> int:
    """从 raw[i]（开括号）起找配对的闭括号的下标；没有就返回 len(raw)。同种括号可嵌套。"""
    closer = PAIRS[opener]
    depth = 0
    for j in range(i, len(raw)):
        ch = raw[j]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return j
    return len(raw)


def strip_stage(raw: str) -> tuple[str, list[str]]:
    """返回 (剥完的话, 剥下来的舞台说明)。未闭合的括号或星号一直剥到句末。"""
    text = raw or ""
    out: list[str] = []
    attempts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in PAIRS:
            j = _close(text, i, ch)
            inner = text[i + 1 : j].strip()
            if inner:
                attempts.append(inner)
            i = j + 1
            continue
        if ch in STARS:
            j = i + 1
            while j < n and text[j] not in STARS:
                j += 1
            inner = text[i + 1 : j].strip()
            if inner:
                attempts.append(inner)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    clean = _STRAY.sub("", "".join(out))  # 落单的闭括号
    clean = _WS.sub(" ", clean).strip()
    if len(clean) > MAX_CLEAN:
        clean = clean[:MAX_CLEAN]
    return clean, attempts
