"""会话层眼里的「玩家」：AI 座位（与托管的真人座位）都实现 `Player` 协议。

`Player` 就是 ARCHITECTURE 第 8 节的 `NpcAgent` 接口；真正的 `NpcAgent` 在 `server/npc.py`，
这里另给一个只走随机合法动作的 `RandomPlayer`，供测试与 npc 未接入时使用。

可选钩子（会话层用 getattr 探测，没有就跳过）：
- `review() -> ReviewData | None`：终局复盘用的补充材料（打法、独白与反思等会话层看不到的想法、按天的心境）。
  决定与发言的想法由会话层从 `act` / `speak` 的返回值自己整理，不必在这里重复。
- `snapshot() -> MindSnapshot | None`，或属性 `mind`（带 `snapshot()`）：会话层在每个白天开始时取一次，
  记成复盘里该座位那一天的心情与怀疑度。
- `last_brief`：最近一次发言用的 `SpeechBrief`（写进 briefs.jsonl）；没有就记 None。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, get_args, runtime_checkable

from pydantic import BaseModel, Field

from werewolf.kernel.autoplay import random_speech
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    EMOTES,
    Action,
    Event,
    EventType,
    Request,
    RequestKind,
    Role,
    Seat,
    View,
    WolfStance,
)
from werewolf.llm.speak import SpeechResult
from werewolf.mind.types import (
    Decision,
    Pace,
    Persona,
    PlannedAct,
    Quote,
    Reaction,
    ScoredOption,
    SpeechPlan,
)
from werewolf.protocol import ReviewDay, ReviewThought

_STANCES: tuple[str, ...] = get_args(WolfStance)
_QUIP_KEYS = ('doubt', 'agree', 'annoyed', None, None)


class ReviewData(BaseModel):
    """`Player.review()` 的返回值：会话层拼复盘时追加的材料。"""

    strategy: str = ''
    thoughts: list[ReviewThought] = Field(default_factory=list)  # 独白、反思等（决定与发言由会话层整理）
    days: list[ReviewDay] = Field(default_factory=list)  # 给了就用它，不再从 snapshot 取


@runtime_checkable
class Player(Protocol):
    """可选钩子（不在协议里，按 getattr 取）：`snapshot()`、`review()`、`fallback_act()`，以及
    `aclose()`——等自己另起的副任务（独白）写完；它被取消时要连带取消这些副任务（会话取消一局时靠这个不漏任务）。"""

    seat: Seat
    persona: Persona

    def observe(self, event: Event, view: View) -> list[Reaction]: ...

    async def act(self, request: Request, view: View) -> tuple[Action, Decision]: ...

    async def speak(self, request: Request, view: View, pace: Pace,
                    heard: Sequence[Quote] = ()) -> tuple[Action, SpeechPlan, Decision, SpeechResult]: ...

    async def reflect(self, view: View) -> None: ...


class RandomPlayer:
    """随机合法动作、占位发言、偶尔一个随机神态。确定的：只用传入的随机数。"""

    def __init__(self, seat: Seat, role: Role, persona: Persona, rng: Rng, *, react_rate: float = 0.15) -> None:
        self.seat = seat
        self.role = role
        self.persona = persona
        self.rng = rng.fork('act')
        self._react = rng.fork('react')
        self.react_rate = react_rate
        self.reflections = 0
        self.last_brief = None

    def observe(self, event: Event, view: View) -> list[Reaction]:
        if event.vis != 'public' or event.actor == self.seat:
            return []
        if event.type not in (EventType.SPEECH, EventType.VOTE, EventType.DEATH):
            return []
        if not self._react.chance(self.react_rate):
            return []
        emote = self._react.choice(sorted(EMOTES))
        quip = self._react.choice(_QUIP_KEYS)
        return [Reaction(seat=self.seat, emote=emote, quip_key=quip,
                         intensity=round(self._react.uniform(0.3, 0.9), 2), event_id=event.id)]

    async def act(self, request: Request, view: View) -> tuple[Action, Decision]:
        opt = self.rng.choice(request.options)
        stance = note = None
        if request.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL) and request.day == 1:
            stance = self.rng.choice(_STANCES)
        if request.kind == RequestKind.WOLF_PROPOSE and self.rng.chance(0.3):
            note = f'刀{opt.target}号吧' if opt.target else '空刀吧'
        action = Action(request_id=request.id, seat=self.seat, option_id=opt.id, note=note,
                        stance=stance)  # type: ignore[arg-type]
        cands = [ScoredOption(option_id=o.id, label=o.label, utility=0.0) for o in request.options]
        n = len(request.options)
        dec = Decision(request_id=request.id, seat=self.seat, day=request.day, kind=request.kind,
                       option_id=opt.id, candidates=cands, probs={o.id: 1 / n for o in request.options},
                       best_option_id=request.options[0].id, thought=f'随便选了：{opt.label}', note=note,
                       stance=stance)
        return action, dec

    async def speak(self, request: Request, view: View, pace: Pace,
                    heard: Sequence[Quote] = ()) -> tuple[Action, SpeechPlan, Decision, SpeechResult]:
        speech = random_speech(self.rng, self.seat, len(view.players), request.day)
        kind = request.speech_kind or 'day'
        plan = SpeechPlan(id='random', seat=self.seat, day=request.day, kind=kind, request_id=request.id,
                          variant='random', label='随口说两句',
                          acts=[PlannedAct(**a.model_dump()) for a in speech.acts],
                          tone=speech.tone, emote=speech.emote, thought='没什么想法，随便说说。')
        dec = Decision(request_id=request.id, seat=self.seat, day=request.day, kind=request.kind,
                       option_id=plan.id, best_option_id=plan.id, probs={plan.id: 1.0}, thought=plan.thought)
        result = SpeechResult(text=speech.text, tier='template', rejected=[])
        return Action(request_id=request.id, seat=self.seat, speech=speech), plan, dec, result

    async def reflect(self, view: View) -> None:
        self.reflections += 1
