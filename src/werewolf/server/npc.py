"""AI 座位：心智 + 模型适配的装配（ARCHITECTURE 第 8 节 `NpcAgent`）。

- `observe` → `Mind.observe`。
- `act`：规则候选 → （走 Jev、至少两项、头名不一锤定音时）Jev 给概率 → `Mind.decide`。
- `speak`：方案 →（Jev 选方案）→ `choose_plan` → **窄化**：`plan.public()`、`view.public()`、`public_mood()`、
  从请求得出的 Situation、真人原话、字数上限 → `build_brief` → `write_speech`。整步超时就交「过」。
- `reflect`：每夜恰好一次 `apply_reflection`；走 Jev 时先把 Jev 的估计并进规则版反思的结果
  （好人用私有状态文本，狼人用公开状态文本、basis='public'）。Claude 的内心独白只进复盘。

信息流纪律：写台词那一步只拿到 `SpeechBrief`；Jev 只拿到 `render.decision_state` / `public_state` 的文字。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from typing import Any

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    Action,
    ActKind,
    Event,
    GameConfig,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    Speech,
    View,
)
from werewolf.llm import decide as llm_decide
from werewolf.llm import reflect as llm_reflect
from werewolf.llm.config import WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.llm.render import decision_state, public_state
from werewolf.llm.speak import SpeechResult, write_speech
from werewolf.mind import invariants as minv
from werewolf.mind.mind import Mind
from werewolf.mind.types import (
    Decision,
    MindSnapshot,
    MindTuning,
    Pace,
    Persona,
    PlannedAct,
    Quote,
    Reaction,
    ReflectionResult,
    ScoredOption,
    Situation,
    SpeechBrief,
    SpeechPlan,
)
from werewolf.protocol import ReviewThought
from werewolf.server.players import ReviewData
from werewolf.voice.brief import build_brief
from werewolf.voice.compose import compose
from werewolf.voice.digest import public_digest
from werewolf.voice.personas import voice_of

log = logging.getLogger(__name__)

# 每类请求问 Jev 的那一句话
QUESTIONS: dict[RequestKind, str] = {
    RequestKind.WOLF_PROPOSE: '今晚你提议刀谁？',
    RequestKind.WOLF_KILL: '今晚刀谁？',
    RequestKind.SEER_CHECK: '今晚你查验谁？',
    RequestKind.WITCH_ACT: '今晚你怎么用药？',
    RequestKind.VOTE: '这一轮你投谁？',
    RequestKind.HUNTER_SHOOT: '你开枪带走谁，还是不开枪？',
}
PLAN_QUESTION = '这次发言你打算怎么说？'


def _decisive(cands: Sequence[ScoredOption], tuning: MindTuning) -> bool:
    return len(cands) < 2 or cands[0].utility - cands[1].utility >= tuning.decisive_gap


def option_text(label: str, reasons: Sequence[str]) -> str:
    """Jev 的选项描述 = 中文标签 + 效用层给的头一条理由。"""
    return f'{label}：{reasons[0]}' if reasons else label


def situation_of(request: Request, view: View) -> Situation:
    """从请求（与公开的死亡信息）得出发言的场合。"""
    ctx = request.context
    died = None
    if request.speech_kind == 'last_words':
        died = 'night' if ctx.get('after') == 'night' else 'vote'
        me = next((p for p in view.players if p.seat == request.seat), None)
        if me is not None and me.died_when is not None:
            died = me.died_when
    return Situation(index=int(ctx.get('index') or 0), total=int(ctx.get('total') or 1),
                     rivals=[int(s) for s in ctx.get('rivals') or []], died=died)


class NpcAgent:
    """一个 AI 座位。确定的：随机数只来自构造时给的 `rng`（会话层的分流）。"""

    def __init__(self, seat: Seat, role: Role, persona: Persona, rng: Rng, gateway: Gateway,
                 ww: WerewolfSettings, config: GameConfig, *, tuning: MindTuning | None = None,
                 check_mind: bool = False, monologue: bool = True) -> None:
        self.seat = seat
        self.role = role
        self.persona = persona
        self.gw = gateway
        self.ww = ww
        self.config = config
        self.tuning = tuning or ww.mind
        self.mind = Mind(seat, persona, role, config, rng.fork('mind'), self.tuning)
        self._speak_rng = rng.fork('speak')
        self.voice = voice_of(persona)
        self.check_mind = check_mind
        self.use_monologue = monologue
        # ---- 记录（复盘、自对弈统计用） ----
        self.decisions: list[Decision] = []
        self.plans: list[SpeechPlan] = []
        self.variants: list[list[SpeechPlan]] = []
        self.results: list[SpeechResult] = []
        self.briefs: list[SpeechBrief] = []
        self.commitments: list[tuple[Seat, int, Seat, str]] = []  # (座位, 天, 说过要投的人, 实际选项)
        self.monologues: list[tuple[int, str]] = []
        self.reflections: list[tuple[int, ReflectionResult]] = []  # (第几夜, 结果)
        self.snapshots_by_day: dict[int, MindSnapshot] = {}
        self.violations: list[str] = []
        self.timeouts = 0
        self.last_brief: SpeechBrief | None = None
        self._reflected: set[int] = set()
        self._side: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ 观察

    def observe(self, event: Event, view: View) -> list[Reaction]:
        return self.mind.observe(event, view)

    # ------------------------------------------------------------------ 选择类请求

    def _jev_on(self) -> bool:
        return self.gw.route('decide') == 'jev' and self.tuning.jev_weight > 0

    def _state(self, view: View, request: Request | None) -> str:
        snap = self.mind.snapshot(request)
        return decision_state(view, snap, self.persona, public_digest(view.public()))

    async def act(self, request: Request, view: View) -> tuple[Action, Decision]:
        intent = self.mind.vote_intent
        cands = self.mind.options(request, view)
        probs: dict[str, float] | None = None
        if self._jev_on() and not _decisive(cands, self.tuning):
            question = QUESTIONS.get(request.kind, '你选哪一项？')
            opts = {o.option_id: option_text(o.label, o.reasons) for o in cands}
            probs = await llm_decide.choose(self.gw, 'J2_act', self._state(view, request), question, opts)
        dec = self.mind.decide(request, view, cands, probs)
        if request.kind == RequestKind.VOTE and request.context.get('round', 'main') == 'main' \
                and intent is not None and any(o.target == intent for o in request.options):
            self.commitments.append((self.seat, request.day, intent, dec.option_id))
        if self.check_mind:
            self.violations += [f'mind:{self.seat}:{v}' for v in minv.check(self.mind.snapshot(request), None, view)]
        self.decisions.append(dec)
        wolfy = request.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL)
        stance = dec.stance if wolfy and request.day == 1 else None
        note = dec.note[:80] if dec.note and request.kind == RequestKind.WOLF_PROPOSE else None
        action = Action(request_id=request.id, seat=self.seat, option_id=dec.option_id, note=note,
                        stance=stance)  # type: ignore[arg-type]
        return action, dec

    # ------------------------------------------------------------------ 发言

    def _max_chars(self, plan: SpeechPlan, pace: Pace) -> int:
        cap = pace.max_chars.get(plan.length) or pace.max_chars.get('medium') or 120
        return max(20, min(int(cap), self.ww.limits.max_speech_chars))

    def _brief(self, plan: SpeechPlan, request: Request, view: View, pace: Pace,
               heard: Sequence[Quote]) -> SpeechBrief:
        """窄化：从这里往后只剩公开族的类型。"""
        return build_brief(plan.public(), view.public(), self.voice, self.mind.public_mood(),
                           situation_of(request, view), list(heard), self._max_chars(plan, pace))

    def _pass_plan(self, request: Request) -> SpeechPlan:
        return SpeechPlan(id='pass_timeout', seat=self.seat, day=request.day, kind=request.speech_kind or 'day',
                          request_id=request.id, variant='pass', label='过', acts=[PlannedAct(kind=ActKind.PASS)],
                          length='short', thought='（来不及想，先过）')

    async def speak(self, request: Request, view: View, pace: Pace,
                    heard: Sequence[Quote] = ()) -> tuple[Action, SpeechPlan, Decision, SpeechResult]:
        lim = self.ww.limits
        variants = self.mind.speech_variants(request, view)
        probs: dict[str, float] | None = None
        if self._jev_on() and len(variants) >= 2:
            ordered = sorted(variants, key=lambda v: -v.utility)
            if ordered[0].utility - ordered[1].utility < self.tuning.decisive_gap:
                opts = {v.id: option_text(v.label or v.variant, v.reasons) for v in variants}
                probs = await llm_decide.choose(self.gw, 'J2_plan', self._state(view, request), PLAN_QUESTION, opts)
        plan, dec = self.mind.choose_plan(variants, probs)
        if self.check_mind:
            self.violations += [f'mind:{self.seat}:{v}' for v in minv.check(self.mind.snapshot(request), plan, view)]
        brief = self._brief(plan, request, view, pace, heard)
        rng = self._speak_rng.fork(request.id)
        # Claude 那一档自己有 speech_timeout_s；多留半个 decide 的时间给核对与退档，整步仍在会话层的上限之内
        budget = lim.speech_timeout_s + 0.5 * lim.decide_timeout_s
        try:
            result = await asyncio.wait_for(write_speech(self.gw, brief, rng), budget)
        except TimeoutError:
            self.timeouts += 1
            plan = self._pass_plan(request)
            brief = self._brief(plan, request, view, pace, ())
            text = compose(brief, rng.fork('pass'))
            result = SpeechResult(text=text, tier='template', rejected=[{'tier': 'all', 'reasons': ['timeout']}])
            dec = dec.model_copy(update={'option_id': plan.id, 'thought': plan.thought})
        self.last_brief = brief
        speech = Speech(text=result.text, acts=plan.public_acts(), tone=plan.tone, emote=plan.emote, source='ai')
        self.decisions.append(dec)
        self.plans.append(plan)
        self.variants.append(variants)
        self.results.append(result)
        self.briefs.append(brief)
        return Action(request_id=request.id, seat=self.seat, speech=speech), plan, dec, result

    # ------------------------------------------------------------------ 夜间反思

    async def reflect(self, view: View) -> None:
        """每夜恰好一次 apply_reflection（被取消也保证规则版落地）；独白另起任务，不挡夜里的行动。"""
        night = view.day
        if night in self._reflected:
            return
        result = self.mind.reflect_rules(view)
        try:
            if self.gw.route('reflect') == 'jev':
                est = await self._jev_reflection(view)
                if est is not None:
                    result = result.model_copy(update={
                        'deltas': dict(est.deltas), 'focus': est.focus if est.focus is not None else result.focus,
                        'source': 'jev', 'basis': est.basis})
        finally:
            if night not in self._reflected:
                self._reflected.add(night)
                self.mind.apply_reflection(result)
                self.reflections.append((night, result))
        if self.use_monologue and self.gw.route('monologue') == 'claude':
            state = self._state(view, None)
            t = asyncio.ensure_future(self._monologue(night, state))
            self._side.add(t)
            t.add_done_callback(self._side.discard)

    async def _jev_reflection(self, view: View) -> ReflectionResult | None:
        snap = self.mind.snapshot()
        others = [s for s in view.living() if s != self.seat]
        if self.role == Role.WEREWOLF:
            pv = view.public()
            state = public_state(pv, snap.public_suspicion, self.persona, public_digest(pv), seat=self.seat)
            return await llm_reflect.reflect(self.gw, state, others, snap.public_suspicion, 'public')
        known = set(view.known_roles)
        seats = [s for s in others if s not in known]
        state = decision_state(view, snap, self.persona, public_digest(view.public()))
        return await llm_reflect.reflect(self.gw, state, seats, snap.suspicion, 'private')

    async def _monologue(self, night: int, state: str) -> None:
        with contextlib.suppress(Exception):
            text = await llm_reflect.monologue(self.gw, state, self.persona)
            if text:
                self.monologues.append((night, text))

    async def aclose(self) -> None:
        """等还没写完的独白（自对弈结束时用）；不抛错。"""
        if self._side:
            await asyncio.gather(*list(self._side), return_exceptions=True)

    # ------------------------------------------------------------------ 复盘钩子

    def snapshot(self) -> MindSnapshot:
        snap = self.mind.snapshot()
        self.snapshots_by_day[snap.day] = snap
        return snap

    def review(self) -> ReviewData:
        thoughts = [ReviewThought(day=n, phase=Phase.NIGHT_WOLF, kind='monologue', text=t[:400], source='claude')
                    for n, t in self.monologues]
        for n, r in self.reflections:
            if r.thought:
                thoughts.append(ReviewThought(day=n, phase=Phase.NIGHT_WOLF, kind='reflection', text=r.thought,
                                              source=r.source))
        return ReviewData(strategy=self.mind.strategy_name(), thoughts=thoughts)


def make_npc(seat: Seat, role: Role, persona: Persona, rng: Rng, gateway: Gateway, ww: WerewolfSettings,
             config: GameConfig, **kw: Any) -> NpcAgent:
    """会话层的默认玩家工厂。"""
    return NpcAgent(seat, role, persona, rng, gateway, ww, config, **kw)


