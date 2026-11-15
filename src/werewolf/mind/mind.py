"""心智的门面：一个 AI 座位的全部规则版思考（ARCHITECTURE 4.2）。

状态（两层：公开 / 私有）在 beliefs、mood、memory 与共用的声明账本里；这里负责：
- `observe`：把事件喂给账本、怀疑度、心情、记忆；跟踪自己的「今天要投谁」与对外口径
  （两者都只在观察到自己的 SPEECH 事件时更新）；返回神态反应。
- `options` / `decide`：效用打分 → 候选 → 取样（见 utility）。`options` 是纯的。
- `speech_variants` / `choose_plan`：发言方案（见 speechplan）。`speech_variants` 是纯的。
- 夜间反思、快照。

随机数：构造时拿到的 `Rng`（会话层给的分流），从不拿种子。取样用 `rng.fork('decide')`，
神态反应用 `rng.fork('react')`（每条事件再按事件 id 派生），首夜打法意愿用 `rng.fork('stance')`。
心智只读视图与去掉文字的事件；除首夜照顾外不读 `is_human`。
"""

from __future__ import annotations

from collections.abc import Mapping

from werewolf.kernel.claims import ClaimLedger, speech_acts
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    GODS,
    ActKind,
    Event,
    EventType,
    GameConfig,
    Request,
    RequestKind,
    Role,
    Seat,
    SeerCheck,
    View,
)
from werewolf.mind import explain, utility
from werewolf.mind.beliefs import Beliefs, BeliefWeights
from werewolf.mind.memory import MemoryStore
from werewolf.mind.mood import Mood
from werewolf.mind.speechplan import finish
from werewolf.mind.strategy import hunter, seer, villager, witch, wolf
from werewolf.mind.strategy.common import ACCUSE_LIKE, Ctx
from werewolf.mind.types import (
    CoverStory,
    Decision,
    MindSnapshot,
    MindTuning,
    Persona,
    PublicMood,
    Reaction,
    ReflectionResult,
    ScoredOption,
    SpeechPlan,
)

# 心智对怀疑度模块的调参（BeliefWeights 的默认是设计值；这里压低「口头指认」的分量，免得首日一边倒地跟风）
BELIEF_WEIGHTS = BeliefWeights(accuse=0.15, accused_by_many=0.0)  # 集成调参：压低首日一边倒的票仓（见 sim 报告）

_STRATEGY = {Role.WEREWOLF: wolf, Role.SEER: seer, Role.WITCH: witch, Role.HUNTER: hunter, Role.VILLAGER: villager}


class Mind:
    def __init__(self, seat: Seat, persona: Persona, role: Role, config: GameConfig, rng: Rng,
                 tuning: MindTuning | None = None) -> None:
        self.seat = seat
        self.persona = persona
        self.traits = persona.traits
        self.role = role
        self.config = config
        self.tuning = tuning or MindTuning()
        self._rng = rng
        self._decide_rng = rng.fork('decide')
        self._react_rng = rng.fork('react')
        self.ledger = ClaimLedger()
        self.beliefs = Beliefs(seat, role, self.traits, config, self.tuning, BELIEF_WEIGHTS)
        self.mood = Mood(seat, self.traits, self.tuning)
        self.memory = MemoryStore(seat, self.traits)
        self.cover = CoverStory()
        self.vote_intent: Seat | None = None
        self._intent_day = 0
        self.day = 0
        self.last_event_id = 0
        self.focus: Seat | None = None
        self.last_thought = ''
        self._pack: dict[Seat, str] = {}
        self._view: View | None = None

    # ------------------------------------------------------------------ 观察

    def observe(self, event: Event, view: View) -> list[Reaction]:
        """每条对本座位可见的（去掉文字的）事件调一次；view 是提交之后的视图。"""
        if event.id <= self.last_event_id:
            return []
        self.last_event_id = event.id
        self._view = view
        if event.day > self.day:
            self.day = event.day
        if self.vote_intent is not None and event.day > self._intent_day:
            self.vote_intent = None
        before_roles = {s: self.ledger.current_role(s) for s in range(1, self.config.seats + 1)}
        self.ledger.add(event)
        self.beliefs.observe(event, view, self.ledger)
        self.mood.observe(event, view, self.ledger)
        self.memory.observe(event, view, self.ledger)
        if event.type == EventType.SPEECH and event.vis == 'public' and event.actor is not None:
            if event.actor == self.seat:
                self._on_own_speech(event)
            elif self.vote_intent is not None:
                self._maybe_cancel(event, before_roles)
        if self.role == Role.WEREWOLF and event.type in (EventType.WOLF_KILL, EventType.SPEECH, EventType.WOLF_PROPOSE):
            self._pack = dict(wolf.pack_plan(view))
        return self._react(event, view)

    def _on_own_speech(self, ev: Event) -> None:
        self.cover = cover_from_ledger(self.ledger, self.seat)
        for a in speech_acts(ev):
            if a.kind == ActKind.VOTE_INTENT and a.target is not None:
                self.vote_intent = a.target
                self._intent_day = ev.day

    def _maybe_cancel(self, ev: Event, before: Mapping[Seat, Role | None]) -> None:
        """说过要投 t 之后出现了改变局面的事：新的身份声明（含对跳）、对 t 或对自己的查验。"""
        t = self.vote_intent
        for a in speech_acts(ev):
            new_claim = a.kind == ActKind.CLAIM_ROLE and a.role in GODS and before.get(ev.actor or 0) != a.role
            check = a.kind == ActKind.CLAIM_CHECK and a.target in (t, self.seat)
            if new_claim or check:
                self.vote_intent = None
                return

    # ------------------------------------------------------------------ 神态反应（只看公开心智）

    def _react(self, ev: Event, view: View) -> list[Reaction]:
        if ev.vis != 'public' or ev.actor == self.seat:
            return []
        alive = next((p.alive for p in view.players if p.seat == self.seat), False)
        if not alive:
            return []
        r = self._react_rng.fork(f'e{ev.id}')
        roll = r.random()
        tr = self.traits
        me = self.seat
        pick: tuple[float, str, str | None, float] | None = None  # (概率, 神态, 嘀咕, 强度)
        if ev.type == EventType.SPEECH and ev.actor is not None:
            acts = speech_acts(ev)
            src = ev.actor
            if any(a.kind == ActKind.CLAIM_CHECK and a.target == me and a.result == 'wolf' for a in acts):
                pick = (0.9, 'angry' if tr.boldness >= 0.5 else 'shock', 'annoyed' if tr.boldness >= 0.5 else 'nervous',
                        0.9)
            elif any(a.kind in ACCUSE_LIKE and a.target == me for a in acts):
                hot = tr.vengeful >= 0.6 or self.mood.trust(src) <= -0.4
                pick = (0.75, 'angry' if hot else ('sweat' if tr.boldness < 0.4 else 'frown'),
                        'annoyed' if hot else ('nervous' if tr.boldness < 0.4 else 'doubt'), 0.7)
            elif any(a.kind in (ActKind.VOUCH, ActKind.DEFEND, ActKind.BACK_CLAIM) and a.target == me for a in acts):
                pick = (0.5, 'nod', 'agree', 0.5)
            elif any(a.kind == ActKind.CLAIM_ROLE and a.role in GODS for a in acts):
                pick = (0.45, 'shock' if tr.logic < 0.6 else 'think', 'surprised' if tr.logic < 0.6 else 'thinking',
                        0.6)
            else:
                tgt = next((a.target for a in acts if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT)
                            and a.target is not None), None)
                if tgt is not None:
                    p = self.beliefs.p(tgt, 'public')
                    if p >= 0.45:
                        pick = (0.25, 'nod', 'agree', 0.4)
                    elif p <= 0.2 or self.mood.trust(tgt) >= 0.3:
                        pick = (0.25, 'frown', 'doubt', 0.4)
                elif all(a.kind == ActKind.PASS for a in acts):
                    pick = (0.15, 'sigh' if tr.talkative >= 0.5 else 'shrug', None, 0.3)
                if pick is None:
                    pick = (0.1, 'think', 'thinking', 0.3)
        elif ev.type == EventType.VOTE and ev.target == me:
            pick = (0.45, 'stare' if tr.boldness >= 0.5 else 'sweat', 'annoyed' if tr.vengeful >= 0.5 else None, 0.5)
        elif ev.type == EventType.DEATH and ev.target is not None and ev.target != me:
            friend = self.mood.trust(ev.target) >= 0.25
            pick = (0.6 if friend else 0.3, 'sigh' if friend else 'shock', 'sigh' if friend else 'surprised',
                    0.7 if friend else 0.4)
        elif ev.type == EventType.ROLE_REVEALED:
            pick = (0.4, 'shock', 'surprised', 0.6)
        if pick is None or roll >= pick[0]:
            return []
        state = self.mood.state('public')
        emote, quip, inten = pick[1], pick[2], pick[3]
        if state == 'tilted' and emote in ('frown', 'stare'):
            emote = 'angry'
        elif state == 'tense' and quip is None:
            quip = 'nervous'
        if r.random() < 0.3 * (1 - tr.talkative):
            quip = None
        return [Reaction(seat=me, emote=emote, quip_key=quip, intensity=round(min(1.0, inten), 3), event_id=ev.id)]

    # ------------------------------------------------------------------ 决定

    def _ctx(self, view: View) -> Ctx:
        pack = wolf.pack_plan(view) if self.role == Role.WEREWOLF else {}
        return Ctx(seat=self.seat, role=self.role, traits=self.traits, tuning=self.tuning, view=view,
                   ledger=self.ledger, beliefs=self.beliefs, mood=self.mood, memory=self.memory, cover=self.cover,
                   vote_intent=self.vote_intent, pack=dict(pack), rng=self._rng, taste=self.persona.id)

    def options(self, request: Request, view: View) -> list[ScoredOption]:
        """候选：已排除被否决的、按效用降序、至多 top_k 项。纯函数。"""
        ctx = self._ctx(view)
        scored = utility.to_scored(utility.score(ctx, request))
        return utility.cut(scored, self.tuning, self.mood.value('private'), self._rng, request.id)

    def all_options(self, request: Request, view: View) -> list[ScoredOption]:
        """全部选项（含被否决的），给复盘与测试看。纯函数。"""
        return utility.to_scored(utility.score(self._ctx(view), request))

    def decide(self, request: Request, view: View, candidates: list[ScoredOption],
               jev_probs: Mapping[str, float] | None = None) -> Decision:
        ctx = self._ctx(view)
        scores = {s.option_id: s for s in utility.score(ctx, request)}
        mercy: dict[str, float] | None = None
        hs = utility.mercy_target(request, view)
        if hs is not None and self.tuning.mercy_first_night > 0:
            mercy = {f'kill:{hs}': 1.0 - self.tuning.mercy_first_night}
        cands = list(candidates) or utility.cut(utility.to_scored(list(scores.values())), self.tuning,
                                                self.mood.value('private'), self._rng, request.id)
        s = utility.sample(cands, self.tuning, self.mood.value('private'), self._decide_rng, jev_probs, mercy)
        sc = scores.get(s.option_id)
        whys = sc.whys if sc else []
        state = ctx.state
        target = sc.target if sc else None
        note = stance = None
        if request.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL):
            if request.kind == RequestKind.WOLF_PROPOSE:
                note = explain.wolf_note(target, whys)
            if request.day == 1:
                stance = wolf.stance_wish(self.traits, self._rng.fork('stance'))
        if request.kind == RequestKind.VOTE and self.vote_intent is not None and target != self.vote_intent \
                and self.traits.stubborn >= 0.6 and request.context.get('round', 'main') == 'main':
            self.mood.strain(6.0 * self.traits.stubborn, 'changed_vote', '违心改了票', about=target)
        thought = explain.thought(s.option_id, whys, state)
        self.last_thought = thought
        if target is not None and request.kind in (RequestKind.VOTE, RequestKind.SEER_CHECK, RequestKind.HUNTER_SHOOT):
            self.focus = target
        return Decision(request_id=request.id, seat=self.seat, day=request.day, kind=request.kind,
                        option_id=s.option_id, source='jev' if jev_probs else 'rules', candidates=list(cands),
                        probs=s.probs, jev_probs=dict(jev_probs) if jev_probs else None, best_option_id=s.best,
                        state=state, mood_cause=sc.cause if sc else None, thought=thought, note=note,
                        stance=stance)

    # ------------------------------------------------------------------ 发言

    def speech_variants(self, request: Request, view: View) -> list[SpeechPlan]:
        """1 到 4 个发言方案，id 互不相同，已过口径校验。纯函数。"""
        ctx = self._ctx(view)
        plans = _STRATEGY[self.role].speech_candidates(ctx, request)
        return finish(ctx, request, plans)

    def choose_plan(self, variants: list[SpeechPlan],
                    jev_probs: Mapping[str, float] | None = None) -> tuple[SpeechPlan, Decision]:
        if not variants:
            raise ValueError('no speech variants')
        by_id = {v.id: v for v in variants}
        opts = [ScoredOption(option_id=v.id, label=v.label, utility=v.utility, reasons=v.reasons) for v in variants]
        rid = variants[0].request_id
        mood = self.mood.value('private')
        cands = utility.cut(opts, self.tuning, mood, self._rng, f'{rid}:plan')
        s = utility.sample(cands, self.tuning, mood, self._decide_rng, jev_probs)
        plan = by_id[s.option_id]
        state = self.mood.state('private')
        cause = None
        if state != 'steady':
            top = self.mood.top_cause('private')
            cause = top.key if top else None
        self.last_thought = plan.thought
        d = Decision(request_id=rid, seat=self.seat, day=plan.day, kind=RequestKind.SPEAK, option_id=plan.id,
                     source='jev' if jev_probs else 'rules', candidates=cands, probs=s.probs,
                     jev_probs=dict(jev_probs) if jev_probs else None, best_option_id=s.best, state=state,
                     mood_cause=cause, thought=plan.thought)
        return plan, d

    def public_mood(self) -> PublicMood:
        return self.mood.public()

    # ------------------------------------------------------------------ 夜间反思

    def reflect_rules(self, view: View) -> ReflectionResult:
        """规则版反思：定今晚的关注点（不另挪怀疑度——规则的判断已在怀疑度里）。纯函数。"""
        layer = 'public' if self.role == Role.WEREWOLF else 'private'
        ranked = [(s, p) for s, p in self.beliefs.ranked(layer, view.living()) if s != self.seat]
        if self.role == Role.WEREWOLF and view.wolf is not None:
            ranked = [(s, p) for s, p in ranked if s not in view.wolf.teammates]
        focus = ranked[0][0] if ranked else None
        thought = f'最在意 {focus} 号' if focus is not None else ''
        return ReflectionResult(thought=thought, deltas={}, focus=focus, basis=layer, source='rules')

    def apply_reflection(self, result: ReflectionResult) -> None:
        """每夜恰好一次：升记忆，按 reflect_weight 缩放、reflect_cap 封顶挪怀疑度，定关注点。"""
        self.memory.consolidate(max(0, self.day - 1))
        layer = 'public' if (result.basis == 'public' or self.role == Role.WEREWOLF) else 'private'
        for s, d in sorted(result.deltas.items()):
            dp = max(-self.tuning.reflect_cap, min(self.tuning.reflect_cap, d * self.tuning.reflect_weight))
            if dp:
                self.beliefs.nudge(int(s), dp, layer)  # type: ignore[arg-type]
        if result.focus is not None:
            self.focus = result.focus
        if result.thought:
            self.last_thought = result.thought

    # ------------------------------------------------------------------ 快照

    def strategy_name(self) -> str:
        if self.role == Role.WEREWOLF:
            return self._pack.get(self.seat, 'deep')
        if self.role in GODS:
            return 'open_god' if self.cover.role == self.role else 'hide_god'
        return 'villager'

    def snapshot(self, request: Request | None = None) -> MindSnapshot:
        about: list[Seat] = []
        if request is not None:
            about = [o.target for o in request.options if o.target is not None][:3]
        if self.focus is not None:
            about.append(self.focus)
        mems = self.memory.recall(about or None, self.day, 4)
        return MindSnapshot(
            seat=self.seat, day=self.day, role=self.role,
            suspicion={s: round(p, 4) for s, p in self.beliefs.all('private').items()},
            public_suspicion={s: round(p, 4) for s, p in self.beliefs.all('public').items()},
            trust={s: round(v, 4) for s, v in self.mood.all_trust().items()},
            mood=round(self.mood.value('private'), 2), stress=round(self.mood.stress('private'), 2),
            state=self.mood.state('private'), thoughts=self.mood.thoughts('private'), memories=mems,
            strategy=self.strategy_name(), pack=dict(self._pack), cover=self.cover.model_copy(deep=True),
            focus=self.focus, vote_intent=self.vote_intent, last_thought=self.last_thought,
        )


def cover_from_ledger(ledger: ClaimLedger, seat: Seat) -> CoverStory:
    """对外口径 = 账本里属于自己的那部分。"""
    role = ledger.current_role(seat)
    since = None
    if role is not None:
        rc = ledger.role_claims(seat, role)
        since = rc[-1].day if rc else None
    checks = [SeerCheck(night=c.night, target=c.target, result=c.result) for c in ledger.checks_by(seat)]
    pots = ledger.potions_by(seat)
    return CoverStory(role=role, since_day=since, checks=checks,
                      saves=[(p.night, p.target) for p in pots if p.kind == 'save'],
                      poisons=[(p.night, p.target) for p in pots if p.kind == 'poison'])
