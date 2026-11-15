"""神态反应的投放：一条事件引出的反应里挑几个给前端，免得满屏都是表情。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from werewolf.kernel.types import EMOTES, Seat
from werewolf.mind.types import Reaction

COOLDOWN_EVENTS = 3  # 一个座位上次露出神态不到这么多条事件，这次跳过
BYPASS_INTENSITY = 0.8  # 强度到这里，不管冷却


def cooling(r: Reaction, recent: Mapping[Seat, int]) -> bool:
    """这条反应的座位还在冷却中。"""
    last = recent.get(r.seat)
    return last is not None and r.event_id - last < COOLDOWN_EVENTS


def pick(reactions: Sequence[Reaction], recent: Mapping[Seat, int], limit: int = 3) -> list[Reaction]:
    """挑出要显示的反应：至多 `limit` 个，强度高的优先，同强度座位小的优先，每个座位至多一个；
    冷却中的座位跳过，除非强度 ≥ BYPASS_INTENSITY。`recent`（座位 → 上次露出神态的事件号）只读不改。"""
    if limit <= 0:
        return []
    out: list[Reaction] = []
    seats: set[Seat] = set()
    for r in sorted(reactions, key=lambda r: (-r.intensity, r.seat)):
        if r.seat in seats:
            continue
        if r.intensity < BYPASS_INTENSITY and cooling(r, recent):
            continue
        out.append(r)
        seats.add(r.seat)
        if len(out) >= limit:
            break
    return out


class ReactionGate:
    """带状态的 `pick`：自己记着每个座位上次露出神态的事件号。"""

    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.recent: dict[Seat, int] = {}

    def pick(self, reactions: Sequence[Reaction]) -> list[Reaction]:
        chosen = pick(reactions, self.recent, self.limit)
        for r in chosen:
            self.recent[r.seat] = r.event_id
        return chosen

    def reset(self) -> None:
        self.recent.clear()


def label(emote: str) -> str:
    """神态的中文标签；不在词表里的返回空串。"""
    return EMOTES.get(emote, '')
