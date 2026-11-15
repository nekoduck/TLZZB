"""怀疑度：对每个别人一个「他是狼」的概率，两层（公开 / 私有），所有身份都一样。

契约见 ARCHITECTURE 4.1。要点：

- **公开层**：一个坐在这个座位上的平民，看着同样的公开事件会有的判断。只吃公开事件，只知道
  「我自己是好人」，并且相信自己公开说过的身份（所以悍跳的狼的公开层就是「我是预言家」的伪装视角）。
  它从不读真实身份，所以狼人的公开层与同座位平民的公开层逐位相同。
- **私有层**：公开层的全部证据，再叠加硬知识（自己的查验、狼队友表、公开翻出的身份）与私有的
  软知识（女巫知道的刀口、自己身份被人冒领）。
- 内部用对数几率累加。每次调整记一条 `Evidence`（座位、增量、理由代码、事件 id、哪一天、哪一层），
  所以任何怀疑都说得出来由。证据分两类：
  - 累加证据：事件到来时按当时的局面算一次，此后不变（指认、担保、被投、夜死、矛盾、场外……）；
  - 重算证据（`derived=True`）：由声明账本与票型在每次观察后整批重算（预言家的查杀与金水、对跳、
    银水、谁投了好人、谁和疑似狼同票），这样后来出现的对跳会回头分走先前查验的信用。
- 归一化：把对数几率整体平移，使「身份不明的座位里的期望狼数」等于 狼总数 − 已知的狼。平移不记证据
  （它不是某条事件的作用），所以证据增量是「相对」的。
- 从不读 `is_human`，也从不读发言文字。

调用约定：每条对本座位可见的（去掉文字的）事件调一次 `observe(event, view, ledger)`；`ledger` 是心智
共用的 `ClaimLedger`（这里会防御性地 `ledger.add(event)`，账本的 `add` 是幂等的）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from werewolf.kernel.claims import ClaimLedger, speech_acts, speech_flags, speech_kind
from werewolf.kernel.types import ActKind, Event, EventType, GameConfig, Role, Seat, View
from werewolf.mind.types import MindTuning, ReasonCode, Traits

Layer = Literal['public', 'private']
LAYERS: tuple[Layer, Layer] = ('public', 'private')

HARD_DELTA = 8.0  # 硬知识证据记录用的名义增量（概率直接取 0 或 1）
_WF: dict[int, float] = {0: 0.0, 1: 0.6, 2: 1.0, 3: 1.3}  # 言语行为的轻重 → 系数
_GOD_ROLES: tuple[Role, ...] = (Role.SEER, Role.WITCH, Role.HUNTER)
_ACCUSE_KINDS = {ActKind.ACCUSE: 1.0, ActKind.VOTE_INTENT: 0.8, ActKind.DOUBT_CLAIM: 0.6}
_VOUCH_KINDS = {ActKind.VOUCH: 1.0, ActKind.BACK_CLAIM: 0.6, ActKind.DEFEND: 0.5}


class BeliefWeights(BaseModel):
    """怀疑度的各项权重（对数几率）。调参旋钮；默认值即设计值。"""

    model_config = ConfigDict(extra='forbid')

    accuse: float = 0.5  # 一次 weight=2 的指认，发言者完全可信、从众 0.5 时挪动多少
    vouch: float = 0.4  # 担保（负向）
    accused_me: float = 0.6  # 他踩我（作用在发言者身上），再乘 0.3 + 1.2 × 记仇
    voted_me: float = 0.5  # 他投我，同上缩放
    many_accusers: int = 3  # 一天里这么多人指认同一人时触发 ACCUSED_BY_MANY
    accused_by_many: float = 0.3  # 再乘 0.5 + 从众
    defended_wolf: float = 0.8  # 替疑似狼说话（乘 (p − 0.5) 与理性系数）
    night_death: float = -1.0  # 夜里死的人大概率是好人
    dead_accused: float = 0.45  # 夜死者生前（或遗言里）指认过的人
    hunter_shot: float = 0.5  # 猎人开枪带走的人
    contradiction: float = 1.0  # 账本报出的一条矛盾，再乘理性系数
    off_game: float = 0.4  # 场外（injection），每人每天至多一次；不大于 accuse
    quiet: float = 0.15  # 白天发言只说「过」或什么都没说
    late_claim: float = 0.4  # 被逼到台上才亮神职
    late_trust: float = 0.35  # 台上才亮的神职，声明可信度再乘它
    check_wolf: float = 3.0  # 预言家声明的查杀，乘声明可信度与理性系数
    check_good: float = 1.6  # 金水（负向）
    checked_me_wolf: float = 3.0  # 有人报我是狼：我知道自己是好人，他在说谎
    counter_claim: float = 0.5  # 对跳：每个声称者
    own_role_taken: float = 4.0  # 私有层：别人冒领我的真实身份
    own_claim_taken: float = 3.0  # 公开层：别人抢我公开自称的身份
    uncontested: float = -0.5  # 唯一的预言家声明，没人对跳
    silver_water: float = -1.0  # 银水，乘可信度；只在声明那夜确实是平安夜时
    poison_claim: float = 1.2  # 女巫说毒了谁（且那人那天早上确实死了），乘可信度
    witch_victim: float = -1.5  # 私有：女巫知道的刀口
    voted_good: float = 0.6  # 把票投给了大概率好人的人
    voted_with_wolf: float = 0.3  # 和疑似狼同票
    stubborn_discount: float = 0.7  # 固执 1.0 时，对头号怀疑的「洗白」证据打掉的比例
    credibility_slope: float = 1.6  # 发言者可信度 = 1 − 斜率 × p(发言者)，压在 [-0.3, 1]
    # 对跳的复盘（都由公开事实重算，乘理性系数）：
    killed_rival: float = 1.5  # 对跳的一位夜里被刀（狼几乎不刀自家悍跳的）：另一位更像狼
    check_refuted: float = 2.0  # 查验被公开事实推翻：查杀的人夜里独死（刀口），或查杀了坐实的女巫、金水被坐实的毒
    rival_check: float = 0.8  # 后跳的一位起跳当场就报先跳的那位是狼：悍跳的老套路（真预言家首夜恰好验到他的机会很小）
    check_confirmed: float = -0.8  # 查杀的人被坐实的毒药毒死
    witch_verified: float = -1.2  # 唯一的女巫声明，用药与天亮的死讯对得上（不是被逼到台上才亮的）
    backed_fake: float = 0.7  # 站边、担保、投票帮过的预言家声明者越像狼，站他的人越可疑（乘 2 × (p − 0.5)）
    fake_gold: float = 0.6  # 像狼的预言家声明者给的金水：常常是他的队友（同上缩放）


class Evidence(BaseModel):
    """一条证据：某层里某座位的对数几率挪动了多少、为什么、因为哪条事件。"""

    model_config = ConfigDict(extra='forbid')

    seat: Seat  # 被调整的座位
    delta: float  # 对数几率增量（正 = 更像狼）
    reason: ReasonCode
    event_id: int | None  # 引起它的事件；公开层的证据（除 REFLECTION）都来自公开事件
    day: int
    layer: Layer
    source: Seat | None = None  # 由谁的言行引起（指认者、投票者、声称者）
    hard: bool = False  # 硬知识：概率直接定为 0 或 1
    derived: bool = False  # 由账本与票型重算，每次观察后整批替换


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _k(*parts: Any) -> str:
    return '|'.join(str(p) for p in parts)


class _LayerState:
    def __init__(self, name: Layer) -> None:
        self.name: Layer = name
        self.acc: list[Evidence] = []
        self.derived: list[Evidence] = []
        self.hard: dict[Seat, float] = {}
        self.cache: dict[Seat, float] | None = None


class Beliefs:
    """怀疑度。见模块说明。"""

    def __init__(self, seat: Seat, role: Role, traits: Traits, config: GameConfig | None = None,
                 tuning: MindTuning | None = None, weights: BeliefWeights | None = None) -> None:
        self.seat = seat
        self.role = role
        self.traits = traits
        self.config = config or GameConfig()
        self.tuning = tuning or MindTuning()
        self.w = weights or BeliefWeights()
        self.others: list[Seat] = [s for s in range(1, self.config.seats + 1) if s != seat]
        self.n_wolves = sum(1 for r in self.config.roles if r == Role.WEREWOLF)
        self.day = 0
        self.last_event_id = 0
        self._layers: dict[Layer, _LayerState] = {name: _LayerState(name) for name in LAYERS}
        # 累加证据的簿记（键都是字符串，便于 dump）
        self._applied: dict[str, int] = {}  # 天|发言者|目标|行为 → 已计入的最大 weight
        self._cap_used: dict[str, float] = {}  # 层|天|发言者|目标 → 已挪动的对数几率
        self._accusers: dict[str, list[Seat]] = {}  # 天|目标 → 指认者
        self._done: set[str] = set()  # 各种「至多一次」的标记
        self._accusations: dict[str, dict[str, list[int]]] = {}  # 发言者 → 目标 → [事件 id, 最大 weight]
        self._night_dead: list[Seat] = []
        self._votes: list[list[Any]] = []  # [事件 id, 天, 轮, 投票者, 目标]
        self._late: dict[str, int] = {}  # 座位|身份 → 声明事件 id
        self._backs: list[list[int]] = []  # [事件 id, 天, 发言者, 被站边的预言家声明者]
        self._ledger = ClaimLedger()  # 调用方不给账本时用的内部账本
        self._ledger_stale = False
        self._last_ledger: ClaimLedger | None = None  # 最近一次 observe 用的账本（查询时缺省用它）

    # ------------------------------------------------------------------ 查询

    def p(self, seat: Seat, layer: Layer = 'private') -> float:
        """seat 是狼的概率。自己：公开层恒为 0（我是好人）；私有层按真实阵营。"""
        if seat == self.seat:
            return 1.0 if (layer == 'private' and self.role == Role.WEREWOLF) else 0.0
        st = self._layers[layer]
        if seat in st.hard:
            return st.hard[seat]
        return self._probs(st).get(seat, 0.0)

    def all(self, layer: Layer = 'private') -> dict[Seat, float]:
        """每个别人（含已出局的）是狼的概率。"""
        return {s: self.p(s, layer) for s in self.others}

    def ranked(self, layer: Layer = 'private', living: Sequence[Seat] | None = None) -> list[tuple[Seat, float]]:
        """按怀疑度从高到低；`living` 给了就只留其中的座位（自己总被排除）。"""
        pool = [s for s in self.others if living is None or s in living]
        return sorted(((s, self.p(s, layer)) for s in pool), key=lambda kv: (-kv[1], kv[0]))

    def evidence(self, seat: Seat, layer: Layer = 'private') -> list[Evidence]:
        """seat 的全部证据，按 |增量| 从大到小（硬知识排最前）。"""
        st = self._layers[layer]
        items = [e for e in st.acc + st.derived if e.seat == seat]
        return sorted(items, key=lambda e: (not e.hard, -abs(e.delta), -(e.event_id or 0)))

    def is_hard(self, seat: Seat, layer: Layer = 'private') -> bool:
        """seat 的身份阵营在该层是否是硬知识（自己总是）。"""
        return seat == self.seat or seat in self._layers[layer].hard

    def claim_trust(self, claimant: Seat, layer: Layer = 'private', role: Role | None = None,
                    ledger: ClaimLedger | None = None) -> float:
        """claimant 当前的身份声明为真的概率；用来给他报的查验、用药加权。

        同一神职有几个人声称时，至多一个是真的，信用按「他是好人的概率」分摊。没有声明时返回
        「他是好人的概率」。`role` 缺省取他当前自称的身份。
        """
        lg = ledger or self._last_ledger or self._ledger
        st = self._layers[layer]
        role = role or lg.current_role(claimant)
        if role is None or role not in _GOD_ROLES:
            return 1.0 - self.p(claimant, layer)
        probs = {s: self.p(s, layer) for s in self.others}
        return self._trusts(st, lg, probs, role).get(claimant, 0.0)

    # ------------------------------------------------------------------ 观察

    def observe(self, event: Event, view: View | None = None, ledger: ClaimLedger | None = None) -> None:
        """吃一条新近可见的（去掉文字的）事件。旧事件（编号不大于已吃过的）忽略。"""
        if event.id <= self.last_event_id:
            return
        if ledger is None:
            if self._ledger_stale and view is not None:
                self._ledger = ClaimLedger.from_events([e for e in view.events if e.id < event.id])
            self._ledger_stale = False
            ledger = self._ledger
        self._last_ledger = ledger
        ledger.add(event)
        self.last_event_id = event.id
        self.day = max(self.day, event.day)
        layers: list[_LayerState] = (
            [self._layers['public'], self._layers['private']] if event.vis == 'public' else [self._layers['private']]
        )
        t = event.type
        if event.vis == 'public':
            if t == EventType.SPEECH and event.actor is not None:
                self._on_speech(event, event.actor, layers, ledger)
            elif t == EventType.VOTE and event.actor is not None:
                self._on_vote(event, layers)
            elif t == EventType.DEATH and event.target is not None:
                self._on_death(event, layers)
            elif t == EventType.HUNTER_SHOT and event.target is not None and event.actor is not None:
                for st in layers:
                    self._add(st, event, event.target, self.w.hunter_shot, ReasonCode.DEAD_ACCUSED,
                              source=event.actor)
            elif t == EventType.ROLE_REVEALED and event.target is not None:
                role = Role(event.data.get('role'))
                for st in layers:
                    self._set_hard(st, event, event.target, role == Role.WEREWOLF, ReasonCode.CLAIM_UNCONTESTED)
        else:
            self._on_private(event)
        self._recompute(ledger)

    # ---- 私有事件 ----

    def _on_private(self, ev: Event) -> None:
        st = self._layers['private']
        if ev.type == EventType.WOLF_TEAM and self.role == Role.WEREWOLF:
            wolves = {int(s) for s in ev.data.get('wolves') or []}
            for s in self.others:
                self._set_hard(st, ev, s, s in wolves, ReasonCode.TEAM)
        elif ev.type == EventType.SEER_RESULT and ev.actor == self.seat and ev.target is not None:
            self._set_hard(st, ev, ev.target, ev.data.get('result') == 'wolf', ReasonCode.SEER_CHECK)
        elif ev.type in (EventType.WITCH_NOTICE, EventType.WITCH_SAVE) and ev.target is not None \
                and self.role == Role.WITCH:
            key = _k('witch_victim', ev.day)
            if key not in self._done:
                self._done.add(key)
                self._add(st, ev, ev.target, self.w.witch_victim, ReasonCode.KILL_PATTERN)

    # ---- 公开事件 ----

    def _on_speech(self, ev: Event, src: Seat, layers: list[_LayerState], ledger: ClaimLedger) -> None:
        acts = speech_acts(ev)
        kind = speech_kind(ev)
        # 新出现的矛盾：对所有发言者一视同仁（含自己——自己的矛盾别人也看得见，但对自己的怀疑没有意义）
        if src != self.seat:
            for c in ledger.contradictions(src):
                key = _k('contra', c.kind, c.event_id)
                if c.event_id == ev.id and key not in self._done:
                    self._done.add(key)
                    for st in layers:
                        self._add(st, ev, src, self.w.contradiction * self._logic(), ReasonCode.CONTRADICTION,
                                  source=src)
        # 台上才亮的神职
        for a in acts:
            if a.kind == ActKind.CLAIM_ROLE and a.role in _GOD_ROLES:
                first = ledger.role_claims(src, a.role)
                died = ledger.death_of(src)
                forced = kind == 'pk' or (kind == 'last_words' and died is not None and died[1] == 'vote')
                key = _k(src, a.role.value)
                if forced and first and first[0].event_id == ev.id and key not in self._late:
                    self._late[key] = ev.id
                    if src != self.seat:
                        for st in layers:
                            self._add(st, ev, src, self.w.late_claim, ReasonCode.LATE_CLAIM, source=src)
        if src == self.seat:
            return  # 自己的指认与担保是自己的判断，不是新证据
        cred = {st.name: self._credibility(st, src) for st in layers}
        top = {st.name: self._top(st) for st in layers}
        for a in acts:
            tgt = a.target
            if tgt is None or tgt == src:
                continue
            if a.kind in _ACCUSE_KINDS:
                if tgt == self.seat:
                    self._accused_me(ev, src, a.weight, layers)
                    continue
                factor = _ACCUSE_KINDS[a.kind] * self.w.accuse * (0.5 + self.traits.conformity)
                self._push(ev, src, tgt, a.kind, a.weight, factor, layers, cred, top, ReasonCode.ACCUSED_BY_MANY)
                rec = self._accusations.setdefault(str(src), {}).setdefault(str(tgt), [ev.id, 0])
                rec[1] = max(rec[1], a.weight)
                if src in self._night_dead:
                    self._dead_accused(ev, src, tgt, layers)
                self._count_accuser(ev, src, tgt, layers)
            elif a.kind in _VOUCH_KINDS and tgt != self.seat:
                if a.kind in (ActKind.BACK_CLAIM, ActKind.VOUCH) and Role.SEER in self._claimed_roles(ledger, tgt):
                    self._backs.append([ev.id, ev.day, src, tgt])
                factor = -_VOUCH_KINDS[a.kind] * self.w.vouch * (0.5 + self.traits.conformity)
                self._push(ev, src, tgt, a.kind, a.weight, factor, layers, cred, top, ReasonCode.VOUCHED_BY_TRUSTED)
                for st in layers:
                    pt = self.p(tgt, st.name)
                    key = _k('defwolf', st.name, ev.day, src, tgt)
                    if pt >= 0.65 and key not in self._done and src not in st.hard:
                        self._done.add(key)
                        self._add(st, ev, src, self.w.defended_wolf * (pt - 0.5) * self._logic(),
                                  ReasonCode.DEFENDED_WOLF, source=src)
        flags = speech_flags(ev)
        if 'injection' in flags:
            key = _k('offgame', ev.day, src)
            if key not in self._done:
                self._done.add(key)
                for st in layers:
                    self._add(st, ev, src, min(self.w.off_game, self.w.accuse), ReasonCode.OFF_GAME, source=src)
        if kind == 'day' and all(a.kind == ActKind.PASS for a in acts):
            key = _k('quiet', ev.day, src)
            if key not in self._done:
                self._done.add(key)
                for st in layers:
                    self._add(st, ev, src, self.w.quiet * self._logic(), ReasonCode.QUIET, source=src)

    def _push(self, ev: Event, src: Seat, tgt: Seat, kind: ActKind, weight: int, factor: float,
              layers: list[_LayerState], cred: dict[str, float], top: dict[str, Seat | None],
              reason: ReasonCode) -> None:
        """指认、担保、号召：同一行为只按当天说过的最重一次计；同一发言者对同一目标一天合计封顶。"""
        akey = _k(ev.day, src, tgt, kind.value)
        prev = self._applied.get(akey, 0)
        if weight <= prev:
            return
        self._applied[akey] = weight
        for st in layers:
            if tgt in st.hard:
                continue
            delta = factor * (_WF[weight] - _WF[prev]) * cred[st.name]
            delta = self._stubborn(delta, tgt, top[st.name])
            ckey = _k(st.name, ev.day, src, tgt)
            room = max(0.0, self.tuning.speaker_cap - self._cap_used.get(ckey, 0.0))
            delta = max(-room, min(room, delta))
            if delta == 0:
                continue
            self._cap_used[ckey] = self._cap_used.get(ckey, 0.0) + abs(delta)
            self._add(st, ev, tgt, delta, reason, source=src)

    def _accused_me(self, ev: Event, src: Seat, weight: int, layers: list[_LayerState]) -> None:
        key = _k(ev.day, src, 'me')
        prev = self._applied.get(key, 0)
        if weight <= prev:
            return
        self._applied[key] = weight
        delta = self.w.accused_me * (_WF[weight] - _WF[prev]) * self._vengeance()
        for st in layers:
            self._add(st, ev, src, delta, ReasonCode.ACCUSED_ME, source=src)

    def _count_accuser(self, ev: Event, src: Seat, tgt: Seat, layers: list[_LayerState]) -> None:
        key = _k(ev.day, tgt)
        lst = self._accusers.setdefault(key, [])
        if src in lst:
            return
        lst.append(src)
        if len(lst) == self.w.many_accusers:
            for st in layers:
                self._add(st, ev, tgt, self.w.accused_by_many * (0.5 + self.traits.conformity),
                          ReasonCode.ACCUSED_BY_MANY)

    def _dead_accused(self, ev: Event, dead: Seat, tgt: Seat, layers: list[_LayerState]) -> None:
        key = _k('deadacc', dead, tgt)
        if key in self._done or tgt == self.seat:
            return
        self._done.add(key)
        weight = self._accusations.get(str(dead), {}).get(str(tgt), [0, 2])[1]
        for st in layers:
            self._add(st, ev, tgt, self.w.dead_accused * _WF[max(1, weight)] * self._logic(),
                      ReasonCode.DEAD_ACCUSED, source=dead)

    def _on_vote(self, ev: Event, layers: list[_LayerState]) -> None:
        voter, tgt = ev.actor, ev.target
        self._votes.append([ev.id, ev.day, str(ev.data.get('round', 'main')), voter, tgt])
        if tgt == self.seat and voter != self.seat and voter is not None:
            for st in layers:
                self._add(st, ev, voter, self.w.voted_me * self._vengeance(), ReasonCode.VOTED_ME, source=voter)

    def _on_death(self, ev: Event, layers: list[_LayerState]) -> None:
        dead = ev.target
        assert dead is not None
        if ev.data.get('when') != 'night':
            return
        if dead not in self._night_dead:
            self._night_dead.append(dead)
        for st in layers:
            self._add(st, ev, dead, self.w.night_death, ReasonCode.KILL_PATTERN)
        for tgt in sorted(int(t) for t in self._accusations.get(str(dead), {})):
            self._dead_accused(ev, dead, tgt, layers)

    # ------------------------------------------------------------------ 重算证据（声明与票型）

    def _recompute(self, ledger: ClaimLedger) -> None:
        for st in self._layers.values():
            st.derived = []
            st.cache = None
            base = self._base(st)
            probs0 = self._normalize(st, base)
            top = max(probs0, key=lambda s: (probs0[s], -s)) if probs0 else None
            derived: list[Evidence] = []
            probs = probs0
            for _ in range(2):
                derived = self._claim_evidence(st, ledger, probs, top)
                probs = self._normalize(st, self._sum(base, derived))
            derived += self._vote_evidence(st, probs)
            st.derived = derived
            st.cache = None

    def _claim_evidence(self, st: _LayerState, ledger: ClaimLedger, probs: dict[Seat, float],
                        top: Seat | None) -> list[Evidence]:
        out: list[Evidence] = []
        lw = self._logic()
        my_claim = ledger.current_role(self.seat)

        def add(seat: Seat, delta: float, reason: ReasonCode, eid: int, day: int, source: Seat | None) -> None:
            if seat == self.seat or seat in st.hard or delta == 0:
                return
            out.append(Evidence(seat=seat, delta=delta, reason=reason, event_id=eid, day=day, layer=st.name,
                                source=source, derived=True))

        for role in _GOD_ROLES:
            cl = self._claimers(ledger, role)
            for c in cl:
                rc = (ledger.role_claims(c, role) or [None])[0]
                eid = rc.event_id if rc else ledger.potions_by(c)[0].event_id
                day = rc.day if rc else ledger.potions_by(c)[0].day
                if c != self.seat and st.name == 'private' and self.role == role:
                    add(c, self.w.own_role_taken, ReasonCode.COUNTER_CLAIM, eid, day, c)
                elif c != self.seat and st.name == 'public' and my_claim == role:
                    add(c, self.w.own_claim_taken, ReasonCode.COUNTER_CLAIM, eid, day, c)
                elif len(cl) >= 2:
                    add(c, self.w.counter_claim, ReasonCode.COUNTER_CLAIM, eid, day, c)
                elif role == Role.SEER and _k(c, role.value) not in self._late:
                    add(c, self.w.uncontested, ReasonCode.CLAIM_UNCONTESTED, eid, day, c)
        # 预言家声明的查验
        trust = self._trusts(st, ledger, probs, Role.SEER)
        for c in ledger.claimants(Role.SEER):
            tr = trust.get(c, 0.0)
            latest: dict[Seat, Any] = {}
            for chk in ledger.checks_by(c):
                latest[chk.target] = chk
            for tgt, chk in latest.items():
                if tgt == self.seat:
                    if chk.result == 'wolf' and not (st.name == 'private' and self.role == Role.WEREWOLF):
                        add(c, self.w.checked_me_wolf, ReasonCode.CONTRADICTION, chk.event_id, chk.day, c)
                    continue
                if tgt == c:
                    continue
                rival = ledger.role_claims(tgt, Role.SEER)
                if rival and ledger.current_role(tgt) == Role.SEER and rival[0].event_id < chk.event_id:
                    # 对方已经自称预言家之后才报他是狼：悍跳的一方总会这么报，这条查验不比对跳本身多带信息
                    continue
                d = tr * lw * (self.w.check_wolf if chk.result == 'wolf' else -self.w.check_good)
                add(tgt, self._stubborn(d, tgt, top), ReasonCode.SEER_CHECK, chk.event_id, chk.day, c)
        # 女巫的银水与毒药
        wtrust = self._trusts(st, ledger, probs, Role.WITCH)
        for pc in ledger.potion_claims():
            tr = wtrust.get(pc.seat, 0.0)
            deaths = ledger.dawn_deaths(pc.night)
            if deaths is None or tr <= 0:
                continue
            if pc.kind == 'save' and not deaths:
                add(pc.target, self._stubborn(self.w.silver_water * tr * lw, pc.target, top),
                    ReasonCode.SAVED_BY_WITCH, pc.event_id, pc.day, pc.seat)
            elif pc.kind == 'poison' and pc.target in deaths:
                add(pc.target, self.w.poison_claim * tr * lw, ReasonCode.KILL_PATTERN, pc.event_id, pc.day, pc.seat)
        out += self._duel_evidence(st, ledger, probs, wtrust)
        return out

    def _duel_evidence(self, st: _LayerState, ledger: ClaimLedger, probs: dict[Seat, float],
                       wtrust: dict[Seat, float]) -> list[Evidence]:
        """预言家对跳怎么分高下：只用公开事实，像会玩的真人那样复盘（每次观察后整批重算）。

        - 夜里的刀口：天亮只死一个人时那就是刀口（同一夜女巫至多用一瓶药）。对跳的一位被刀，另一位更像狼；
          被查杀的人（报查杀之后）成了刀口，那条查杀就是假的（不论有没有对跳）。
        - 女巫：唯一的女巫声明、用药与天亮的死讯对得上，就很可信；查杀她的人可疑；她毒死的人，给他发金水的人可疑。
        - 后跳的一位起跳时当场查杀先跳的那位：悍跳的老套路，后跳的这位更像狼。
        - 像狼的声明者：他的金水、站他边的人、帮他投对跳另一位的人，跟着可疑。
        """
        out: list[Evidence] = []
        lw = self._logic()

        def add(seat: Seat, delta: float, reason: ReasonCode, eid: int, day: int, source: Seat | None) -> None:
            if seat == self.seat or seat in st.hard or delta == 0:
                return
            out.append(Evidence(seat=seat, delta=delta, reason=reason, event_id=eid, day=day, layer=st.name,
                                source=source, derived=True))

        def prob(s: Seat) -> float:
            if s == self.seat:
                return 1.0 if (st.name == 'private' and self.role == Role.WEREWOLF) else 0.0
            return st.hard.get(s, probs.get(s, 0.5))

        def knifed(s: Seat) -> float:
            """s 夜里死、且多半是被狼刀的：独死 1.0，两人同死（其中一个是毒）0.25，被坐实的毒死 0。"""
            d = ledger.death_of(s)
            if d is None or d[1] != 'night' or s in poisoned:
                return 0.0
            dawn = ledger.dawn_deaths(d[0]) or [s]
            return 1.0 if len(dawn) == 1 else 0.25

        poisoned: set[Seat] = set()
        for pc in ledger.potion_claims():
            deaths = ledger.dawn_deaths(pc.night)
            if pc.kind == 'poison' and deaths and pc.target in deaths and wtrust.get(pc.seat, 0.0) > 0:
                poisoned.add(pc.target)
        # 坐实的女巫
        witches = self._claimers(ledger, Role.WITCH)
        witch: Seat | None = None
        if len(witches) == 1 and _k(witches[0], Role.WITCH.value) not in self._late:
            w = witches[0]
            for pc in ledger.potions_by(w):
                deaths = ledger.dawn_deaths(pc.night)
                if deaths is not None and ((pc.kind == 'save' and not deaths) or
                                           (pc.kind == 'poison' and pc.target in deaths)):
                    witch = w
                    rc = (ledger.role_claims(w, Role.WITCH) or [None])[0]
                    eid, day = (rc.event_id, rc.day) if rc else (pc.event_id, pc.day)
                    add(w, self.w.witch_verified * lw, ReasonCode.CLAIM_UNCONTESTED, eid, day, w)
                    break
        cl = ledger.claimants(Role.SEER)
        duel = len(cl) >= 2
        first = {c: ledger.role_claims(c, Role.SEER)[0] for c in cl}
        for c in cl:
            rc = first[c]
            # 刀口：c 被刀了，对跳的另一位更像狼
            k = knifed(c) if duel else 0.0
            if k:
                for r in cl:
                    if r != c:
                        add(r, self.w.killed_rival * k * lw, ReasonCode.KILL_PATTERN, first[r].event_id,
                            first[r].day, r)
            # 查验与公开事实对不对得上
            latest: dict[Seat, Any] = {}
            for chk in ledger.checks_by(c):
                latest[chk.target] = chk
            for tgt, chk in latest.items():
                if tgt == c:
                    continue
                if duel and tgt in first and chk.result == 'wolf' and chk.event_id == rc.event_id \
                        and first[tgt].event_id < rc.event_id:
                    add(c, self.w.rival_check * lw, ReasonCode.COUNTER_CLAIM, rc.event_id, rc.day, c)
                d = ledger.death_of(tgt)
                refuted = confirmed = 0.0
                if chk.result == 'wolf':
                    if d is not None and d[0] > chk.day:
                        refuted = knifed(tgt)
                    if tgt == witch:
                        refuted = max(refuted, wtrust.get(tgt, 0.0))
                    if tgt in poisoned:
                        confirmed = 1.0
                elif tgt in poisoned:
                    refuted = 1.0
                if refuted:
                    add(c, self.w.check_refuted * refuted * lw, ReasonCode.CONTRADICTION, chk.event_id, chk.day, c)
                if confirmed:
                    add(c, self.w.check_confirmed * lw, ReasonCode.KILL_PATTERN, chk.event_id, chk.day, c)
                fake = max(0.0, prob(c) - 0.5) * 2
                if chk.result == 'good' and fake > 0:
                    add(tgt, self.w.fake_gold * fake * lw, ReasonCode.VOUCHED_BY_TRUSTED, chk.event_id, chk.day, c)
        if not duel:
            return out
        # 站边与帮票：一个人一条（取最像狼的那位声明者）
        sided: dict[Seat, tuple[float, int, int, ReasonCode]] = {}
        for eid, day, src, c in self._backs:
            if c in cl and src not in cl:
                f = max(0.0, prob(c) - 0.5) * 2
                if f > sided.get(src, (0.0,))[0]:
                    sided[src] = (f, eid, day, ReasonCode.DEFENDED_WOLF)
        for eid, day, _rnd, voter, tgt in self._votes:
            if voter is None or voter in cl or tgt not in cl:
                continue
            for c in cl:
                if c != tgt and first[c].event_id < eid and first[tgt].event_id < eid:
                    f = max(0.0, prob(c) - 0.5) * 2
                    if f > sided.get(voter, (0.0,))[0]:
                        sided[voter] = (f, eid, day, ReasonCode.VOTED_WITH_WOLF)
        for s, (f, eid, day, reason) in sorted(sided.items()):
            add(s, self.w.backed_fake * f * lw, reason, eid, day, s)
        return out

    @staticmethod
    def _claimed_roles(ledger: ClaimLedger, seat: Seat) -> set[Role]:
        return {rc.role for rc in ledger.role_claims(seat)}

    def _vote_evidence(self, st: _LayerState, probs: dict[Seat, float]) -> list[Evidence]:
        out: list[Evidence] = []
        lw = self._logic()

        def prob(s: Seat) -> float:
            if s == self.seat:
                return self.p(s, st.name)
            return st.hard.get(s, probs.get(s, 0.0))

        groups: dict[str, list[list[Any]]] = {}
        for v in self._votes:
            groups.setdefault(_k(v[1], v[2], v[4]), []).append(v)
        for eid, day, rnd, voter, tgt in self._votes:
            if voter == self.seat or tgt is None or tgt == self.seat or voter in st.hard:
                continue
            pt = prob(tgt)
            if pt < 0.35:
                out.append(Evidence(seat=voter, delta=self.w.voted_good * (0.35 - pt) / 0.35 * lw,
                                    reason=ReasonCode.VOTED_GOOD, event_id=eid, day=day, layer=st.name,
                                    source=voter, derived=True))
            mates = [m for m in groups[_k(day, rnd, tgt)] if m[3] != voter]
            best = max((prob(m[3]) for m in mates), default=0.0)
            if best >= 0.65:
                out.append(Evidence(seat=voter, delta=self.w.voted_with_wolf * (best - 0.5) * 2 * lw,
                                    reason=ReasonCode.VOTED_WITH_WOLF, event_id=eid, day=day, layer=st.name,
                                    source=voter, derived=True))
        return out

    def _claimers(self, ledger: ClaimLedger, role: Role) -> list[Seat]:
        """自称该神职的人；女巫还包括只报过用药、没改称别的身份的人。"""
        cl = ledger.claimants(role)
        if role == Role.WITCH:
            for pc in ledger.potion_claims():
                if pc.seat not in cl and ledger.current_role(pc.seat) in (None, Role.WITCH):
                    cl.append(pc.seat)
        return cl

    def _trusts(self, st: _LayerState, ledger: ClaimLedger, probs: dict[Seat, float], role: Role) -> dict[Seat, float]:
        cl = self._claimers(ledger, role)
        g: dict[Seat, float] = {}
        for c in cl:
            if c == self.seat:
                g[c] = 1.0 if st.name == 'public' else float(self.role == role)
            elif st.name == 'private' and self.role == role:
                g[c] = 0.0
            elif c in st.hard:
                g[c] = 1.0 - st.hard[c]
            else:
                g[c] = 1.0 - probs.get(c, 0.5)
                if _k(c, role.value) in self._late:
                    g[c] *= self.w.late_trust
        total = max(1.0, sum(g.values()))
        return {c: v / total for c, v in g.items()}

    # ------------------------------------------------------------------ 反思

    def nudge(self, seat: Seat, delta_p: float, layer: Layer = 'private') -> float:
        """夜间反思：把 seat 的概率挪 delta_p（压在 0.02 到 0.98），记一条 REFLECTION 证据。
        硬知识的座位与自己不动。返回实际记下的对数几率增量。缩放与封顶由调用方按 MindTuning 做。"""
        st = self._layers[layer]
        if seat == self.seat or seat in st.hard or seat not in self.others or delta_p == 0:
            return 0.0
        cur = self.p(seat, layer)
        tgt = min(0.98, max(0.02, cur + delta_p))
        d = _logit(tgt) - _logit(cur)
        if d == 0:
            return 0.0
        st.acc.append(Evidence(seat=seat, delta=d, reason=ReasonCode.REFLECTION, event_id=self.last_event_id or None,
                               day=self.day, layer=layer))
        st.cache = None
        return d

    # ------------------------------------------------------------------ 存取

    def dump(self) -> dict[str, Any]:
        return {
            'seat': self.seat, 'role': self.role.value, 'day': self.day, 'last_event_id': self.last_event_id,
            'layers': {name: {'acc': [e.model_dump(mode='json') for e in st.acc],
                              'derived': [e.model_dump(mode='json') for e in st.derived],
                              'hard': [[s, v] for s, v in sorted(st.hard.items())]}
                       for name, st in self._layers.items()},
            'applied': dict(self._applied), 'cap_used': dict(self._cap_used),
            'accusers': {k: list(v) for k, v in self._accusers.items()}, 'done': sorted(self._done),
            'accusations': {s: {t: list(r) for t, r in m.items()} for s, m in self._accusations.items()},
            'night_dead': list(self._night_dead), 'votes': [list(v) for v in self._votes], 'late': dict(self._late),
            'backs': [list(b) for b in self._backs],
        }

    def load(self, data: dict[str, Any]) -> None:
        """从 `dump()` 的结果恢复（座位、身份、人格、配置由构造函数给）。内部账本会在下一次带 view 的
        `observe` 时从视图重建；调用方自己传账本时不受影响。"""
        self.day = int(data['day'])
        self.last_event_id = int(data['last_event_id'])
        for name, raw in data['layers'].items():
            st = self._layers[name]
            st.acc = [Evidence.model_validate(e) for e in raw['acc']]
            st.derived = [Evidence.model_validate(e) for e in raw['derived']]
            st.hard = {int(s): float(v) for s, v in raw['hard']}
            st.cache = None
        self._applied = {k: int(v) for k, v in data['applied'].items()}
        self._cap_used = {k: float(v) for k, v in data['cap_used'].items()}
        self._accusers = {k: [int(s) for s in v] for k, v in data['accusers'].items()}
        self._done = set(data['done'])
        self._accusations = {s: {t: [int(x) for x in r] for t, r in m.items()} for s, m in data['accusations'].items()}
        self._night_dead = [int(s) for s in data['night_dead']]
        self._votes = [list(v) for v in data['votes']]
        self._late = {k: int(v) for k, v in data['late'].items()}
        self._backs = [[int(x) for x in b] for b in data.get('backs', [])]
        self._ledger = ClaimLedger()
        self._ledger_stale = True
        self._last_ledger = None

    # ------------------------------------------------------------------ 内部

    def _logic(self) -> float:
        return 0.6 + 0.8 * self.traits.logic

    def _vengeance(self) -> float:
        return 0.3 + 1.2 * self.traits.vengeful

    def _credibility(self, st: _LayerState, src: Seat) -> float:
        return max(-0.3, min(1.0, 1.0 - self.w.credibility_slope * self.p(src, st.name)))

    def _top(self, st: _LayerState) -> Seat | None:
        probs = self._probs(st)
        return max(probs, key=lambda s: (probs[s], -s)) if probs else None

    def _stubborn(self, delta: float, seat: Seat, top: Seat | None) -> float:
        """固执：对头号怀疑的「洗白」证据打折。"""
        if delta < 0 and seat == top:
            return delta * (1.0 - self.w.stubborn_discount * self.traits.stubborn)
        return delta

    def _add(self, st: _LayerState, ev: Event, seat: Seat, delta: float, reason: ReasonCode,
             source: Seat | None = None) -> None:
        if seat == self.seat or seat not in self.others or seat in st.hard or delta == 0:
            return
        st.acc.append(Evidence(seat=seat, delta=delta, reason=reason, event_id=ev.id, day=ev.day, layer=st.name,
                               source=source))
        st.cache = None

    def _set_hard(self, st: _LayerState, ev: Event, seat: Seat, wolf: bool, reason: ReasonCode) -> None:
        if seat == self.seat or seat not in self.others:
            return
        st.hard[seat] = 1.0 if wolf else 0.0
        st.acc.append(Evidence(seat=seat, delta=HARD_DELTA if wolf else -HARD_DELTA, reason=reason, event_id=ev.id,
                               day=ev.day, layer=st.name, hard=True))
        st.cache = None

    def _base(self, st: _LayerState) -> dict[Seat, float]:
        base = {s: 0.0 for s in self.others if s not in st.hard}
        for e in st.acc:
            if e.seat in base and not e.hard:
                base[e.seat] += e.delta
        return base

    @staticmethod
    def _sum(base: dict[Seat, float], items: Iterable[Evidence]) -> dict[Seat, float]:
        out = dict(base)
        for e in items:
            if e.seat in out:
                out[e.seat] += e.delta
        return out

    def _probs(self, st: _LayerState) -> dict[Seat, float]:
        if st.cache is None:
            st.cache = self._normalize(st, self._sum(self._base(st), st.derived))
        return st.cache

    def _normalize(self, st: _LayerState, logits: dict[Seat, float]) -> dict[Seat, float]:
        """整体平移对数几率，使不明座位的期望狼数 = 狼总数 − 已知的狼。结果压在 [0.01, 0.99]。"""
        unknown = [s for s in self.others if s not in st.hard]
        if not unknown:
            return {}
        known = sum(v for s, v in st.hard.items() if s in self.others)
        if st.name == 'private' and self.role == Role.WEREWOLF:
            known += 1
        target = min(max(self.n_wolves - known, 0.05), len(unknown) - 0.05)
        lo, hi = -30.0, 30.0
        for _ in range(50):
            mid = (lo + hi) / 2
            if sum(_sigmoid(logits[s] + mid) for s in unknown) < target:
                lo = mid
            else:
                hi = mid
        c = (lo + hi) / 2
        return {s: min(0.99, max(0.01, _sigmoid(logits[s] + c))) for s in unknown}
