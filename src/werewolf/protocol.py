"""浏览器与本地服务之间的消息（WebSocket，JSON，一条消息一个对象，用 `t` 区分）。

契约见 `docs/werewolf/ARCHITECTURE.md` 第 7 节。服务端发给真人的只有他的视图里有的东西；
身份、夜间行动、内心想法在终局的 `review` 里才整份给出。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from werewolf.kernel.types import Event, Role, Seat, SpeechAct, SpeechKind
from werewolf.mind.types import AvatarSpec

PROTOCOL_VERSION = 1

Speed = Literal['slow', 'normal', 'fast', 'instant']


class _Msg(BaseModel):
    model_config = ConfigDict(extra='forbid')


# --------------------------------------------------------------------------- 服务端 → 浏览器


class LlmStatus(_Msg):
    jev: bool  # Jev 可用
    claude: bool  # Claude 可用
    speech: Literal['claude', 'jev_select', 'template']  # 台词此刻走哪一档
    offline: bool = False  # --offline：全走规则与模板


class Hello(_Msg):
    t: Literal['hello'] = 'hello'
    version: int = PROTOCOL_VERSION
    llm: LlmStatus
    resumable: bool = False  # 有一局没打完，可以续上


class PlayerCard(_Msg):
    seat: Seat
    name: str
    is_human: bool = False
    title: str = ''
    tags: list[str] = Field(default_factory=list)
    avatar: AvatarSpec | None = None  # 真人没有人设头像，前端画一个默认的


class GameStarted(_Msg):
    t: Literal['game'] = 'game'
    game_id: str
    seed: int
    you: Seat
    your_role: Role
    players: list[PlayerCard]
    rules: dict[str, Any] = Field(default_factory=dict)  # 展示用：板子、女巫自救规则等
    replay: bool = False  # 续局时重放历史消息：前端不放动画


class EventMsg(_Msg):
    """一条对真人可见的内核事件。重连时历史事件带 replay=True。"""

    t: Literal['event'] = 'event'
    ev: Event
    replay: bool = False


class Narration(_Msg):
    """法官的话。`key` 是台词的种类，前端可据此换画面，如 'night_fall'、'wolves_wake'、'dawn_peaceful'。"""

    t: Literal['narration'] = 'narration'
    key: str
    text: str
    replay: bool = False


class Thinking(_Msg):
    """某个座位正在想（发言前、行动前）。夜里不发，免得从时机上漏信息。"""

    t: Literal['thinking'] = 'thinking'
    seat: Seat
    on: bool
    what: Literal['speak', 'vote'] = 'speak'


class ReactionMsg(_Msg):
    t: Literal['reaction'] = 'reaction'
    seat: Seat
    emote: str  # mind.types.EMOTES 的键
    label: str  # 中文，如「皱眉」
    quip: str | None = None


class ClientOption(_Msg):
    id: str
    label: str
    target: Seat | None = None


class RequestMsg(_Msg):
    """轮到真人了。`kind` 同内核的 RequestKind；speak 没有 options。"""

    t: Literal['request'] = 'request'
    id: str
    kind: Literal['wolf_kill', 'seer_check', 'witch_act', 'hunter_shoot', 'speak', 'vote']
    options: list[ClientOption] = Field(default_factory=list)
    speech_kind: SpeechKind | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float | None = None  # None = 不限时
    replay: bool = False


class Parsed(_Msg):
    """系统怎样理解真人刚才那段发言（言语行为）。给玩家看，也让他知道哪些话没被当真。"""

    t: Literal['parsed'] = 'parsed'
    event_id: int
    acts: list[SpeechAct]
    flags: list[str] = Field(default_factory=list)
    notice: str | None = None  # 如「括号里的动作不算数」「冒充法官的话已被忽略」


class PrivateInfo(_Msg):
    """只给真人看的提示，如查验结果、女巫得知的刀口、队友的提议。内容同时也在对应的私有事件里。"""

    t: Literal['private'] = 'private'
    key: str  # 'seer_result' | 'witch_notice' | 'wolf_team' | 'wolf_propose' | 'hunter_blocked'
    text: str
    data: dict[str, Any] = Field(default_factory=dict)
    replay: bool = False


class ReviewSeat(_Msg):
    seat: Seat
    name: str
    role: Role
    is_human: bool
    alive: bool
    strategy: str = ''
    thoughts: list[dict[str, Any]] = Field(default_factory=list)  # [{day, phase, text, kind}]
    suspicion_by_day: dict[int, dict[Seat, float]] = Field(default_factory=dict)


class Review(_Msg):
    """终局复盘：全部身份、全部事件（含夜里的）、每个 AI 每一步的内心想法。"""

    t: Literal['review'] = 'review'
    winner: Literal['wolf', 'good'] | None
    reason: str
    seats: list[ReviewSeat]
    events: list[Event]
    stats: dict[str, Any] = Field(default_factory=dict)  # 模型调用次数、花费、时延等


class CostMsg(_Msg):
    t: Literal['cost'] = 'cost'
    spent_usd: float
    calls: int


class ErrorMsg(_Msg):
    t: Literal['error'] = 'error'
    code: str  # 'illegal_action' | 'bad_message' | 'no_game' | 'internal'
    message: str


ServerMsg = (
    Hello | GameStarted | EventMsg | Narration | Thinking | ReactionMsg | RequestMsg | Parsed | PrivateInfo
    | Review | CostMsg | ErrorMsg
)


# --------------------------------------------------------------------------- 浏览器 → 服务端


class NewGame(_Msg):
    t: Literal['new_game'] = 'new_game'
    name: str = Field(default='你', max_length=12)
    role: Role | Literal['random'] = 'random'
    seat: Seat | None = None  # None = 随机座位
    seed: int | None = None
    speed: Speed = 'normal'


class Resume(_Msg):
    t: Literal['resume'] = 'resume'


class ActionMsg(_Msg):
    t: Literal['action'] = 'action'
    request_id: str
    option_id: str | None = None
    text: str | None = Field(default=None, max_length=500)  # 发言原文


class Ack(_Msg):
    """前端演完了某条事件的动画（比如一段发言打字完毕）。服务端据此走下一步，等不到也会超时继续。"""

    t: Literal['ack'] = 'ack'
    event_id: int


class SetOptions(_Msg):
    t: Literal['set'] = 'set'
    speed: Speed | None = None
    god_view: bool | None = None  # 出局之后才允许打开


ClientMsg = NewGame | Resume | ActionMsg | Ack | SetOptions
