"""声明账本：只读公开发言事件里的言语行为（acts，从不读文字），全桌一致。

契约见 ARCHITECTURE 3.2。账本记的永远是「某人说过」，不是事实；死活与平安夜只按公开的
`death`、`dawn` 事件判断，用来查声明在公开事实下是否可能成立（`contradictions`）。

`add` 是幂等的：编号不大于已喂过的最大编号的事件直接忽略，所以几个部件共用一本账、各自
防御性地调 `add` 也不会重复记账。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from werewolf.kernel.types import ROLE_ZH, ActKind, Event, EventType, Role, Seat, SpeechAct

ContradictionKind = Literal[
    'role_change',  # 改口身份
    'check_conflict',  # 对同一目标报了不同结果
    'double_check',  # 一夜报两验
    'future_night',  # 报的夜数超过已过的夜数（或小于 1）
    'check_dead',  # 验了那一夜开始时已死的人
    'check_self',  # 验了自己
    'save_died',  # 说救了的人却在那天早上死了
    'poison_alive',  # 说毒了的人那天早上没死
    'double_save',  # 说救过两个不同的人（解药只有一瓶）
    'double_poison',  # 说毒过两个不同的人（毒药只有一瓶）
    'two_potions',  # 同一夜既救又毒
]


class RoleClaim(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    role: Role
    day: int
    event_id: int


class CheckClaim(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    target: Seat
    result: Literal['wolf', 'good']
    night: int
    day: int
    event_id: int


class PotionClaim(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    kind: Literal['save', 'poison']
    target: Seat
    night: int
    day: int
    event_id: int


class Contradiction(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    kind: ContradictionKind
    event_id: int
    text: str  # 中性的中文描述，只含座位号与夜数


class ClaimLedger:
    """公开声明的账。逐条 `add` 事件（或 `from_events` 一次喂完）。"""

    def __init__(self) -> None:
        self.last_event_id = 0
        self._roles: list[RoleClaim] = []
        self._checks: list[CheckClaim] = []
        self._potions: list[PotionClaim] = []
        self._intents: dict[int, dict[Seat, Seat]] = {}
        self._contradictions: list[Contradiction] = []
        self._deaths: dict[Seat, tuple[int, str]] = {}  # 座位 → (death.day, when)
        self._dawns: dict[int, list[Seat]] = {}  # 第几天早上 → 公布的死者
        self._nights = 0  # 已经开始过的夜数（= 见过的最大 day）

    @classmethod
    def from_events(cls, events: Sequence[Event]) -> ClaimLedger:
        ledger = cls()
        for ev in events:
            ledger.add(ev)
        return ledger

    # ------------------------------------------------------------------ 喂事件

    def add(self, ev: Event) -> None:
        """喂一条事件；非公开事件、旧事件忽略。只有公开的 speech、death、dawn 会改变账本。"""
        if ev.id <= self.last_event_id:
            return
        self.last_event_id = ev.id
        if ev.vis != 'public':
            return
        self._nights = max(self._nights, ev.day)
        if ev.type == EventType.DEATH and ev.target is not None:
            self._deaths.setdefault(ev.target, (int(ev.data.get('day', ev.day)), str(ev.data.get('when', ''))))
        elif ev.type == EventType.DAWN:
            self._dawns[ev.day] = sorted(int(s) for s in ev.data.get('deaths') or [])
        elif ev.type == EventType.SPEECH and ev.actor is not None:
            for a in speech_acts(ev):
                self._add_act(ev, ev.actor, a)

    def _add_act(self, ev: Event, seat: Seat, a: SpeechAct) -> None:
        if a.kind == ActKind.CLAIM_ROLE and a.role is not None:
            prev = self.current_role(seat)
            if prev is not None and prev != a.role:
                self._flag(seat, 'role_change', ev.id,
                           f'{seat} 号先自称{ROLE_ZH[prev]}，后改称{ROLE_ZH[a.role]}')
            if prev != a.role:
                self._roles.append(RoleClaim(seat=seat, role=a.role, day=ev.day, event_id=ev.id))
        elif a.kind == ActKind.CLAIM_CHECK and a.target is not None and a.result is not None:
            self._add_check(ev, seat, a.target, a.result, a.night if a.night is not None else ev.day)
        elif a.kind in (ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON) and a.target is not None:
            kind: Literal['save', 'poison'] = 'save' if a.kind == ActKind.CLAIM_SAVE else 'poison'
            self._add_potion(ev, seat, kind, a.target, a.night if a.night is not None else ev.day)
        elif a.kind == ActKind.VOTE_INTENT and a.target is not None:
            self._intents.setdefault(ev.day, {})[seat] = a.target

    def _add_check(self, ev: Event, seat: Seat, target: Seat, result: Literal['wolf', 'good'], night: int) -> None:
        mine = self.checks_by(seat)
        if any(c.target == target and c.result == result and c.night == night for c in mine):
            return  # 重报同一条，不算新声明
        if night < 1 or night > self._nights:
            self._flag(seat, 'future_night', ev.id, f'{seat} 号报了第 {night} 夜的查验，但只过了 {self._nights} 夜')
        if target == seat:
            self._flag(seat, 'check_self', ev.id, f'{seat} 号报自己验了自己')
        died = self._deaths.get(target)
        if died is not None and died[0] < night:
            self._flag(seat, 'check_dead', ev.id, f'{seat} 号报第 {night} 夜验了 {target} 号，但他那时已经出局')
        if any(c.target == target and c.result != result for c in mine):
            self._flag(seat, 'check_conflict', ev.id, f'{seat} 号对 {target} 号前后报了不同的查验结果')
        if any(c.night == night and c.target != target for c in mine):
            self._flag(seat, 'double_check', ev.id, f'{seat} 号报第 {night} 夜验了两个人')
        self._checks.append(CheckClaim(seat=seat, target=target, result=result, night=night, day=ev.day,
                                       event_id=ev.id))

    def _add_potion(self, ev: Event, seat: Seat, kind: Literal['save', 'poison'], target: Seat, night: int) -> None:
        mine = self.potions_by(seat)
        if any(p.kind == kind and p.target == target and p.night == night for p in mine):
            return
        if night < 1 or night > self._nights:
            self._flag(seat, 'future_night', ev.id, f'{seat} 号报了第 {night} 夜用药，但只过了 {self._nights} 夜')
        deaths = self._dawns.get(night)
        if deaths is not None:
            if kind == 'save' and target in deaths:
                self._flag(seat, 'save_died', ev.id, f'{seat} 号说第 {night} 夜救了 {target} 号，但他那天早上死了')
            if kind == 'poison' and target not in deaths:
                self._flag(seat, 'poison_alive', ev.id,
                           f'{seat} 号说第 {night} 夜毒了 {target} 号，但他那天早上没死')
        if any(p.kind == kind and p.target != target for p in mine):
            self._flag(seat, 'double_save' if kind == 'save' else 'double_poison', ev.id,
                       f'{seat} 号说{"救" if kind == "save" else "毒"}过两个不同的人')
        if any(p.kind != kind and p.night == night for p in mine):
            self._flag(seat, 'two_potions', ev.id, f'{seat} 号说第 {night} 夜既用了解药又用了毒药')
        self._potions.append(PotionClaim(seat=seat, kind=kind, target=target, night=night, day=ev.day,
                                         event_id=ev.id))

    def _flag(self, seat: Seat, kind: ContradictionKind, event_id: int, text: str) -> None:
        self._contradictions.append(Contradiction(seat=seat, kind=kind, event_id=event_id, text=text))

    # ------------------------------------------------------------------ 查询

    def role_claims(self, seat: Seat | None = None, role: Role | None = None) -> list[RoleClaim]:
        """身份声明（每次改称记一条，重复自称同一身份不重复记），按时间先后。"""
        return [c for c in self._roles if (seat is None or c.seat == seat) and (role is None or c.role == role)]

    def current_role(self, seat: Seat) -> Role | None:
        mine = self.role_claims(seat)
        return mine[-1].role if mine else None

    def claimants(self, role: Role) -> list[Seat]:
        """当前自称该身份的人（含已出局的），按首次声明该身份的先后。"""
        first: dict[Seat, int] = {}
        for c in self._roles:
            if c.role == role:
                first.setdefault(c.seat, c.event_id)
        return [s for s, _ in sorted(first.items(), key=lambda kv: kv[1]) if self.current_role(s) == role]

    def checks_by(self, seat: Seat) -> list[CheckClaim]:
        return [c for c in self._checks if c.seat == seat]

    def checks_on(self, target: Seat) -> list[CheckClaim]:
        return [c for c in self._checks if c.target == target]

    def potions_by(self, seat: Seat) -> list[PotionClaim]:
        return [p for p in self._potions if p.seat == seat]

    def potion_claims(self) -> list[PotionClaim]:
        """全部用药声明（契约之外的便利查询）。"""
        return list(self._potions)

    def vote_intents(self, day: int) -> dict[Seat, Seat]:
        return dict(self._intents.get(day, {}))

    def contradictions(self, seat: Seat) -> list[Contradiction]:
        return [c for c in self._contradictions if c.seat == seat]

    def all_contradictions(self) -> list[Contradiction]:
        """全桌的矛盾（契约之外的便利查询）。"""
        return list(self._contradictions)

    def dawn_deaths(self, day: int) -> list[Seat] | None:
        """第 day 天早上公布的死者；还没天亮返回 None。平安夜是空表。"""
        d = self._dawns.get(day)
        return None if d is None else list(d)

    def death_of(self, seat: Seat) -> tuple[int, str] | None:
        """公开的死亡：(death.day, when)；还活着返回 None。"""
        return self._deaths.get(seat)

    @property
    def nights(self) -> int:
        return self._nights


def speech_acts(ev: Event) -> list[SpeechAct]:
    """取发言事件里的言语行为（坏掉的条目跳过）。不读文字。"""
    if ev.type != EventType.SPEECH:
        return []
    out: list[SpeechAct] = []
    for raw in (ev.data.get('speech') or {}).get('acts') or []:
        try:
            out.append(SpeechAct.model_validate(raw))
        except ValidationError:
            continue
    return out


def speech_flags(ev: Event) -> list[str]:
    """发言事件的标记（injection、stage_direction、truncated）。"""
    if ev.type != EventType.SPEECH:
        return []
    return [str(f) for f in (ev.data.get('speech') or {}).get('flags') or []]


def speech_kind(ev: Event) -> str:
    return str(ev.data.get('kind') or 'day')
