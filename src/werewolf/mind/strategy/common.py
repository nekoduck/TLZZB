"""各身份共用的打法零件：决策上下文、选项打分的记账、引用证据、拼言语行为、好人的投票与发言。

单位一律是自然对数几率（MindTuning 的说明）：差 1 是「明显更好」，差 2.5 以上是「没得选」。
这里只读心智的状态与视图，从不改动它们，所以由它算出的打分与方案都是纯的。
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from werewolf.kernel.claims import ClaimLedger, speech_acts, speech_kind
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import ActKind, Event, EventType, Phase, Request, Role, Seat, View
from werewolf.mind.beliefs import Beliefs
from werewolf.mind.explain import Why
from werewolf.mind.memory import MemoryStore
from werewolf.mind.mood import Mood
from werewolf.mind.types import (
    PRIVATE_REASONS,
    CoverStory,
    MindState,
    MindTuning,
    PlannedAct,
    ReasonCode,
    SpeechPlan,
    Traits,
)

Layer = Literal['public', 'private']

ACCUSE_LIKE = (ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.DOUBT_CLAIM, ActKind.QUESTION)

# 策略层的旋钮（对数几率）。调平衡时动这里。
TILT_BONUS = 1.6  # tilted 时对交情 ≤ -0.5 的座位的投票、指认、开枪加的效用
TENSE_BONUS = 0.6  # tense 时反击 / 自保的偏向
CONFIDENT_BONUS = 0.6  # confident 时带队类方案
CONFORMITY_GAIN = 2.5  # 号召：每个可信号召者 × 从众的平方（从众低的几乎不跟）
GRUDGE_TRUST = -0.25  # 交情低于它、且记仇 ≥ 0.6，才会在发言里翻旧账
GRUDGE_STRENGTH = 2.5  # 旧账对应的记忆当前强度至少多少才提
GUT_RANK = 0.2  # 第一感觉在挑发言目标时折合多少怀疑度（概率）
GUT_VOTE = 0.8  # 第一感觉在投票里折合多少效用
KILLED_RIVAL = 1.4  # 对跳的另一位夜里被刀了：活着的那位更像狼（乘理性权重）
COUNTER_VOTE = 1.6  # 对跳时投不信的那位预言家（两人里必有一个在说谎）
KILLED_RIVAL_RANK = 0.3
CONSOLIDATE = 0.25  # 合票：每份可信号召（至多两份）加的效用，再乘 0.5 + 从众
CONSOLIDATE_MIN_P = 0.3
ENEMY_VOTE = 1.0  # 攻击过我认定的真预言家，每次（至多两次）加的投票效用（乘理性权重）
ENEMY_RANK = 0.12  # 同上，挑发言目标时折合多少怀疑度
LONE_TRUST = 0.7  # 对跳的另一位出局后，剩下那位的可信度不到它就仍然怀疑他
CLAIM_GUT = 0.1  # 对跳时站边的第一感觉分量（声明可信度差不多时谁也说不准）  # 同上，挑发言目标时折合多少怀疑度


def logit(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


# --------------------------------------------------------------------------- 上下文


@dataclass
class Ctx:
    """一次决策看到的一切：视图 + 心智状态（只读）。"""

    seat: Seat
    role: Role
    traits: Traits
    tuning: MindTuning
    view: View
    ledger: ClaimLedger
    beliefs: Beliefs
    mood: Mood
    memory: MemoryStore
    cover: CoverStory
    vote_intent: Seat | None
    pack: dict[Seat, str]
    rng: Rng  # 只用来 fork 出确定性的派生流，不在原流上抽
    taste: str = ''  # 人设 id：决定没有证据时的「第一感觉」（见 gut）
    events_by_id: dict[int, Event] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.events_by_id = {e.id: e for e in self.view.events}

    @property
    def day(self) -> int:
        return self.view.day

    @property
    def living(self) -> list[Seat]:
        return self.view.living()

    @property
    def others(self) -> list[Seat]:
        return [s for s in self.view.living() if s != self.seat]

    @property
    def state(self) -> MindState:
        return self.mood.state('private')

    def gut(self, s: Seat) -> float:
        """对 s 的第一感觉，-1 到 1：由人设与当天日子决定（不掷骰、不看身份），只在证据相近时起作用。"""
        h = zlib.crc32(f'{self.taste}|{self.seat}|{s}|{self.day}'.encode())
        return (h % 2001) / 1000.0 - 1.0

    def p(self, s: Seat, layer: Layer = 'private') -> float:
        return self.beliefs.p(s, layer)

    def hard(self, s: Seat) -> bool:
        return s != self.seat and self.beliefs.is_hard(s, 'private')

    def trust(self, s: Seat) -> float:
        return self.mood.trust(s)

    def is_public(self, eid: int) -> bool:
        ev = self.events_by_id.get(eid)
        return ev is not None and ev.vis == 'public'

    def teammates(self) -> list[Seat]:
        return list(self.view.wolf.teammates) if self.view.wolf else []

    def phase_event(self, phase: Phase, day: int) -> int | None:
        for e in self.view.events:
            if e.type == EventType.PHASE and e.day == day and e.data.get('phase') == phase.value:
                return e.id
        return None

    def claim_event(self, seat: Seat, role: Role) -> int | None:
        rc = self.ledger.role_claims(seat, role)
        return rc[0].event_id if rc else None

    def intents_today(self) -> dict[Seat, Seat]:
        return {s: t for s, t in self.ledger.vote_intents(self.day).items() if s != self.seat}

    def calls_on(self, target: Seat) -> list[Seat]:
        return [s for s, t in self.intents_today().items() if t == target]

    def crowd_target(self, exclude: Iterable[Seat] = ()) -> Seat | None:
        """今天被号召最多的存活座位（不含自己与 exclude）；没人号召返回 None。"""
        ex = set(exclude) | {self.seat}
        counts: dict[Seat, int] = {}
        for t in self.intents_today().values():
            if t in ex or t not in self.living:
                continue
            counts[t] = counts.get(t, 0) + 1
        if not counts:
            return None
        return max(counts, key=lambda s: (counts[s], -s))

    def last_own_speech(self) -> int:
        last = 0
        for e in self.view.events:
            if e.type == EventType.SPEECH and e.actor == self.seat:
                last = e.id
        return last

    def accusers(self) -> list[tuple[Seat, int]]:
        """上次自己发言之后（且在今天之内）点到我、踩我、报我查杀的人：(座位, 最近一次的事件 id)，次数多的在前。"""
        since = self.last_own_speech()
        counts: dict[Seat, list[int]] = {}
        for e in self.view.events:
            if e.id <= since or e.type != EventType.SPEECH or e.actor in (None, self.seat) or e.day != self.day:
                continue
            for a in speech_acts(e):
                hit = (a.kind in ACCUSE_LIKE and a.target == self.seat) or (
                    a.kind == ActKind.CLAIM_CHECK and a.target == self.seat and a.result == 'wolf')
                if hit:
                    assert e.actor is not None
                    counts.setdefault(e.actor, []).append(e.id)
                    break
        return [(s, ids[-1]) for s, ids in sorted(counts.items(), key=lambda kv: (-len(kv[1]), -kv[1][-1]))]

    def checked_wolf_by(self) -> list[Seat]:
        """公开报过我是狼的人。"""
        return sorted({c.seat for c in self.ledger.checks_on(self.seat) if c.result == 'wolf'})

    def alive_at_night(self, s: Seat, night: int) -> bool:
        died = self.ledger.death_of(s)
        return died is None or died[0] >= night

    def danger(self, request: Request) -> bool:
        """快被放逐了：在 PK 台上，或今天被两个以上的人号召投，或被报了查杀（遗言不算）。"""
        if request.speech_kind == 'pk':
            return True
        if request.speech_kind == 'last_words':
            return False
        return len(self.calls_on(self.seat)) >= 2 or bool(self.checked_wolf_by())


def survivor_claimants(ctx: Ctx) -> list[Seat]:
    """自称预言家、还活着、而同样自称预言家的另一位夜里被刀了的人（狼通常不刀自家悍跳的）。"""
    cl = ctx.ledger.claimants(Role.SEER)
    if len(cl) < 2:
        return []
    killed = [c for c in cl if (d := ctx.ledger.death_of(c)) is not None and d[1] == 'night']
    if not killed:
        return []
    return [c for c in cl if c in ctx.living and c != ctx.seat and c not in killed]


def seer_side(ctx: Ctx) -> list[Seat]:
    """我认定是真预言家的人：没人对跳、我也信得过的声明者；或夜里被刀的声明者（狼通常不刀自家悍跳的）。"""
    cl = [c for c in ctx.ledger.claimants(Role.SEER) if c != ctx.seat]
    if not cl or ctx.ledger.current_role(ctx.seat) == Role.SEER:
        return []
    out: list[Seat] = []
    for c in cl:
        d = ctx.ledger.death_of(c)
        if d is not None and d[1] == 'night':
            out.append(c)
        elif d is None and len(cl) == 1 and ctx.beliefs.claim_trust(c, 'private', Role.SEER, ctx.ledger) >= 0.55 \
                and not (ctx.hard(c) and ctx.p(c) >= 0.5):
            out.append(c)
    return out


def seer_enemies(ctx: Ctx) -> dict[Seat, list[int]]:
    """踩过、号召投过、质疑过、投过我认定的真预言家的人 → 这些事件的 id。狼常常这样暴露自己。"""
    side = set(seer_side(ctx))
    if not side:
        return {}
    out: dict[Seat, list[int]] = {}
    for e in ctx.view.events:
        if e.vis != 'public' or e.actor is None or e.actor == ctx.seat or e.actor in side:
            continue
        hit = e.type == EventType.VOTE and e.target in side
        if e.type == EventType.SPEECH:
            hit = any(a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.DOUBT_CLAIM) and a.target in side
                      for a in speech_acts(e))
        if hit and not (ctx.hard(e.actor) and ctx.p(e.actor) < 0.5):
            out.setdefault(e.actor, []).append(e.id)
    return out


def doubted_claimant(ctx: Ctx) -> Seat | None:
    """预言家对跳过（含已出局的一位、我不是其中之一）时，活着的声明者里我更不信的那位；分不出高下时看第一感觉。
    对跳的另一位出局了，剩下这位仍有一半可能是狼，信得不够就还算「不信的那位」。"""
    if ctx.ledger.current_role(ctx.seat) == Role.SEER:
        return None
    allc = [c for c in ctx.ledger.claimants(Role.SEER) if c != ctx.seat]
    if len(allc) < 2:
        return None
    cl = [c for c in allc if c in ctx.living]
    if not cl:
        return None
    trust = {c: ctx.beliefs.claim_trust(c, 'private', Role.SEER, ctx.ledger) + CLAIM_GUT * ctx.gut(c) for c in cl}
    worst = min(cl, key=lambda c: (trust[c], c))
    if len(cl) == 1 and trust[worst] >= LONE_TRUST:
        return None
    return worst


# --------------------------------------------------------------------------- 选项打分的记账


@dataclass
class Score:
    option_id: str
    label: str
    target: Seat | None = None
    u: float = 0.0
    whys: list[Why] = field(default_factory=list)
    hard: bool = False
    vetoed: bool = False
    veto_reason: str | None = None
    cause: str | None = None  # 心境项的来由（念头 key）
    motive: ReasonCode | None = None

    def add(self, du: float, key: str | None = None, seat: Seat | None = None) -> None:
        self.u += du
        if key is not None and abs(du) >= 0.05:
            self.whys.append((key, seat))

    def veto(self, why: str) -> None:
        self.vetoed = True
        self.veto_reason = why


def grudge_cause(ctx: Ctx, seat: Seat) -> str | None:
    """对 seat 的负面念头里分量最重的那条的 key（交情差的来由）。"""
    items = [t for t in ctx.mood.thoughts('private') if t.about == seat and t.value < 0]
    if items:
        return min(items, key=lambda t: t.value).key
    top = ctx.mood.top_cause('private')
    return top.key if top else None


def top_cause_key(ctx: Ctx) -> str | None:
    t = ctx.mood.top_cause('private')
    return t.key if t else None


def mood_terms(ctx: Ctx, sc: Score, target: Seat | None, *, accusers: Sequence[Seat] = ()) -> None:
    """心境改变决定：tilted 偏向记仇的人；tense 胆大的反击、胆小的跟大票。来由记进 sc.cause。"""
    if target is None:
        return
    st = ctx.state
    if st == 'tilted' and ctx.trust(target) <= -0.5:
        sc.add(TILT_BONUS * (0.5 + ctx.traits.vengeful), 'grudge', target)
        sc.cause = grudge_cause(ctx, target)
    elif st == 'tense':
        if ctx.traits.boldness >= 0.5 and target in accusers:
            sc.add(TENSE_BONUS, 'counter', target)
            sc.cause = grudge_cause(ctx, target)
        elif ctx.traits.boldness < 0.5 and target == ctx.crowd_target():
            sc.add(TENSE_BONUS, 'crowd', target)
            sc.cause = top_cause_key(ctx)


def evidence_weight(ctx: Ctx) -> float:
    """理性证据在效用里的权重；tilted 减半。"""
    w = 1.0 + 0.8 * ctx.traits.logic
    return w * (0.5 if ctx.state == 'tilted' else 1.0)


def conformity_term(ctx: Ctx, sc: Score, target: Seat) -> None:
    """号召只是一条言语行为：按从众与号召者的可信度加一点，受 speaker_cap 封顶。"""
    callers = ctx.calls_on(target)
    if not callers:
        return
    cred = sum(max(0.0, 1.0 - 1.6 * ctx.p(c)) for c in callers)
    bonus = min(ctx.tuning.speaker_cap, CONFORMITY_GAIN * ctx.traits.conformity ** 2 * cred)
    sc.add(bonus, 'called', target)


def consolidate_term(ctx: Ctx, sc: Score, t: Seat, p: float) -> None:
    """好人合票：自己也觉得可疑、又有不止一位可信的人号召投他时，往一处投，免得票被狼的整票压过。"""
    if p < CONSOLIDATE_MIN_P:
        return
    cred = sum(max(0.0, 1.0 - 1.6 * ctx.p(c)) for c in ctx.calls_on(t))
    if cred < 1.0:
        return
    sc.add(CONSOLIDATE * min(2.0, cred) * (0.5 + ctx.traits.conformity), 'crowd', t)


def commit_term(ctx: Ctx, sc: Score, target: Seat, request: Request) -> None:
    if request.context.get('round', 'main') != 'main':
        return
    if ctx.vote_intent is not None and ctx.vote_intent == target:
        sc.add(ctx.tuning.commit_bonus * (0.5 + ctx.traits.stubborn), 'commit', target)


# --------------------------------------------------------------------------- 好人的投票


def score_vote_town(ctx: Ctx, request: Request) -> list[Score]:
    """好人（含藏着身份的神职）的投票。硬知识：自己验出的狼标 hard；验过的好人、公开翻出的好人否决。"""
    out: list[Score] = []
    w = evidence_weight(ctx)
    accusers = [s for s, _ in ctx.accusers()] + [s for s, t in ctx.intents_today().items() if t == ctx.seat]
    probs = {o.target: ctx.p(o.target) for o in request.options if o.target is not None}
    enemies = seer_enemies(ctx)
    for o in request.options:
        sc = Score(o.id, o.label, o.target)
        if o.target is None:
            top = max(probs.values(), default=0.0)
            sc.add(-1.6 + (0.8 if top < 0.3 else 0.0), 'abstain')
            out.append(sc)
            continue
        t = o.target
        p = probs[t]
        if ctx.hard(t):
            if p >= 0.5:
                sc.hard = True
                sc.add(w * logit(0.99), 'hard_wolf', t)
                sc.motive = ReasonCode.SEER_CHECK if ctx.role == Role.SEER else None
            else:
                sc.veto('known_good')
            out.append(sc)
            continue
        sc.add(w * logit(p), 'suspect' if p >= 0.4 else 'trusted', t)
        sc.add(GUT_VOTE * ctx.gut(t))
        if t in survivor_claimants(ctx):
            sc.add(KILLED_RIVAL * w, ReasonCode.KILL_PATTERN.value, t)
        elif t == doubted_claimant(ctx):
            sc.add(COUNTER_VOTE * w, ReasonCode.COUNTER_CLAIM.value, t)
        if t in enemies:
            sc.add(ENEMY_VOTE * min(2, len(enemies[t])) * w, ReasonCode.VOTED_GOOD.value, t)
        conformity_term(ctx, sc, t)
        consolidate_term(ctx, sc, t, p)
        commit_term(ctx, sc, t, request)
        mood_terms(ctx, sc, t, accusers=accusers)
        out.append(sc)
    return out


# --------------------------------------------------------------------------- 证据引用


def involves(ev: Event, target: Seat) -> bool:
    if ev.actor == target or ev.target == target:
        return True
    return any(a.target == target for a in speech_acts(ev))


def evidence_ok(reason: ReasonCode, ev: Event, target: Seat | None, me: Seat) -> bool:
    """一条公开事件能不能作为 reason 的证据（mind/invariants 与 validate_plan 共用）。"""
    if ev.vis != 'public':
        return False
    acts = speech_acts(ev)
    if reason == ReasonCode.VOTED_ME:
        return ev.type == EventType.VOTE and ev.actor == target and ev.target == me
    if reason == ReasonCode.ACCUSED_ME:
        return ev.type == EventType.SPEECH and ev.actor == target and any(
            (a.kind in ACCUSE_LIKE and a.target == me) or (a.kind == ActKind.CLAIM_CHECK and a.target == me
                                                           and a.result == 'wolf') for a in acts)
    if reason == ReasonCode.SEER_CHECK:
        if ev.type == EventType.PHASE and ev.data.get('phase') == Phase.NIGHT_SEER.value:
            return True
        return ev.type == EventType.SPEECH and any(a.kind == ActKind.CLAIM_CHECK and a.target == target for a in acts)
    if reason in (ReasonCode.COUNTER_CLAIM, ReasonCode.CLAIM_UNCONTESTED, ReasonCode.LATE_CLAIM):
        return ev.type == EventType.SPEECH and ev.actor == target and any(a.kind == ActKind.CLAIM_ROLE for a in acts)
    if reason == ReasonCode.SAVED_BY_WITCH:
        return ev.type == EventType.SPEECH and any(a.kind == ActKind.CLAIM_SAVE and a.target == target for a in acts)
    if reason in (ReasonCode.QUIET, ReasonCode.OFF_GAME, ReasonCode.CONTRADICTION, ReasonCode.DEFENDED_WOLF):
        return ev.type == EventType.SPEECH and ev.actor == target
    if reason in (ReasonCode.VOTED_GOOD, ReasonCode.VOTED_WITH_WOLF):
        return ev.type == EventType.VOTE and ev.actor == target
    if reason in (ReasonCode.DEAD_ACCUSED, ReasonCode.ACCUSED_BY_MANY, ReasonCode.VOUCHED_BY_TRUSTED):
        if ev.type == EventType.HUNTER_SHOT:
            return ev.target == target
        return ev.type == EventType.SPEECH and any(a.target == target for a in acts)
    if target is None:
        return True
    return involves(ev, target)


_CITABLE_ACCUSE = {
    ReasonCode.ACCUSED_ME, ReasonCode.VOTED_ME, ReasonCode.VOTED_GOOD, ReasonCode.VOTED_WITH_WOLF,
    ReasonCode.DEFENDED_WOLF, ReasonCode.ACCUSED_BY_MANY, ReasonCode.DEAD_ACCUSED, ReasonCode.COUNTER_CLAIM,
    ReasonCode.LATE_CLAIM, ReasonCode.CONTRADICTION, ReasonCode.QUIET, ReasonCode.OFF_GAME, ReasonCode.SEER_CHECK,
}
_CITABLE_VOUCH = {ReasonCode.SEER_CHECK, ReasonCode.CLAIM_UNCONTESTED, ReasonCode.SAVED_BY_WITCH,
                  ReasonCode.VOUCHED_BY_TRUSTED}
_GRUDGE = (ReasonCode.VOTED_ME, ReasonCode.ACCUSED_ME)


@dataclass
class Cite:
    reason: ReasonCode
    evidence: list[int]
    fuzzy: bool


def cite(ctx: Ctx, target: Seat, sign: int = 1, prefer: Sequence[ReasonCode] = ()) -> Cite | None:
    """为指认（sign=1）或担保（sign=-1）找一条说得出口的理由：公开层的证据，证据事件还在记忆里。

    seer_check 作说出口的理由时，要求那条查验来自自己或自己站边的预言家（站边由调用方补 BACK_CLAIM）。"""
    allowed = _CITABLE_ACCUSE if sign > 0 else _CITABLE_VOUCH
    hostile = hostile_today(ctx)
    best: tuple[float, ReasonCode, list[int]] | None = None
    by_reason: dict[ReasonCode, list[tuple[float, int]]] = {}
    for e in ctx.beliefs.evidence(target, 'public'):
        if e.hard or e.event_id is None or e.reason in PRIVATE_REASONS or e.reason not in allowed:
            continue
        if e.delta * sign <= 0:
            continue
        ev = ctx.events_by_id.get(e.event_id)
        if ev is None or not evidence_ok(e.reason, ev, target, ctx.seat) or not ctx.memory.has(e.event_id):
            continue
        if e.reason == ReasonCode.SEER_CHECK and e.source is not None and e.source != ctx.seat:
            if ctx.ledger.current_role(e.source) != Role.SEER or e.source in hostile:
                continue  # 引他的查验就得站他的边；正在踩我的人，我不站
        by_reason.setdefault(e.reason, []).append((abs(e.delta), e.event_id))
    for r, items in by_reason.items():
        score = sum(d for d, _ in items) + (5.0 if r in prefer else 0.0)
        ids = [eid for _, eid in sorted(items, key=lambda x: (-x[0], -x[1]))][:2]
        if best is None or score > best[0]:
            best = (score, r, ids)
    if best is None:
        return None
    _, r, ids = best
    fuzzy = all(ctx.memory.is_fuzzy(eid, ctx.day) for eid in ids)
    return Cite(r, sorted(ids), fuzzy)


def enemy_cite(ctx: Ctx, t: Seat) -> Cite | None:
    """t 投过我认定的真预言家：以那张票为证（VOTED_GOOD），票还在记忆里才算。"""
    if ctx.role == Role.WEREWOLF:
        return None
    for eid in reversed(seer_enemies(ctx).get(t, [])):
        ev = ctx.events_by_id.get(eid)
        if ev is not None and ev.type == EventType.VOTE and ctx.memory.has(eid) \
                and evidence_ok(ReasonCode.VOTED_GOOD, ev, t, ctx.seat):
            return Cite(ReasonCode.VOTED_GOOD, [eid], ctx.memory.is_fuzzy(eid, ctx.day))
    return None


SOFT_SUSPECT = 0.55  # 公开层怀疑度到这里，替他说话的人才算「替可疑的人说话」
EAGER_SPEAKERS = 2  # 当天前几个发言就号召投人（自己没给证据），算「带节奏太急」
_SOFT_ORDER = (ReasonCode.DEFENDED_WOLF, ReasonCode.FOLLOWS_CROWD, ReasonCode.QUIET, ReasonCode.TOO_EAGER)


def public_cite(ctx: Ctx, t: Seat) -> Cite | None:
    """怀疑度里没有说得出口的证据时，从 t 今天与昨天的公开发言里找一条软理由（替可疑的人说话、
    跟着别人号召、不表态、上来就带节奏）。只是给按感觉挑的目标找个说法，所以调用方只给轻重 1。"""
    mates = set(ctx.teammates())
    found: dict[ReasonCode, int] = {}
    called: dict[int, set[Seat]] = {}  # 那一天更早的发言里已被号召或踩过的座位
    speakers: dict[int, list[Seat]] = {}
    for e in ctx.view.events:
        if e.type != EventType.SPEECH or e.vis != 'public' or e.actor is None or e.day < ctx.day - 1:
            continue
        acts = speech_acts(e)
        day_called = called.setdefault(e.day, set())
        order = speakers.setdefault(e.day, [])
        if e.actor not in order:
            order.append(e.actor)
        if e.actor == t and speech_kind(e) in ('day', 'pk'):
            pushes = [a for a in acts if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT) and a.target not in (None, t)]
            props = [a for a in acts if a.kind in (ActKind.VOUCH, ActKind.BACK_CLAIM, ActKind.DEFEND)
                     and a.target not in (None, t, ctx.seat)]
            if any(a.target in day_called and a.weight <= 1 for a in pushes):
                found[ReasonCode.FOLLOWS_CROWD] = e.id
            if any(a.target not in mates and ctx.p(a.target, 'public') >= SOFT_SUSPECT  # type: ignore[arg-type]
                   for a in props):
                found[ReasonCode.DEFENDED_WOLF] = e.id
            if speech_kind(e) == 'day' and all(a.kind == ActKind.PASS for a in acts):
                found[ReasonCode.QUIET] = e.id
            if pushes and all(a.weight <= 1 for a in pushes) and len(order) <= EAGER_SPEAKERS:
                found[ReasonCode.TOO_EAGER] = e.id
        day_called.update(a.target for a in acts if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT)
                          and a.target is not None)
    for r in _SOFT_ORDER:
        eid = found.get(r)
        ev = ctx.events_by_id.get(eid) if eid is not None else None
        if ev is not None and evidence_ok(r, ev, t, ctx.seat):
            return Cite(r, [ev.id], False)
    return None


def pa(kind: ActKind, target: Seat | None = None, weight: int = 2, reason: ReasonCode | None = None,
       evidence: Sequence[int] = (), motive: ReasonCode | None = None, truthful: bool = True, fuzzy: bool = False,
       **kw: object) -> PlannedAct:
    return PlannedAct(kind=kind, target=target, weight=weight, reason=reason, evidence=list(evidence),
                      motive=motive, truthful=truthful, fuzzy=fuzzy, **kw)  # type: ignore[arg-type]


def accuse_acts(ctx: Ctx, t: Seat, *, motive: ReasonCode | None = None, truthful: bool = True,
                strong: bool = False, intent: bool = True) -> list[PlannedAct]:
    """踩 t（带说得出口的理由）并号召投他。找不到公开理由就是 GUT，轻重 1。"""
    prefer = _GRUDGE if ctx.traits.vengeful >= 0.6 else ()
    c = cite(ctx, t, 1, prefer)
    if c is None:
        c = enemy_cite(ctx, t)
    out: list[PlannedAct] = []
    soft = public_cite(ctx, t) if c is None else None
    if soft is not None:  # 只是按感觉挑的人找了个公开的说法：轻重 1，不算硬指认
        m = motive or ReasonCode.GUT
        out.append(pa(ActKind.ACCUSE, t, 1, soft.reason, soft.evidence, m, truthful))
        if intent:
            out.append(pa(ActKind.VOTE_INTENT, t, 1, soft.reason, soft.evidence, m, truthful))
        return out
    if c is None:
        out.append(pa(ActKind.ACCUSE, t, 1, ReasonCode.GUT, motive=motive or ReasonCode.GUT, truthful=truthful))
        if intent:
            out.append(pa(ActKind.VOTE_INTENT, t, 1, ReasonCode.GUT, motive=motive or ReasonCode.GUT,
                          truthful=truthful))
        return out
    w = 3 if strong and not c.fuzzy else 2
    m = motive or c.reason
    out.append(pa(ActKind.ACCUSE, t, w, c.reason, c.evidence, m, truthful, c.fuzzy))
    if intent:
        out.append(pa(ActKind.VOTE_INTENT, t, 2, c.reason, c.evidence, m, truthful, c.fuzzy))
    return out


def vouch_act(ctx: Ctx, t: Seat, *, motive: ReasonCode | None = None, truthful: bool = True) -> PlannedAct | None:
    c = cite(ctx, t, -1)
    if c is None:
        return None
    if c.reason == ReasonCode.SEER_CHECK and not _seer_check_ok(ctx, t, c):
        return None
    return pa(ActKind.VOUCH, t, 2, c.reason, c.evidence, motive or c.reason, truthful, c.fuzzy)


def _seer_check_ok(ctx: Ctx, t: Seat, c: Cite) -> bool:
    for eid in c.evidence:
        ev = ctx.events_by_id.get(eid)
        if ev is not None and ev.actor is not None and ev.actor != ctx.seat \
                and ctx.ledger.current_role(ev.actor) != Role.SEER:
            return False
    return True


def backing_acts(ctx: Ctx, acts: Sequence[PlannedAct]) -> list[PlannedAct]:
    """计划里用了别人报的查验作理由，就得同时站边那位预言家（validate_plan 的要求）。"""
    need: list[Seat] = []
    for a in acts:
        if a.reason != ReasonCode.SEER_CHECK:
            continue
        for eid in a.evidence:
            ev = ctx.events_by_id.get(eid)
            if ev is not None and ev.type == EventType.SPEECH and ev.actor not in (None, ctx.seat) \
                    and ev.actor not in need:
                need.append(ev.actor)  # type: ignore[arg-type]
    return [pa(ActKind.BACK_CLAIM, s, 1) for s in need]


def grudge_act(ctx: Ctx, exclude: Iterable[Seat] = ()) -> PlannedAct | None:
    """记仇的人翻旧账：交情差、记忆还在（且没淡到看不清）的「他投我 / 踩我」。"""
    if ctx.traits.vengeful < 0.6:
        return None
    ex = set(exclude) | set(ctx.teammates()) | {ctx.seat}
    best: tuple[float, Seat, ReasonCode, int] | None = None
    for m in ctx.memory.all():
        if m.private or m.kind not in ('voted_me', 'accused_me') or not m.about:
            continue
        s = m.about[0]
        if s in ex or s not in ctx.living or ctx.trust(s) > GRUDGE_TRUST:
            continue
        if ctx.hard(s) and ctx.p(s) < 0.5:
            continue
        strength = ctx.memory.strength(m, ctx.day)
        if strength < GRUDGE_STRENGTH:
            continue
        reason = ReasonCode.VOTED_ME if m.kind == 'voted_me' else ReasonCode.ACCUSED_ME
        ev = ctx.events_by_id.get(m.event_id)
        if ev is None or not evidence_ok(reason, ev, s, ctx.seat):
            continue
        key = strength - 0.1 * (ctx.day - m.day)
        if best is None or key > best[0]:
            best = (key, s, reason, m.event_id)
    if best is None:
        return None
    _, s, reason, eid = best
    return pa(ActKind.ACCUSE, s, 2, reason, [eid], reason, True, ctx.memory.is_fuzzy(eid, ctx.day))


def merge(acts: Iterable[PlannedAct | None]) -> list[PlannedAct]:
    """去掉 None 与重复的（种类，目标），保持先后，至多 12 条。"""
    out: list[PlannedAct] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for a in acts:
        if a is None:
            continue
        key = (a.kind.value, a.target, a.role.value if a.role else None)
        if a.kind == ActKind.CLAIM_CHECK:
            key = (a.kind.value, a.target, str(a.night))
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out[:12]


def plan(ctx: Ctx, request: Request, pid: str, variant: str, acts: Iterable[PlannedAct | None], utility: float,
         addressed: Sequence[Seat] = (), reasons: Sequence[str] = ()) -> SpeechPlan:
    """方案的骨架：语气、标签、想法、口径由 speechplan 统一补上。"""
    return SpeechPlan(id=pid, seat=ctx.seat, day=request.day, kind=request.speech_kind or 'day', request_id=request.id,
                      variant=variant, acts=merge(acts), utility=round(utility, 4), addressed=list(addressed),
                      reasons=list(reasons))


# --------------------------------------------------------------------------- 好人的发言


def rank_targets(ctx: Ctx, layer: Layer = 'private', exclude: Iterable[Seat] = ()) -> list[tuple[Seat, float]]:
    ex = set(exclude) | {ctx.seat}
    items = [(s, ctx.p(s, layer)) for s, _ in ctx.beliefs.ranked(layer, ctx.living) if s not in ex]
    surv = set(survivor_claimants(ctx))
    doubted = doubted_claimant(ctx) if layer == 'private' else None
    enemies = seer_enemies(ctx) if layer == 'private' else {}

    def key(kv: tuple[Seat, float]) -> tuple[float, Seat]:
        bonus = KILLED_RIVAL_RANK if kv[0] in surv else (KILLED_RIVAL_RANK if kv[0] == doubted else 0.0)
        bonus += ENEMY_RANK * min(2, len(enemies.get(kv[0], [])))
        return (-(kv[1] + GUT_RANK * ctx.gut(kv[0]) + bonus), kv[0])

    return sorted(items, key=key)


def hostile_today(ctx: Ctx) -> set[Seat]:
    """报过我查杀的人，和今天踩我、号召投我的人。"""
    out = set(ctx.checked_wolf_by())
    for e in ctx.view.events:
        if e.type == EventType.SPEECH and e.day == ctx.day and e.actor not in (None, ctx.seat) and any(
                a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT) and a.target == ctx.seat for a in speech_acts(e)):
            out.add(e.actor)  # type: ignore[arg-type]
    return out


def claim_backing(ctx: Ctx, layer: Layer = 'private') -> list[PlannedAct]:
    """站边：唯一（或最可信）的预言家声明者。对跳时质疑另一位。"""
    claimants = [c for c in ctx.ledger.claimants(Role.SEER) if c != ctx.seat and c in ctx.living]
    if not claimants:
        return []
    if ctx.ledger.current_role(ctx.seat) == Role.SEER:
        return []
    trusts = {c: ctx.beliefs.claim_trust(c, layer, Role.SEER, ctx.ledger) + CLAIM_GUT * ctx.gut(c) for c in claimants}
    best = max(claimants, key=lambda c: (trusts[c], -c))
    out: list[PlannedAct] = []
    lone = len(claimants) == 1
    if lone and ctx.traits.conformity < 0.4:
        pass  # 只有一个人跳、还没人对跳时，不从众的人先不站边
    elif best in hostile_today(ctx):
        pass  # 他今天正踩我、报我查杀：嘴上还说「信他的身份」就自相矛盾了
    elif trusts[best] >= 0.5 and not (ctx.hard(best) and ctx.p(best) >= 0.5):
        out.append(pa(ActKind.BACK_CLAIM, best, 1 if lone or trusts[best] < 0.7 else 2))
    for c in claimants:
        if c != best and len(claimants) >= 2:
            eid = ctx.claim_event(c, Role.SEER)
            out.append(pa(ActKind.DOUBT_CLAIM, c, 2, ReasonCode.COUNTER_CLAIM, [eid] if eid else [],
                          ReasonCode.COUNTER_CLAIM))
    return out


def town_variants(ctx: Ctx, request: Request, *, layer: Layer = 'private', exclude: Iterable[Seat] = (),
                  motive: ReasonCode | None = None, truthful: bool = True,
                  hidden: dict[Seat, str] | None = None) -> list[SpeechPlan]:
    """平民式的几种说法：踩头号怀疑、保信任的人、站边、辩解、跟票、划水。

    `layer` 选用哪一层的怀疑度挑目标（狼的深水用公开层）；`exclude` 不踩、不保的座位（狼的队友）；
    `hidden` 是藏着的硬知识（预言家的查验：座位 → 'wolf'/'good'），用它挑目标时动机记 HIDDEN_CHECK。"""
    tr = ctx.traits
    hidden = hidden or {}
    ranked = rank_targets(ctx, layer, exclude)
    out: list[SpeechPlan] = []
    accusers = ctx.accusers()
    addressed = [accusers[0][0]] if accusers else []
    st = ctx.mood.state('public')
    priv_st = ctx.state
    first_day_quiet = ctx.day <= 1 and not any(
        e.type == EventType.SPEECH and e.day == ctx.day and e.actor != ctx.seat and speech_acts(e)
        and any(a.kind != ActKind.PASS for a in speech_acts(e)) for e in ctx.view.events)
    backing = claim_backing(ctx, layer)
    backed = {a.target for a in backing if a.kind == ActKind.BACK_CLAIM}
    if backed:  # 不踩自己站边的人
        ranked = [kv for kv in ranked if kv[0] not in backed]
    defend_self = pa(ActKind.DEFEND, None, 2, ReasonCode.SELF_PRESERVE) if accusers else None
    del st
    if ranked:
        top, ptop = ranked[0]
        psecond = ranked[1][1] if len(ranked) > 1 else 0.0
        m = ReasonCode.HIDDEN_CHECK if hidden.get(top) == 'wolf' else motive
        acts = [defend_self, *accuse_acts(ctx, top, motive=m, truthful=truthful, strong=priv_st == 'confident')]
        acts.append(grudge_act(ctx, exclude=[top, *exclude]))
        acts += backing
        acts = acts + backing_acts(ctx, [a for a in acts if a is not None])
        u = 1.0 + 1.5 * (ptop - psecond) + 0.4 * tr.boldness + 0.3 * tr.talkative
        if priv_st == 'confident':
            u += CONFIDENT_BONUS
        if priv_st == 'tense' and tr.boldness >= 0.5:
            u += TENSE_BONUS
        if priv_st == 'tilted' and ctx.trust(top) <= -0.5:
            u += TILT_BONUS * 0.5
        if first_day_quiet:
            u -= 0.3
        out.append(plan(ctx, request, f'push:{top}', 'push', acts, u, addressed))
        # 保人
        low = [s for s, p in reversed(ranked) if p < 0.3]
        for s in low[:1]:
            m2 = ReasonCode.HIDDEN_CHECK if hidden.get(s) == 'good' else motive
            v = vouch_act(ctx, s, motive=m2, truthful=truthful)
            if v is None:
                continue
            acts2: list[PlannedAct | None] = [defend_self, v]
            if ptop >= 0.4:
                acts2.append(pa(ActKind.VOTE_INTENT, top, 1, ReasonCode.GUT, motive=m or ReasonCode.GUT,
                                truthful=truthful))
            acts2 += backing
            acts2.append(grudge_act(ctx, exclude=[s, *exclude]))
            acts2 += backing_acts(ctx, [a for a in acts2 if a is not None])
            u2 = 0.4 + 1.0 * (0.33 - ctx.p(s, layer)) + 0.3 * (1 - tr.boldness)
            if priv_st == 'tilted':
                u2 -= 0.8
            out.append(plan(ctx, request, f'vouch:{s}', 'vouch', acts2, u2))
        # 跟票
        crowd = ctx.crowd_target(exclude=exclude)
        if crowd is not None and crowd != top and not (ctx.hard(crowd) and ctx.p(crowd) < 0.5):
            m3 = motive or ReasonCode.FOLLOWS_CROWD
            c = cite(ctx, crowd, 1)
            soft = public_cite(ctx, crowd) if c is None else None
            acts3: list[PlannedAct | None] = [defend_self]
            if c is not None:
                acts3.append(pa(ActKind.VOTE_INTENT, crowd, 2, c.reason, c.evidence, m3, truthful, c.fuzzy))
            elif soft is not None:
                acts3.append(pa(ActKind.VOTE_INTENT, crowd, 1, soft.reason, soft.evidence, m3, truthful))
            else:
                acts3.append(pa(ActKind.VOTE_INTENT, crowd, 1, ReasonCode.GUT, motive=m3, truthful=truthful))
            u3 = 0.1 + 1.2 * tr.conformity + 1.0 * (ctx.p(crowd, layer) - ptop)
            if priv_st == 'tense' and tr.boldness < 0.5:
                u3 += TENSE_BONUS
            out.append(plan(ctx, request, f'follow:{crowd}', 'follow', acts3, u3))
    # 辩解
    if accusers:
        acc, _ = accusers[0]
        acts4: list[PlannedAct | None] = [pa(ActKind.DEFEND, None, 3, ReasonCode.SELF_PRESERVE)]
        if acc not in set(exclude):
            acts4 += accuse_acts(ctx, acc, motive=motive, truthful=truthful, strong=False)
        acts4.append(grudge_act(ctx, exclude=[acc, *exclude]))
        acts4 += backing
        acts4 += backing_acts(ctx, [a for a in acts4 if a is not None])
        u4 = 1.0 + 0.5 * len(accusers) + (0.6 if ctx.checked_wolf_by() else 0.0)
        if priv_st == 'tense' and tr.boldness >= 0.5:
            u4 += TENSE_BONUS
        if priv_st == 'tilted':
            u4 -= 0.4
        out.append(plan(ctx, request, 'defend', 'defend', acts4, u4, [acc]))
    # 划水
    u5 = -0.9 + 2.4 * (0.45 - tr.talkative) + (0.5 if first_day_quiet else 0.0)
    out.append(plan(ctx, request, 'pass', 'pass', [pa(ActKind.PASS, None, 1)], u5))
    return out
