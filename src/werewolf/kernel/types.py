"""内核的公共类型：身份、阶段、事件、言语行为、请求与动作、视图。

这份文件是各模块之间的契约（`docs/werewolf/ARCHITECTURE.md` 第 3 节）。
改它要同步改文档；实现模块不得私自加字段绕过它。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Seat = int  # 1..9
Viewer = Seat | Literal['judge']  # 'judge' 是裁判视角，全可见；只给会话层落盘、复盘和测试用


# --------------------------------------------------------------------------- 身份与阵营


class Role(StrEnum):
    WEREWOLF = 'werewolf'
    VILLAGER = 'villager'
    SEER = 'seer'
    WITCH = 'witch'
    HUNTER = 'hunter'


class Team(StrEnum):
    WOLF = 'wolf'
    GOOD = 'good'


GODS: frozenset[Role] = frozenset({Role.SEER, Role.WITCH, Role.HUNTER})

ROLE_ZH: dict[Role, str] = {
    Role.WEREWOLF: '狼人',
    Role.VILLAGER: '平民',
    Role.SEER: '预言家',
    Role.WITCH: '女巫',
    Role.HUNTER: '猎人',
}

TEAM_ZH: dict[Team, str] = {Team.WOLF: '狼人阵营', Team.GOOD: '好人阵营'}


def team_of(role: Role) -> Team:
    return Team.WOLF if role == Role.WEREWOLF else Team.GOOD


def default_roles() -> list[Role]:
    """9 人标准局「预女猎白」：3 狼、3 民、预言家、女巫、猎人。"""
    return [Role.WEREWOLF] * 3 + [Role.VILLAGER] * 3 + [Role.SEER, Role.WITCH, Role.HUNTER]


# --------------------------------------------------------------------------- 阶段


class Phase(StrEnum):
    SETUP = 'setup'
    NIGHT_WOLF = 'night_wolf'
    NIGHT_SEER = 'night_seer'
    NIGHT_WITCH = 'night_witch'
    DAWN = 'dawn'
    HUNTER = 'hunter'
    LAST_WORDS = 'last_words'
    DAY_SPEECH = 'day_speech'
    DAY_VOTE = 'day_vote'
    PK_SPEECH = 'pk_speech'
    PK_VOTE = 'pk_vote'
    GAME_OVER = 'game_over'


NIGHT_PHASES: frozenset[Phase] = frozenset({Phase.NIGHT_WOLF, Phase.NIGHT_SEER, Phase.NIGHT_WITCH})


# --------------------------------------------------------------------------- 配置


class GameConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seats: int = 9
    roles: list[Role] = Field(default_factory=default_roles)
    names: dict[Seat, str] = Field(default_factory=dict)  # 缺的座位叫「N号」
    human_seat: Seat | None = None  # None = 全员 AI（自对弈）
    # 女巫自救：never 不可自救；first_night 仅首夜；always 全程
    witch_self_save: Literal['never', 'first_night', 'always'] = 'first_night'
    last_words_nights: int = 1  # 前几夜的死者有遗言
    max_days: int = 15  # 到这一天白天结束仍未分胜负，判平局
    allow_abstain: bool = True
    allow_empty_kill: bool = True  # 狼人可以空刀


# --------------------------------------------------------------------------- 神态与狼队分工

# 神态的词表。Speech.emote、SpeechPlan.emote、Reaction.emote 都只能取这里的键；内核拒收表外的键。
EMOTES: dict[str, str] = {
    'nod': '点头',
    'frown': '皱眉',
    'sneer': '冷笑',
    'shock': '一愣',
    'think': '沉吟',
    'laugh': '笑了',
    'angry': '拍桌',
    'sweat': '冒汗',
    'silent': '沉默',
    'stare': '盯着看',
    'sigh': '叹气',
    'shrug': '摊手',
}

# 狼人在首夜随提议一起报的打法意愿。内核只存不解释；怎样据此分工见 ARCHITECTURE 4.2。
WolfStance = Literal['jump_seer', 'charge', 'deep']


# --------------------------------------------------------------------------- 言语行为


class Tone(StrEnum):
    CALM = 'calm'  # 平静
    FIRM = 'firm'  # 笃定
    ANGRY = 'angry'  # 恼火
    NERVOUS = 'nervous'  # 紧张
    JOKING = 'joking'  # 打趣
    COLD = 'cold'  # 冷淡
    EARNEST = 'earnest'  # 恳切
    HESITANT = 'hesitant'  # 犹豫


class ActKind(StrEnum):
    CLAIM_ROLE = 'claim_role'  # 自称身份：role
    CLAIM_CHECK = 'claim_check'  # 报查验：target、result、night
    CLAIM_SAVE = 'claim_save'  # 自称女巫救过：target、night
    CLAIM_POISON = 'claim_poison'  # 自称女巫毒过：target、night
    ACCUSE = 'accuse'  # 指某人是狼或可疑：target
    VOUCH = 'vouch'  # 认某人是好人：target
    DEFEND = 'defend'  # 辩解：target 为 None 是替自己辩，否则替别人辩
    VOTE_INTENT = 'vote_intent'  # 表态今天要投谁 / 号召投谁：target
    BACK_CLAIM = 'back_claim'  # 站边：相信 target 的身份声明
    DOUBT_CLAIM = 'doubt_claim'  # 质疑 target 的身份声明
    QUESTION = 'question'  # 点名要 target 解释
    PASS = 'pass'  # 过


class SpeechAct(BaseModel):
    """一条言语行为。它是「某人说过这句话」，永远不是事实。

    `revalidate_instances` 让带私有标注的子类实例（mind 的 PlannedAct）进不了 Speech：
    只有 `SpeechPlan.public_acts()` 产出的纯 SpeechAct 能通过校验。
    """

    model_config = ConfigDict(extra='forbid', revalidate_instances='subclass-instances')

    kind: ActKind
    target: Seat | None = None
    role: Role | None = None  # CLAIM_ROLE 用
    result: Literal['wolf', 'good'] | None = None  # CLAIM_CHECK 用
    night: int | None = None  # 声称发生在第几夜
    weight: int = Field(default=2, ge=1, le=3)  # 语气轻重：1 顺口一提，3 斩钉截铁


SpeechKind = Literal['day', 'pk', 'last_words']
SpeechSource = Literal['human', 'ai']
# 公开发言事件上允许出现的标记。台词走了哪一档、核对是否失败，只写进 decisions.jsonl 与复盘，不上公开事件。
SPEECH_FLAGS: frozenset[str] = frozenset({'injection', 'stage_direction', 'truncated'})


class Speech(BaseModel):
    """一次发言。`acts` 进公开的声明账本；`text` 只给人看，内核不从文字里读事实。

    `text` 只存在于内核的事件日志、发给浏览器的消息和落盘文件里；视图与喂给心智的事件一律是
    去掉文字的副本（`kernel.events.strip_text`），所以任何带底牌的模型调用都碰不到别人的原话。
    """

    model_config = ConfigDict(extra='forbid')

    text: str = Field(max_length=600)
    acts: list[SpeechAct] = Field(default_factory=list, max_length=12)
    tone: Tone = Tone.CALM
    emote: str | None = None  # EMOTES 的键
    source: SpeechSource = 'ai'
    flags: list[str] = Field(default_factory=list)  # 只能取 SPEECH_FLAGS 里的值


# --------------------------------------------------------------------------- 事件


class EventType(StrEnum):
    GAME_START = 'game_start'
    ROLE_DEALT = 'role_dealt'
    WOLF_TEAM = 'wolf_team'
    PHASE = 'phase'
    WOLF_PROPOSE = 'wolf_propose'
    WOLF_KILL = 'wolf_kill'
    SEER_RESULT = 'seer_result'
    WITCH_NOTICE = 'witch_notice'
    WITCH_SAVE = 'witch_save'
    WITCH_POISON = 'witch_poison'
    DAWN = 'dawn'
    DEATH = 'death'
    DEATH_CAUSE = 'death_cause'
    HUNTER_BLOCKED = 'hunter_blocked'
    HUNTER_SHOT = 'hunter_shot'
    ROLE_REVEALED = 'role_revealed'
    SPEECH = 'speech'
    VOTE = 'vote'
    VOTE_RESULT = 'vote_result'
    EXILE = 'exile'
    GAME_OVER = 'game_over'


Visibility = Literal['public', 'wolves', 'seats', 'judge']


class Event(BaseModel):
    """事件日志里的一条。只有内核能造事件（`kernel/events.py`）。字段含义见 ARCHITECTURE 3.3 的表。

    `data` 只放 JSON 原生值（映射的键是字符串，座位出现在列表或值里），必须满足
    `Event.model_validate_json(ev.model_dump_json()) == ev`。发牌阶段的事件 `day = 0`；
    第 n 夜与随后的白天 `day = n`。
    """

    model_config = ConfigDict(extra='forbid')

    id: int  # 从 1 起，连续
    day: int
    phase: Phase
    type: EventType
    actor: Seat | None = None
    target: Seat | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    vis: Visibility = 'public'
    vis_seats: list[Seat] = Field(default_factory=list)  # vis == 'seats' 时有效


def strip_text(ev: Event) -> Event:
    """发言事件去掉文字后的副本；其他事件原样返回。视图与喂给心智的事件都用它。"""
    if ev.type != EventType.SPEECH:
        return ev
    speech = dict(ev.data.get('speech') or {})
    if not speech.get('text'):
        return ev
    speech['text'] = ''
    return ev.model_copy(update={'data': {**ev.data, 'speech': speech}})


# --------------------------------------------------------------------------- 请求与动作


class RequestKind(StrEnum):
    WOLF_PROPOSE = 'wolf_propose'
    WOLF_KILL = 'wolf_kill'
    SEER_CHECK = 'seer_check'
    WITCH_ACT = 'witch_act'
    HUNTER_SHOOT = 'hunter_shoot'
    SPEAK = 'speak'
    VOTE = 'vote'


class Option(BaseModel):
    """合法动作表里的一项。id 形如 `kill:5`、`kill:none`、`check:3`、`save`、`poison:4`、`pass`、
    `shoot:2`、`shoot:none`、`vote:7`、`vote:abstain`。"""

    model_config = ConfigDict(extra='forbid')

    id: str
    target: Seat | None = None
    label: str  # 给人看的中文，如「刀 5 号」


class Request(BaseModel):
    """内核向某个座位要一个决定。SPEAK 没有选项，其余必须从 `options` 里选一项。"""

    model_config = ConfigDict(extra='forbid')

    id: str  # 形如 'r17'，整局唯一
    kind: RequestKind
    seat: Seat
    day: int
    phase: Phase
    options: list[Option] = Field(default_factory=list)
    speech_kind: SpeechKind | None = None  # 仅 SPEAK
    # 附带信息（只含 JSON 原生值，映射的键都是字符串）：
    #   wolf_propose  {'first_night': bool}
    #   wolf_kill     {'proposals': [{'seat': 3, 'option_id': 'kill:5', 'target': 5, 'note': '…', 'stance': 'charge'}]}
    #   witch_act     {'victim': 3 | None, 'victim_known': bool, 'antidote': bool, 'poison': bool}
    #   hunter_shoot  {'after': 'night' | 'vote'}
    #   vote          {'round': 'main' | 'pk', 'candidates': [seat]}
    #   speak         {'index': 2, 'total': 7, 'after': 'night' | 'vote' | None, 'rivals': [seat]}  # rivals 仅 PK
    context: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    seat: Seat
    option_id: str | None = None  # 选择类请求
    speech: Speech | None = None  # SPEAK
    note: str | None = Field(default=None, max_length=80)  # WOLF_PROPOSE 给队友的一句话
    stance: WolfStance | None = None  # WOLF_PROPOSE / WOLF_KILL：首夜报的打法意愿


class IllegalAction(Exception):
    """动作没过内核校验，状态不变。

    `code`：unknown_request（不存在或已答过）/ wrong_seat / not_an_option / missing_speech /
    bad_speech（空、超长、座位越界、神态或标记不在表里）/ game_over。
    """

    def __init__(self, code: str, message: str = '') -> None:
        super().__init__(f'{code}: {message}' if message else code)
        self.code = code


# --------------------------------------------------------------------------- 视图（信息集）


class PlayerPublic(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    name: str
    alive: bool
    is_human: bool = False
    died_day: int | None = None
    died_when: Literal['night', 'vote', 'shot'] | None = None
    revealed_role: Role | None = None  # 只有公开翻出的身份（猎人开枪、终局）


class SeerCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')

    night: int
    target: Seat
    result: Literal['wolf', 'good']


class WitchInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')

    antidote: bool
    poison: bool
    tonight_victim: Seat | None = None  # 只在女巫行动的那个夜里、且解药还在时有值
    saved: list[tuple[int, Seat]] = Field(default_factory=list)  # (夜, 座位)
    poisoned: list[tuple[int, Seat]] = Field(default_factory=list)


class WolfInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')

    teammates: list[Seat]  # 不含自己，含已死的
    kills: list[tuple[int, Seat | None]] = Field(default_factory=list)  # (夜, 目标)；空刀是 None


class PublicView(BaseModel):
    """全桌都知道的局面：只含公开事件（文字已去掉）。写台词、解析真人发言、导演定节奏，只许用它。"""

    model_config = ConfigDict(extra='forbid')

    day: int
    phase: Phase
    players: list[PlayerPublic]
    events: list[Event] = Field(default_factory=list)

    def living(self) -> list[Seat]:
        return [p.seat for p in self.players if p.alive]

    def dead(self) -> list[Seat]:
        return [p.seat for p in self.players if not p.alive]


class View(BaseModel):
    """某个座位此刻知道的一切。心智与带底牌的模型调用只能从它来。

    它必须只由「配置的公开部分 + 对该座位可见的事件」算得出：`kernel.views.build_view(config, seat, events)`
    是纯函数，`Game.view(seat)` 就是用它算的。`events` 里的发言事件不带文字。
    """

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    role: Role
    team: Team
    alive: bool
    day: int
    phase: Phase
    players: list[PlayerPublic]
    known_roles: dict[Seat, Role]  # 自己；狼人还知道队友；再加公开翻出的
    seer_checks: list[SeerCheck] = Field(default_factory=list)  # 仅预言家
    witch: WitchInfo | None = None  # 仅女巫
    wolf: WolfInfo | None = None  # 仅狼人
    hunter_can_shoot: bool | None = None  # 仅猎人
    events: list[Event] = Field(default_factory=list)

    def living(self) -> list[Seat]:
        return [p.seat for p in self.players if p.alive]

    def dead(self) -> list[Seat]:
        return [p.seat for p in self.players if not p.alive]

    def public(self) -> PublicView:
        return PublicView(day=self.day, phase=self.phase, players=self.players,
                          events=[e for e in self.events if e.vis == 'public'])


class GameResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    winner: Team | None  # None = 平局（到了 max_days）
    reason: Literal['wolves_dead', 'villagers_dead', 'gods_dead', 'max_days']
    day: int
    roles: dict[Seat, Role]
    survivors: list[Seat]
