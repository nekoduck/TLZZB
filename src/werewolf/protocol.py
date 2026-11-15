"""浏览器与本地服务之间的消息（WebSocket，JSON，一条消息一个对象，用 `t` 区分）。

契约见 `docs/werewolf/ARCHITECTURE.md` 第 7 节。服务端发给真人的只有他的视图里有的东西；
身份、夜间行动、内心想法在终局的 `review` 里才整份给出。

编号：浏览器看到的事件号是按「这名观众看得见的事件」连续编的 `seq`，请求号是服务端另发的令牌。
内核的原始编号从不上线，否则编号的空档会数出夜里有几个人行动。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from werewolf.kernel.types import Event, Phase, Role, Seat, SpeechAct, SpeechKind
from werewolf.mind.types import AvatarSpec

PROTOCOL_VERSION = 1

Speed = Literal['slow', 'normal', 'fast', 'instant']


class _Msg(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SeatRole(_Msg):
    seat: Seat
    role: Role


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
    speed: Speed  # 服务端当前的速度档
    resumable: bool = False  # 有一局可以续上（打完的局在开新局之前也算，重连后能看到复盘）


class PlayerCard(_Msg):
    seat: Seat
    name: str
    is_human: bool = False
    title: str = ''
    tags: list[str] = Field(default_factory=list)
    avatar: AvatarSpec | None = None  # 真人没有人设头像，前端画一个默认的


class RulesInfo(_Msg):
    board: str  # 如「9 人标准局：3 狼 3 民 预言家 女巫 猎人」
    role_counts: dict[str, int]  # 键是 Role 的值
    witch_self_save: Literal['never', 'first_night', 'always']
    last_words_nights: int
    allow_abstain: bool
    allow_empty_kill: bool
    max_days: int


class GameStarted(_Msg):
    t: Literal['game'] = 'game'
    game_id: str
    you: Seat
    your_role: Role
    players: list[PlayerCard]
    rules: RulesInfo
    replay: bool = False


class SessionState(_Msg):
    """会话层的状态。在 `game` 之后、每次 `set` 被接受之后、`can_god_view` 变化时、重放结束时各发一次。
    重放时它是最后一条带历史的消息：它之后的都是现场。"""

    t: Literal['state'] = 'state'
    speed: Speed
    god_view: bool = False
    can_god_view: bool = False  # 真人已出局，且内核不会再向他要任何决定
    open_request: RequestMsg | None = None  # 此刻等着真人答的请求（至多一个）
    over: bool = False


class EventMsg(_Msg):
    """一条对真人可见的内核事件。`ev.id` 已被改写成 `seq`（按可见事件连续编号）。

    `hold_ms`：服务端在这之后至少这么久不发下一条消息（已按速度档折算），前端的动画要在这段时间内演完。
    `wait_ack`：服务端等到 `ack{seq}`（或超时）才继续；只用于别人的发言。重放与 instant 档都不等。
    """

    t: Literal['event'] = 'event'
    seq: int
    ev: Event
    hold_ms: int = 0
    wait_ack: bool = False
    replay: bool = False


NarrationKey = Literal[
    'deal', 'night_fall', 'wolves_wake', 'wolves_sleep', 'seer_wake', 'seer_sleep', 'witch_wake', 'witch_sleep',
    'dawn_peaceful', 'dawn_deaths', 'skill', 'last_words', 'speech_start', 'vote_start', 'vote_result',
    'pk_start', 'pk_vote', 'exile', 'no_exile', 'hunter_shot', 'game_over',
]


class Narration(_Msg):
    """法官的话，只是要显示的文字。画面与顶栏由 `phase` 事件驱动，不由它驱动。"""

    t: Literal['narration'] = 'narration'
    key: NarrationKey
    text: str
    hold_ms: int = 0
    replay: bool = False


class Thinking(_Msg):
    """某个座位正在想（发言前、投票前）。夜里不发，免得从时机上漏信息。重放时不发。"""

    t: Literal['thinking'] = 'thinking'
    seat: Seat
    on: bool
    what: Literal['speak', 'vote'] = 'speak'


class ReactionMsg(_Msg):
    """旁听者的神态。前端在 `seq` 那条事件的动画期间显示它；那条事件已经不在台上就丢掉。重放时不发。"""

    t: Literal['reaction'] = 'reaction'
    seat: Seat
    emote: str  # kernel.types.EMOTES 的键
    label: str  # 中文，如「皱眉」
    quip: str | None = None  # 至多十来个字的一句嘀咕，不含座位号、身份词、投票意向
    intensity: float = 0.5
    seq: int  # 触发它的事件


class ClientOption(_Msg):
    id: str
    label: str
    target: Seat | None = None
    action: Literal['kill', 'check', 'save', 'poison', 'shoot', 'vote', 'none']  # none：空刀、不用药、不开枪、弃票


class RequestMsg(_Msg):
    """轮到真人了。同一时刻至多有一个未答的请求。v1 不限时。

    `context` 按 kind：
      wolf_kill     {'proposals': [{'seat', 'option_id', 'target', 'note', 'stance'}], 'first_night': bool}
      witch_act     {'victim': seat | None, 'victim_known': bool, 'antidote': bool, 'poison': bool}
      hunter_shoot  {'after': 'night' | 'vote'}
      vote          {'round': 'main' | 'pk', 'candidates': [seat]}
      speak         {'index', 'total', 'after': 'night' | 'vote' | None, 'rivals': [seat], 'max_chars': int,
                     'confirm': bool}   # confirm：发言要先预览、确认后才提交
    """

    t: Literal['request'] = 'request'
    id: str  # 服务端发的令牌，不是内核的请求号
    kind: Literal['wolf_kill', 'seer_check', 'witch_act', 'hunter_shoot', 'speak', 'vote']
    options: list[ClientOption] = Field(default_factory=list)
    speech_kind: SpeechKind | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class Accepted(_Msg):
    """真人的动作已被受理。发言在收到文字、开始解析之前就回它，输入框可以关了。"""

    t: Literal['accepted'] = 'accepted'
    request_id: str
    option_id: str | None = None


class Parsed(_Msg):
    """系统怎样理解真人的一段发言（言语行为）。

    `preview=True`：这是对 `preview` 消息的答复，还没提交；玩家可以去掉读错的条目再确认。
    `preview=False`：发言已提交，`seq` 是那条发言事件的号。
    """

    t: Literal['parsed'] = 'parsed'
    preview: bool = False
    request_id: str | None = None
    seq: int | None = None
    acts: list[SpeechAct]
    flags: list[str] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)  # 被剥掉的片段（括号里的动作、冒充法官的句子）
    notice: str | None = None  # 如「括号里的动作不算数」「同一个人说多少遍只算一次」
    replay: bool = False


class PrivateInfo(_Msg):
    """只给真人看的提示：一句可以直接显示的中文。内容同时也在对应的私有事件里。"""

    t: Literal['private'] = 'private'
    key: Literal['role', 'wolf_team', 'wolf_propose', 'wolf_kill', 'seer_result', 'witch_notice', 'witch_used',
                 'hunter_blocked', 'hunter_pass']
    text: str
    seq: int | None = None  # 对应的事件
    replay: bool = False


class GodView(_Msg):
    """上帝视角打开时补发：全部身份，加上此前对真人不可见的事件（按内核顺序）。此后 `event` 改发全部事件。
    不含任何内心想法，那些只在 `review` 里。"""

    t: Literal['god'] = 'god'
    roles: list[SeatRole]
    events: list[Event]


class ReviewThought(_Msg):
    day: int
    phase: Phase
    kind: Literal['decision', 'speech', 'monologue', 'reflection']
    text: str
    event_id: int | None = None  # 这个想法引出的内核事件（发言、投票、夜间行动）；独白与反思为空
    choice: str | None = None  # 选中的那一项的中文
    best: str | None = None  # 规则的头名（与 choice 不同时才给）
    lies: list[int] = Field(default_factory=list)  # 那段发言里不是真心话的 act 的下标
    state: str | None = None  # 当时的心境
    source: str | None = None  # 'rules' | 'jev' | 'claude' | 'jev_select' | 'template' | 'fallback'


class SuspicionPoint(_Msg):
    seat: Seat
    p: float


class ReviewDay(_Msg):
    day: int
    mood: float
    state: str
    suspicion: list[SuspicionPoint]


class ReviewSeat(_Msg):
    seat: Seat
    name: str
    role: Role
    is_human: bool
    alive: bool
    strategy: str = ''
    thoughts: list[ReviewThought] = Field(default_factory=list)
    days: list[ReviewDay] = Field(default_factory=list)


class ReviewStats(_Msg):
    seed: int
    days: int
    calls: int = 0
    jev_calls: int = 0
    claude_calls: int = 0
    spent_usd: float = 0.0
    speech_sources: dict[str, int] = Field(default_factory=dict)


class Review(_Msg):
    """终局复盘：全部身份、全部事件（含夜里的，用内核的原始编号）、每个 AI 每一步的内心想法。"""

    t: Literal['review'] = 'review'
    winner: Literal['wolf', 'good'] | None
    reason: Literal['wolves_dead', 'villagers_dead', 'gods_dead', 'max_days']
    seats: list[ReviewSeat]
    events: list[Event]
    stats: ReviewStats
    replay: bool = False


class CostMsg(_Msg):
    """只在每个白天开始和终局时发；金额取整到分，不带调用次数（次数会漏出夜里的活动量）。"""

    t: Literal['cost'] = 'cost'
    spent_usd: float
    budget: Literal['ok', 'warn', 'exhausted'] = 'ok'


class ErrorMsg(_Msg):
    t: Literal['error'] = 'error'
    code: Literal['illegal_action', 'bad_message', 'no_game', 'not_allowed', 'internal']
    message: str
    request_id: str | None = None
    detail: str | None = None  # IllegalAction 的 code；not_an_option、bad_speech 时请求仍然有效


ServerMsg = Annotated[
    Hello | GameStarted | SessionState | EventMsg | Narration | Thinking | ReactionMsg | RequestMsg | Accepted
    | Parsed | PrivateInfo | GodView | Review | CostMsg | ErrorMsg,
    Field(discriminator='t'),
]


# --------------------------------------------------------------------------- 浏览器 → 服务端


class NewGame(_Msg):
    t: Literal['new_game'] = 'new_game'
    # 名字只在界面上显示；发给模型的文字一律称「N号」。只收汉字与字母，免得名字成了注入的通道。
    name: str = Field(default='你', pattern=r'^[一-鿿A-Za-z]{1,8}$')
    role: Role | Literal['random'] = 'random'
    seat: Seat | None = None  # None = 随机座位
    seed: int | None = Field(default=None, ge=0, lt=2**53)  # 只在服务以 --dev 启动时生效
    speed: Speed | None = None  # None = 用服务端的默认


class Resume(_Msg):
    t: Literal['resume'] = 'resume'


class Preview(_Msg):
    """发言前先让系统读一遍。服务端回 `parsed{preview:true}`，不提交。"""

    t: Literal['preview'] = 'preview'
    request_id: str
    text: str = Field(max_length=500)


class ActionMsg(_Msg):
    t: Literal['action'] = 'action'
    request_id: str
    option_id: str | None = None
    text: str | None = Field(default=None, max_length=500)  # 发言原文
    # 预览过的发言：保留哪几条言语行为（预览结果里的下标）。只能删，不能加。None = 全留。
    keep: list[int] | None = None
    stance: Literal['jump_seer', 'charge', 'deep'] | None = None  # 真人头狼首夜可选的打法


class Ack(_Msg):
    """前端演完了某条 `wait_ack` 事件的动画（一段发言打字完毕或被跳过）。"""

    t: Literal['ack'] = 'ack'
    seq: int


class SetOptions(_Msg):
    t: Literal['set'] = 'set'
    speed: Speed | None = None
    god_view: bool | None = None  # 只在 state.can_god_view 为真时接受


ClientMsg = Annotated[NewGame | Resume | Preview | ActionMsg | Ack | SetOptions, Field(discriminator='t')]

SessionState.model_rebuild()
