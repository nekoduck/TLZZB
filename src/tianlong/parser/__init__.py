"""自由输入 → 内核动作（方案 2.4、4.2 第 1 步、7.3 J1、7.5 C1）。

    result = await parser.classify(llm, state, defs, raw)      # J1 → C1 → 戏内回话
    bound = parser.bind(state, defs, result)                    # 合法动作 + 一句话，或不耗时的回话
    thread = await parser.map_wish(llm, state, defs, text)      # /心愿 → {thread: …} 的参数
"""

from tianlong.parser.binding import Bound, bind
from tianlong.parser.candidates import (
    AskCand,
    ClaimCand,
    Menu,
    SelfCand,
    claim_candidates,
    request_candidates,
    self_event_candidates,
)
from tianlong.parser.intake import ParseResult, classify
from tianlong.parser.stage import strip_stage
from tianlong.parser.wish import map_wish

__all__ = [
    "AskCand",
    "Bound",
    "ClaimCand",
    "Menu",
    "ParseResult",
    "SelfCand",
    "bind",
    "claim_candidates",
    "classify",
    "map_wish",
    "request_candidates",
    "self_event_candidates",
    "strip_stage",
]
