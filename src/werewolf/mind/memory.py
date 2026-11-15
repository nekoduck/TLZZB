"""记忆槽（ARCHITECTURE 4.1）：只能由事件产生，8 个短期槽、8 个长期槽，按情绪强度淘汰。

- `observe(event, view)` 从（去掉文字的）事件里自动产生记忆；策略层也可以 `add` 自己造的记忆，
  但 `event_id` 必填，所以角色不会记得没发生过的事。来自私有事件的记忆标 `private`。
- 夜里 `consolidate(day)` 把当天最强的几条短期记忆升入长期槽。
- 强度随天数衰减（短期半衰期短，长期长）。衰减过半的只能带 `fuzzy` 引用（`is_fuzzy`）；
  被挤掉的不能再引用（`has` 为假）。
- `recall(about, day, k)` 按新近度、强度、相关度三项打分。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from werewolf.kernel.claims import ClaimLedger, speech_acts
from werewolf.kernel.types import ROLE_ZH, ActKind, Event, EventType, Role, Seat, View
from werewolf.mind.types import Memory, Traits

SHORT_SLOTS = 8
LONG_SLOTS = 8


class MemoryWeights(BaseModel):
    """记忆的调参旋钮。"""

    model_config = ConfigDict(extra='forbid')

    short_half_life: float = 2.0  # 短期记忆的强度半衰期（天）
    long_half_life: float = 5.0  # 长期记忆的半衰期
    promote_per_night: int = 2  # 每夜至多升几条进长期
    promote_min: float = 3.0  # 强度低于它的不升
    w_recency: float = 0.4  # recall 打分：新近度
    w_intensity: float = 0.6  # 当前强度 / 10
    w_relevance: float = 1.0  # 涉及正在谈的人


def _seats(about: Seat | Iterable[Seat] | None) -> set[Seat]:
    if about is None:
        return set()
    if isinstance(about, int):
        return {about}
    return set(about)


class MemoryStore:
    """一个座位的记忆。见模块说明。"""

    def __init__(self, seat: Seat, traits: Traits | None = None, weights: MemoryWeights | None = None) -> None:
        self.seat = seat
        self.traits = traits
        self.w = weights or MemoryWeights()
        self.day = 0
        self.last_event_id = 0
        self._short: list[Memory] = []
        self._long: list[Memory] = []

    # ------------------------------------------------------------------ 写

    def add(self, memory: Memory) -> bool:
        """放进短期槽；同一（事件，种类）不重复。满了挤掉当前强度最弱的（同强度挤旧的）。返回它是否留下。"""
        if any(m.event_id == memory.event_id and m.kind == memory.kind for m in self.all()):
            return False
        self.day = max(self.day, memory.day)
        mem = memory.model_copy(update={'slot': 'short'})
        self._short.append(mem)
        if len(self._short) > SHORT_SLOTS:
            weakest = min(self._short, key=lambda m: (self.strength(m, self.day), m.event_id))
            self._short.remove(weakest)
            return weakest is not mem
        return True

    def consolidate(self, day: int) -> list[Memory]:
        """夜间反思：把第 day 天最强的短期记忆升入长期槽（长期满了，比最弱的当前强度高才挤进去）。返回升上去的。"""
        self.day = max(self.day, day)
        cands = sorted((m for m in self._short if m.day == day and m.intensity >= self.w.promote_min),
                       key=lambda m: (-m.intensity, -m.event_id))
        promoted: list[Memory] = []
        for m in cands[: self.w.promote_per_night]:
            if len(self._long) >= LONG_SLOTS:
                weakest = min(self._long, key=lambda x: (self.strength(x, day), x.event_id))
                if self.strength(weakest, day) >= self.strength(m, day):
                    continue
                self._long.remove(weakest)
            self._short.remove(m)
            up = m.model_copy(update={'slot': 'long'})
            self._long.append(up)
            promoted.append(up)
        return promoted

    def observe(self, event: Event, view: View | None = None, ledger: ClaimLedger | None = None) -> list[Memory]:
        """从一条新近可见的事件产生记忆，返回新留下的。不读文字。"""
        if event.id <= self.last_event_id:
            return []
        self.last_event_id = event.id
        self.day = max(self.day, event.day)
        out: list[Memory] = []
        for kind, about, text, intensity in self._extract(event):
            mem = Memory(event_id=event.id, day=event.day, kind=kind, about=sorted(set(about)), text=text,
                         intensity=max(0.0, min(10.0, intensity)), private=event.vis != 'public')
            if self.add(mem):
                out.append(mem)
        return [m for m in out if self.has(m.event_id)]

    def _extract(self, ev: Event) -> list[tuple[str, list[Seat], str, float]]:
        me = self.seat
        veng = self.traits.vengeful if self.traits else 0.5
        t = ev.type
        out: list[tuple[str, list[Seat], str, float]] = []
        if t == EventType.SPEECH and ev.actor is not None and ev.actor != me:
            src = ev.actor
            for a in speech_acts(ev):
                tgt = a.target
                if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.DOUBT_CLAIM) and tgt == me:
                    out.append(('accused_me', [src], f'{src} 号踩我', 4.0 + a.weight + 2.0 * veng))
                elif a.kind in (ActKind.VOUCH, ActKind.BACK_CLAIM, ActKind.DEFEND) and tgt == me:
                    out.append(('vouched_me', [src], f'{src} 号替我说话', 4.0))
                elif a.kind == ActKind.CLAIM_ROLE and a.role in (Role.SEER, Role.WITCH, Role.HUNTER):
                    out.append(('claim', [src], f'{src} 号自称{ROLE_ZH[a.role]}', 5.0))
                elif a.kind == ActKind.CLAIM_CHECK and tgt is not None:
                    res = '狼人' if a.result == 'wolf' else '好人'
                    if tgt == me:
                        out.append(('checked_me', [src], f'{src} 号报我是{res}', 8.0 if a.result == 'wolf' else 5.0))
                    else:
                        out.append(('check_claim', [src, tgt], f'{src} 号报 {tgt} 号是{res}',
                                    5.0 if a.result == 'wolf' else 3.5))
                elif a.kind in (ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON) and tgt is not None:
                    verb = '救' if a.kind == ActKind.CLAIM_SAVE else '毒'
                    out.append(('potion_claim', [src, tgt], f'{src} 号说{verb}了 {tgt} 号', 4.5))
                elif a.kind == ActKind.ACCUSE and tgt is not None and a.weight >= 3:
                    out.append(('accusation', [src, tgt], f'{src} 号咬定 {tgt} 号', 3.0))
        elif t == EventType.VOTE and ev.actor is not None and ev.target == me and ev.actor != me:
            out.append(('voted_me', [ev.actor], f'{ev.actor} 号投了我', 5.0 + 2.0 * veng))
        elif t == EventType.VOTE_RESULT and ev.target is not None:
            out.append(('exile', [ev.target], f'第 {ev.day} 天 {ev.target} 号被投出局', 4.0))
        elif t == EventType.DEATH and ev.target is not None and ev.data.get('when') == 'night':
            out.append(('death', [ev.target], f'{ev.target} 号夜里死了', 4.0))
        elif t == EventType.HUNTER_SHOT and ev.target is not None and ev.actor is not None and ev.vis == 'public':
            out.append(('shot', [ev.actor, ev.target], f'{ev.actor} 号开枪带走了 {ev.target} 号', 6.0))
        elif t == EventType.ROLE_REVEALED and ev.target is not None:
            role = Role(ev.data.get('role'))
            out.append(('revealed', [ev.target], f'{ev.target} 号亮出了{ROLE_ZH[role]}', 5.0))
        elif t == EventType.SEER_RESULT and ev.actor == me and ev.target is not None:
            res = '狼人' if ev.data.get('result') == 'wolf' else '好人'
            out.append(('my_check', [ev.target], f'我验了 {ev.target} 号，是{res}', 7.0))
        elif t == EventType.WOLF_TEAM:
            mates = [int(s) for s in ev.data.get('wolves') or [] if int(s) != me]
            if mates:
                out.append(('teammates', mates, '队友是 ' + '、'.join(f'{s} 号' for s in mates), 6.0))
        elif t == EventType.WITCH_SAVE and ev.target is not None:
            out.append(('my_save', [ev.target], f'我救了 {ev.target} 号', 6.0))
        elif t == EventType.WITCH_POISON and ev.target is not None:
            out.append(('my_poison', [ev.target], f'我毒了 {ev.target} 号', 6.0))
        elif t == EventType.WITCH_NOTICE and ev.target is not None:
            out.append(('knife', [ev.target], f'第 {ev.day} 夜被刀的是 {ev.target} 号', 4.0))
        return out

    # ------------------------------------------------------------------ 读

    def all(self) -> list[Memory]:
        """全部记忆：长期在前，再短期，各按事件先后。"""
        return sorted(self._long, key=lambda m: m.event_id) + sorted(self._short, key=lambda m: m.event_id)

    def has(self, event_id: int) -> bool:
        """这条事件是否还在某个记忆槽里（指认引用证据的前提）。"""
        return any(m.event_id == event_id for m in self._short) or any(m.event_id == event_id for m in self._long)

    def get(self, event_id: int) -> list[Memory]:
        return [m for m in self.all() if m.event_id == event_id]

    def strength(self, memory: Memory, day: int) -> float:
        """当前强度：intensity × 0.5^(天数 / 半衰期)。"""
        half = self.w.long_half_life if memory.slot == 'long' else self.w.short_half_life
        age = max(0, day - memory.day)
        return memory.intensity * 0.5 ** (age / half)

    def is_fuzzy(self, event_id: int, day: int) -> bool:
        """强度已衰减过半（只能带 fuzzy 引用）。不在槽里的也算模糊——那种根本不能引用，先查 `has`。"""
        mems = self.get(event_id)
        if not mems:
            return True
        return all(self.strength(m, day) < 0.5 * m.intensity - 1e-9 for m in mems)

    def recall(self, about: Seat | Sequence[Seat] | None, day: int, k: int = 4,
               include_private: bool = True) -> list[Memory]:
        """按新近度 + 当前强度 + 相关度取至多 k 条，分高的在前（同分新的在前）。"""
        focus = _seats(about)

        def score(m: Memory) -> float:
            age = max(0, day - m.day)
            rel = 1.0 if focus and focus & set(m.about) else 0.0
            return (self.w.w_recency * 0.5 ** age + self.w.w_intensity * self.strength(m, day) / 10.0
                    + self.w.w_relevance * rel)

        pool = [m for m in self.all() if include_private or not m.private]
        return sorted(pool, key=lambda m: (-score(m), -m.event_id, m.kind))[:k]

    # ------------------------------------------------------------------ 存取

    def dump(self) -> dict[str, Any]:
        return {'seat': self.seat, 'day': self.day, 'last_event_id': self.last_event_id,
                'short': [m.model_dump(mode='json') for m in self._short],
                'long': [m.model_dump(mode='json') for m in self._long]}

    def load(self, data: dict[str, Any]) -> None:
        self.day = int(data['day'])
        self.last_event_id = int(data['last_event_id'])
        self._short = [Memory.model_validate(m) for m in data['short']]
        self._long = [Memory.model_validate(m) for m in data['long']]
