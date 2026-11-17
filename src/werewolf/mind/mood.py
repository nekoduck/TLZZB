"""念头、心情、压力与交情（ARCHITECTURE 4.1）。

- 念头 `Thought`：数值、来由（key、event_id）、过期日。心情 = 50 + 有效念头之和，压在 0 到 100。
  标 `private` 的念头不进公开心智。
- 压力单独累计，也分两层：公开层只由公开事件产生；私有层另加私有念头带来的压力与 `strain`
  （违背性格行事）。平安度过一天减压。
- 状态：`tilted`（心情 ≤ 25）优先于 `tense`（压力 ≥ 60），再其次 `confident`（心情 ≥ 70），其余 `steady`。
- 交情 `trust`：对每个别人 -1 到 +1。踩我、投我降（按记仇缩放），保我、与我同票升，每天向 0 回落。
  交情只由公开事件改变，所以它本身是公开的。

公开层只读公开事件与自己公开说过的话，所以狼人的公开心情与同座位平民的完全相同。从不读文字、不读 `is_human`。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from werewolf.kernel.claims import ClaimLedger, speech_acts
from werewolf.kernel.types import GODS, ROLE_ZH, ActKind, Event, EventType, Role, Seat, View
from werewolf.mind.types import MindState, MindTuning, PublicMood, Thought, Traits

Layer = Literal['public', 'private']
_LAYERS: tuple[Layer, Layer] = ('public', 'private')

_WF: dict[int, float] = {1: 0.6, 2: 1.0, 3: 1.3}
_ACCUSE = {ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.DOUBT_CLAIM}
_SUPPORT = {ActKind.VOUCH, ActKind.BACK_CLAIM, ActKind.DEFEND}


class MoodWeights(BaseModel):
    """念头的数值与压力增量。调参旋钮。"""

    model_config = ConfigDict(extra='forbid')

    accused: float = -7.0  # 被踩一次（weight=2），再乘 0.7 + 0.6 × 记仇
    accused_twice: float = -10.0  # 被同一人连着两天踩
    accused_many: float = -8.0  # 一天里被两个以上的人踩
    voted: float = -5.0  # 被投一票
    checked_wolf: float = -15.0  # 被人报成查杀
    gold: float = 8.0  # 被人报成金水
    vouched: float = 5.0  # 被保
    claim_contested: float = -10.0  # 自己公开自称的神职被人抢
    on_pk: float = -6.0  # 上了 PK 台
    push_won: float = 4.0  # 自己踩的、投的人出局了
    lost_friend: float = -6.0  # 交情好的人死了
    peaceful: float = 3.0  # 平安夜
    found_wolf: float = 6.0  # 私有：验出了狼
    mate_exposed: float = -8.0  # 私有：队友被报查杀
    mate_died: float = -12.0  # 私有：队友死了
    hunter_blocked: float = -6.0  # 私有：被毒，开不了枪
    stress_accused: float = 8.0
    stress_many: float = 10.0
    stress_voted: float = 6.0
    stress_checked: float = 15.0
    stress_contested: float = 12.0
    stress_pk: float = 10.0
    stress_mate: float = 10.0
    relief_quiet_day: float = 15.0  # 一天里没被踩、没被投，入夜时减压
    relief_rough_day: float = 5.0
    trust_decay: float = 0.9  # 每过一天交情乘它
    trust_accused: float = 0.1  # 被踩：-(它 + 0.3 × 记仇) × 轻重
    trust_voted: float = 0.15  # 被投：-(它 + 0.35 × 记仇)
    trust_vouched: float = 0.15
    trust_gold: float = 0.3
    trust_checked: float = 0.3  # 被报查杀：-(它 + 0.4 × 记仇)
    trust_covote: float = 0.08
    friend_threshold: float = 0.25  # 交情不低于它的人死了会难过


class Mood:
    """一个座位的念头与心情。见模块说明。"""

    def __init__(self, seat: Seat, traits: Traits, tuning: MindTuning | None = None,
                 weights: MoodWeights | None = None) -> None:
        self.seat = seat
        self.traits = traits
        self.tuning = tuning or MindTuning()
        self.w = weights or MoodWeights()
        self.day = 0
        self.last_event_id = 0
        self._thoughts: dict[str, Thought] = {}
        self._stress: dict[Layer, float] = {'public': 0.0, 'private': 0.0}
        self._trust: dict[Seat, float] = {}
        self._teammates: list[Seat] = []
        self._my_claim: Role | None = None
        self._accused_days: dict[str, list[int]] = {}  # 发言者 → 踩过我的日子
        self._done: set[str] = set()
        self._my_votes: dict[str, Seat] = {}  # 天|轮 → 我投的人
        self._my_pushes: dict[str, list[Seat]] = {}  # 天 → 我当天踩过、号召过的人
        self._rough: set[int] = set()  # 被踩或被投的日子

    # ------------------------------------------------------------------ 查询

    def thoughts(self, layer: Layer = 'private') -> list[Thought]:
        """当前有效的念头，按 |数值| 从大到小，同分新的在前。公开层不含私有念头。"""
        items = [t for t in self._thoughts.values() if layer == 'private' or not t.private]
        return sorted(items, key=lambda t: (-abs(t.value), -(t.event_id or 0), t.key))

    def value(self, layer: Layer = 'private') -> float:
        return max(0.0, min(100.0, 50.0 + sum(t.value for t in self.thoughts(layer))))

    def stress(self, layer: Layer = 'private') -> float:
        return self._stress[layer]

    def state(self, layer: Layer = 'private') -> MindState:
        v = self.value(layer)
        if v <= 25:
            return 'tilted'
        if self._stress[layer] >= 60:
            return 'tense'
        if v >= 70:
            return 'confident'
        return 'steady'

    def top_cause(self, layer: Layer = 'private') -> Thought | None:
        """分量最重的一条有效念头（写进 Decision.mood_cause 的就是它的 key）。"""
        items = self.thoughts(layer)
        return items[0] if items else None

    def trust(self, seat: Seat) -> float:
        return self._trust.get(seat, 0.0)

    def all_trust(self) -> dict[Seat, float]:
        return {s: v for s, v in sorted(self._trust.items()) if s != self.seat}

    def public(self) -> PublicMood:
        hints = [t.text for t in self.thoughts('public')[:2]]
        return PublicMood(state=self.state('public'), mood=round(self.value('public'), 1), hints=hints)

    # ------------------------------------------------------------------ 主动调整

    def strain(self, amount: float, cause_key: str, text: str, about: Seat | None = None,
               event_id: int | None = None) -> None:
        """违背性格行事（胆小的被迫跳身份、固执的被迫改票）：只加私有压力，并留一条私有念头。"""
        self._bump('private', amount)
        self._think(cause_key, text, -abs(amount) / 3, expires=self.day + 1, about=about, event_id=event_id,
                    private=True)

    def new_day(self, day: int) -> None:
        """入夜（day 加一）时调：念头过期、交情回落、减压。重复调用无副作用。"""
        while self.day < day:
            prev = self.day
            self.day += 1
            relief = self.w.relief_rough_day if prev in self._rough else self.w.relief_quiet_day
            for layer in _LAYERS:
                self._stress[layer] = max(0.0, self._stress[layer] - relief)
            self._trust = {s: v * self._decay(v) for s, v in self._trust.items() if abs(v * self._decay(v)) > 1e-3}
            self._thoughts = {k: t for k, t in self._thoughts.items()
                              if t.expires_day is None or t.expires_day >= self.day}

    # ------------------------------------------------------------------ 观察

    def observe(self, event: Event, view: View | None = None, ledger: ClaimLedger | None = None) -> None:
        """吃一条新近可见的（去掉文字的）事件。`view`、`ledger` 目前不需要，留作接口一致。"""
        if event.id <= self.last_event_id:
            return
        self.last_event_id = event.id
        if event.day > self.day:
            self.new_day(event.day)
        t = event.type
        if event.vis != 'public':
            self._on_private(event)
        elif t == EventType.SPEECH and event.actor is not None:
            if event.actor == self.seat:
                self._on_own_speech(event)
            else:
                self._on_speech(event, event.actor)
        elif t == EventType.VOTE and event.actor is not None:
            self._on_vote(event, event.actor)
        elif t == EventType.VOTE_RESULT:
            self._on_vote_result(event)
        elif t == EventType.DEATH and event.target is not None:
            self._on_death(event, event.target)
        elif t == EventType.DAWN and event.data.get('peaceful'):
            self._think('peaceful', '昨夜是平安夜', self.w.peaceful, expires=event.day, event_id=event.id)

    def _on_private(self, ev: Event) -> None:
        if ev.type == EventType.WOLF_TEAM:
            wolves = [int(s) for s in ev.data.get('wolves') or []]
            if self.seat in wolves:
                self._teammates = [s for s in wolves if s != self.seat]
        elif ev.type == EventType.SEER_RESULT and ev.actor == self.seat and ev.target is not None \
                and ev.data.get('result') == 'wolf':
            self._think(f'found_wolf:{ev.target}', f'验出 {ev.target} 号是狼', self.w.found_wolf,
                        expires=ev.day + 1, about=ev.target, event_id=ev.id, private=True)
        elif ev.type == EventType.HUNTER_BLOCKED and ev.target == self.seat:
            self._think('hunter_blocked', '被毒了，开不了枪', self.w.hunter_blocked, expires=ev.day + 1,
                        event_id=ev.id, private=True)

    def _on_own_speech(self, ev: Event) -> None:
        for a in speech_acts(ev):
            if a.kind == ActKind.CLAIM_ROLE and a.role is not None:
                self._my_claim = a.role
            elif a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT) and a.target is not None:
                lst = self._my_pushes.setdefault(str(ev.day), [])
                if a.target not in lst:
                    lst.append(a.target)

    def _on_speech(self, ev: Event, src: Seat) -> None:
        for a in speech_acts(ev):
            tgt = a.target
            if a.kind in _ACCUSE and tgt == self.seat:
                self._accused(ev, src, a.weight)
            elif a.kind in _SUPPORT and tgt == self.seat:
                key = f'vouch|{ev.day}|{src}'
                if key not in self._done:
                    self._done.add(key)
                    self._think(f'vouched_by:{src}', f'{src} 号替我说话', self.w.vouched, expires=ev.day + 1,
                                about=src, event_id=ev.id)
                    self._adjust_trust(src, self.w.trust_vouched)
            elif a.kind == ActKind.CLAIM_CHECK and tgt == self.seat:
                key = f'check|{src}|{a.result}'
                if key in self._done:
                    continue
                self._done.add(key)
                if a.result == 'wolf':
                    self._think(f'checked_wolf_by:{src}', f'{src} 号报我是狼', self.w.checked_wolf,
                                expires=ev.day + 2, about=src, event_id=ev.id)
                    self._bump('both', self.w.stress_checked)
                    self._adjust_trust(src, -(self.w.trust_checked + 0.4 * self.traits.vengeful))
                    self._rough.add(ev.day)
                else:
                    self._think(f'gold_by:{src}', f'{src} 号给我发了金水', self.w.gold, expires=ev.day + 1,
                                about=src, event_id=ev.id)
                    self._adjust_trust(src, self.w.trust_gold)
            elif a.kind == ActKind.CLAIM_CHECK and a.result == 'wolf' and tgt in self._teammates:
                self._think(f'mate_exposed:{tgt}', f'队友 {tgt} 号被报了查杀', self.w.mate_exposed,
                            expires=ev.day + 1, about=tgt, event_id=ev.id, private=True)
                self._bump('private', self.w.stress_mate)
            elif a.kind == ActKind.CLAIM_ROLE and a.role is not None and a.role == self._my_claim \
                    and a.role in GODS:
                key = f'contest|{src}|{a.role.value}'
                if key not in self._done:
                    self._done.add(key)
                    self._think(f'claim_contested:{src}', f'{src} 号跟我抢{ROLE_ZH[a.role]}的身份',
                                self.w.claim_contested, expires=ev.day + 2, about=src, event_id=ev.id)
                    self._bump('both', self.w.stress_contested)

    def _accused(self, ev: Event, src: Seat, weight: int) -> None:
        days = self._accused_days.setdefault(str(src), [])
        twice = (ev.day - 1) in days
        if ev.day not in days:
            days.append(ev.day)
        scale = 0.7 + 0.6 * self.traits.vengeful
        if twice:
            value, text, expires = self.w.accused_twice * scale, f'{src} 号连着两天踩我', ev.day + 3
        else:
            value, text, expires = self.w.accused * scale * _WF.get(weight, 1.0), f'{src} 号当众踩我', ev.day + 2
        key = f'accused_by:{src}'
        old = self._thoughts.get(key)
        if old is None or old.day < ev.day or value < old.value:
            self._think(key, text, value, expires=expires, about=src, event_id=ev.id)
        once = f'accused|{ev.day}|{src}'
        if once not in self._done:
            self._done.add(once)
            self._rough.add(ev.day)
            self._bump('both', self.w.stress_accused)
            self._adjust_trust(src, -(self.w.trust_accused + 0.3 * self.traits.vengeful) * _WF.get(weight, 1.0))
            n = sum(1 for d in self._accused_days.values() if ev.day in d)
            if n >= 2 and f'many|{ev.day}' not in self._done:
                self._done.add(f'many|{ev.day}')
                self._think('accused_by_many', f'第 {ev.day} 天好几个人一起踩我', self.w.accused_many,
                            expires=ev.day + 1, event_id=ev.id)
                self._bump('both', self.w.stress_many)

    def _on_vote(self, ev: Event, voter: Seat) -> None:
        rnd = str(ev.data.get('round', 'main'))
        if voter == self.seat:
            if ev.target is not None:
                self._my_votes[f'{ev.day}|{rnd}'] = ev.target
            return
        if ev.target != self.seat:
            return
        self._rough.add(ev.day)
        key = f'voted_by:{voter}'
        old = self._thoughts.get(key)
        value = self.w.voted * (0.7 + 0.6 * self.traits.vengeful)
        if old is not None and old.day == ev.day:
            value = min(old.value, value)
        self._think(key, f'{voter} 号投了我', value, expires=ev.day + 2, about=voter, event_id=ev.id)
        self._bump('both', self.w.stress_voted)
        self._adjust_trust(voter, -(self.w.trust_voted + 0.35 * self.traits.vengeful))

    def _on_vote_result(self, ev: Event) -> None:
        rnd = str(ev.data.get('round', 'main'))
        if self.seat in (ev.data.get('tied') or []) and ev.data.get('outcome') == 'pk':
            self._think('on_pk', '被推上了 PK 台', self.w.on_pk, expires=ev.day, event_id=ev.id)
            self._bump('both', self.w.stress_pk)
        for row in ev.data.get('tally') or []:
            voters = [int(s) for s in row.get('voters') or []]
            if self.seat in voters and row.get('target') != self.seat:
                for s in voters:
                    if s != self.seat:
                        self._adjust_trust(s, self.w.trust_covote)
        out = ev.target
        if out is not None and out != self.seat:
            mine = set(self._my_pushes.get(str(ev.day), [])) | {self._my_votes.get(f'{ev.day}|{rnd}')}
            if out in mine:
                self._think('push_won', f'我推的 {out} 号出局了', self.w.push_won, expires=ev.day + 1, about=out,
                            event_id=ev.id)

    def _on_death(self, ev: Event, dead: Seat) -> None:
        if dead == self.seat:
            return
        if dead in self._teammates:
            self._think(f'mate_died:{dead}', f'队友 {dead} 号出局了', self.w.mate_died, expires=ev.day + 1,
                        about=dead, event_id=ev.id, private=True)
            self._bump('private', self.w.stress_mate)
        if self.trust(dead) >= self.w.friend_threshold:
            self._think(f'lost_friend:{dead}', f'信得过的 {dead} 号出局了', self.w.lost_friend, expires=ev.day + 1,
                        about=dead, event_id=ev.id)

    # ------------------------------------------------------------------ 内部

    def _think(self, key: str, text: str, value: float, *, expires: int | None, about: Seat | None = None,
               event_id: int | None = None, private: bool = False) -> None:
        self._thoughts[key] = Thought(key=key, text=text, value=round(value, 3), day=self.day, expires_day=expires,
                                      about=about, event_id=event_id, private=private)

    def _bump(self, layer: Literal['public', 'private', 'both'], amount: float) -> None:
        layers: tuple[Layer, ...] = _LAYERS if layer == 'both' else (layer,)
        for name in layers:
            self._stress[name] = max(0.0, min(100.0, self._stress[name] + amount))

    def _decay(self, v: float) -> float:
        """交情每天的回落系数：记仇的人旧怨散得慢（负值的回落按记仇放缓）。"""
        d = self.w.trust_decay
        return d + (1.0 - d) * 0.6 * self.traits.vengeful if v < 0 else d

    def _adjust_trust(self, seat: Seat, delta: float) -> None:
        if seat == self.seat:
            return
        self._trust[seat] = max(-1.0, min(1.0, self._trust.get(seat, 0.0) + delta))

    # ------------------------------------------------------------------ 存取

    def dump(self) -> dict[str, Any]:
        return {
            'seat': self.seat, 'day': self.day, 'last_event_id': self.last_event_id,
            'thoughts': [t.model_dump(mode='json') for t in self._thoughts.values()],
            'stress': dict(self._stress), 'trust': [[s, v] for s, v in sorted(self._trust.items())],
            'teammates': list(self._teammates), 'my_claim': self._my_claim.value if self._my_claim else None,
            'accused_days': {k: list(v) for k, v in self._accused_days.items()}, 'done': sorted(self._done),
            'my_votes': dict(self._my_votes), 'my_pushes': {k: list(v) for k, v in self._my_pushes.items()},
            'rough': sorted(self._rough),
        }

    def load(self, data: dict[str, Any]) -> None:
        self.day = int(data['day'])
        self.last_event_id = int(data['last_event_id'])
        self._thoughts = {t['key']: Thought.model_validate(t) for t in data['thoughts']}
        self._stress = {'public': float(data['stress']['public']), 'private': float(data['stress']['private'])}
        self._trust = {int(s): float(v) for s, v in data['trust']}
        self._teammates = [int(s) for s in data['teammates']]
        self._my_claim = Role(data['my_claim']) if data['my_claim'] else None
        self._accused_days = {k: [int(d) for d in v] for k, v in data['accused_days'].items()}
        self._done = set(data['done'])
        self._my_votes = {k: int(v) for k, v in data['my_votes'].items()}
        self._my_pushes = {k: [int(s) for s in v] for k, v in data['my_pushes'].items()}
        self._rough = {int(d) for d in data['rough']}

