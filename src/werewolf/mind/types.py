"""心智层的公共类型：人设、理由、发言计划、台词简报、决定、反应、心智快照。

契约见 `docs/werewolf/ARCHITECTURE.md` 第 4 节。心智里的一切数值都由规则计算，
模型只读它们（作为输入），从不写它们；唯一的例外是夜间反思，写入量由规则封顶。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from werewolf.kernel.types import RequestKind, Role, Seat, SpeechAct, SpeechKind, Tone

# --------------------------------------------------------------------------- 人设


class Traits(BaseModel):
    """性格数值，都在 0 到 1 之间。它们折算成决策权重（十字军之王 3 的做法）。"""

    model_config = ConfigDict(extra='forbid')

    boldness: float = Field(ge=0, le=1)  # 胆量：敢跳身份、敢带队、敢开枪
    logic: float = Field(ge=0, le=1)  # 理性：看证据多过看感觉
    talkative: float = Field(ge=0, le=1)  # 话量
    stubborn: float = Field(ge=0, le=1)  # 固执：改主意的阻力
    vengeful: float = Field(ge=0, le=1)  # 记仇
    conformity: float = Field(ge=0, le=1)  # 从众
    deception: float = Field(ge=0, le=1)  # 演技：做狼时撒谎的本事


class StyleCard(BaseModel):
    """说话的样子。全部原创，不引用任何现成作品里的人物。"""

    model_config = ConfigDict(extra='forbid')

    sentence_len: Literal['short', 'medium', 'long'] = 'medium'
    catchphrases: list[str] = Field(default_factory=list)  # 口头禅
    openers: list[str] = Field(default_factory=list)  # 开场的习惯说法
    closers: list[str] = Field(default_factory=list)  # 收尾的习惯说法
    fillers: list[str] = Field(default_factory=list)  # 语气词、填充词
    quirks: list[str] = Field(default_factory=list)  # 说话习惯的白话描述，给写台词的模型看
    samples: list[str] = Field(default_factory=list)  # 3 到 6 句示范台词


class AvatarSpec(BaseModel):
    """头像参数。前端按它画 SVG，不用任何外部图片。"""

    model_config = ConfigDict(extra='forbid')

    bg: str  # 底色，十六进制
    skin: str
    hair: str
    hair_style: Literal['short', 'long', 'bun', 'cap', 'hood', 'bald', 'curly', 'ponytail', 'bob', 'spiky']
    accessory: Literal['none', 'glasses', 'scarf', 'earring', 'hat', 'headphones', 'beard', 'pipe', 'flower', 'mask'] = 'none'
    outfit: str = '#3b4a6b'  # 衣服颜色


class Persona(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str  # 昵称，2 到 4 个字
    title: str  # 一句话身份，如「退休语文老师」
    tags: list[str] = Field(default_factory=list, max_length=3)  # 玩家一眼能读懂的性格标签
    traits: Traits
    bio: str  # 第一人称自述，150 到 300 字。长篇自述比形容词清单准
    style: StyleCard
    avatar: AvatarSpec


# --------------------------------------------------------------------------- 理由与计划


class ReasonCode(StrEnum):
    SEER_CHECK = 'seer_check'  # 查验结果（自己的，或自己相信的预言家的）
    ACCUSED_ME = 'accused_me'  # 他踩过我
    VOTED_ME = 'voted_me'  # 他投过我
    VOTED_GOOD = 'voted_good'  # 他把票投给了我认定的好人
    VOTED_WITH_WOLF = 'voted_with_wolf'  # 他和疑似狼人同票，或保过疑似狼人
    DEFENDED_WOLF = 'defended_wolf'  # 他替疑似狼人说话
    COUNTER_CLAIM = 'counter_claim'  # 对跳：两个人抢同一个身份
    LATE_CLAIM = 'late_claim'  # 起跳太晚
    CONTRADICTION = 'contradiction'  # 前后说法矛盾
    QUIET = 'quiet'  # 划水、不表态
    TOO_EAGER = 'too_eager'  # 带节奏太急
    FOLLOWS_CROWD = 'follows_crowd'  # 只会跟票
    KILL_PATTERN = 'kill_pattern'  # 从夜里死的是谁反推
    CLAIM_UNCONTESTED = 'claim_uncontested'  # 身份没人对跳
    VOUCHED_BY_TRUSTED = 'vouched_by_trusted'  # 我信的人保了他
    SAVED_BY_WITCH = 'saved_by_witch'  # 银水
    OFF_GAME = 'off_game'  # 冒充法官、贴脸、场外
    SELF_PRESERVE = 'self_preserve'  # 自保
    TEAM = 'team'  # 狼人的战术考虑。只在内部用，永不说出口
    GUT = 'gut'  # 直觉


class PlannedAct(SpeechAct):
    """计划里的一条言语行为：公开部分（SpeechAct）加上私有标注。"""

    reason: ReasonCode | None = None
    evidence: list[int] = Field(default_factory=list)  # 佐证的事件 id，只能是公开事件
    truthful: bool = True  # 这句是不是真心话。只留在心智里，不进公开事件
    fuzzy: bool = False  # 记得不牢，说的时候带「好像」「我记得」


class SpeechPlan(BaseModel):
    """先决定说什么（这份计划），再写怎么说（台词）。计划里的 acts 原样进内核的声明账本。"""

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    day: int
    kind: SpeechKind
    variant: str  # 方案名，如 'reveal_seer'、'hide'、'push_suspect'、'counter_claim'、'pass'
    acts: list[PlannedAct] = Field(default_factory=list)
    tone: Tone = Tone.CALM
    length: Literal['short', 'medium', 'long'] = 'medium'
    emote: str | None = None
    cover_role: Role | None = None  # 对外口径的身份；None 表示这次不表态
    addressed: list[Seat] = Field(default_factory=list)  # 主要在回应谁
    utility: float = 0.0
    thought: str = ''  # 内心想法，私有，终局复盘时才给玩家看

    def public_acts(self) -> list[SpeechAct]:
        keep = set(SpeechAct.model_fields)
        return [SpeechAct(**{k: v for k, v in a.model_dump().items() if k in keep}) for a in self.acts]


class BriefPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    act: SpeechAct
    text: str  # 要点的白话，如「报查验：昨夜验了 7 号，是狼人」
    reason: str | None = None  # 理由的白话，如「他昨天把票投给了 3 号」
    hedge: bool = False  # 说的时候留余地


class PersonaVoice(BaseModel):
    """人设里与说话有关的部分。不含任何身份信息。"""

    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    title: str
    tags: list[str]
    bio: str
    style: StyleCard


class SpeechBrief(BaseModel):
    """写台词的一方（Claude、Jev 选句、模板）能看到的全部内容。

    这里没有底牌：只有公开事件、对外口径、这次要说的要点。狼人的简报里不出现「狼人」身份、
    不出现队友、不出现夜里的事；预言家的查验只有在计划决定说出口时，才以要点的形式出现。
    """

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    name: str
    day: int
    kind: SpeechKind
    persona: PersonaVoice
    cover_role: Role | None = None
    tone: Tone = Tone.CALM
    length: Literal['short', 'medium', 'long'] = 'medium'
    max_chars: int = 160
    emote: str | None = None
    points: list[BriefPoint] = Field(default_factory=list)
    names: dict[Seat, str] = Field(default_factory=dict)
    alive: list[Seat] = Field(default_factory=list)
    dead: list[Seat] = Field(default_factory=list)
    recent: list[str] = Field(default_factory=list)  # 最近的公开事实句（内核渲染），由旧到新
    addressed: list[Seat] = Field(default_factory=list)
    mood_hint: str = ''  # 如「有些恼火：2 号连着两天踩你」。只来自公开事件
    forbidden: list[str] = Field(default_factory=list)  # 不许说的事


# --------------------------------------------------------------------------- 决定与反应


class ScoredOption(BaseModel):
    model_config = ConfigDict(extra='forbid')

    option_id: str
    label: str
    utility: float
    reasons: list[str] = Field(default_factory=list)  # 白话理由，给 Jev 当选项描述的后半句，也进复盘
    vetoed: bool = False  # 效用层否决：与目标或底线冲突，模型看不到这一项
    veto_reason: str | None = None


DecisionSource = Literal['rules', 'jev', 'claude', 'fallback']


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    seat: Seat
    day: int
    kind: RequestKind
    option_id: str
    source: DecisionSource = 'rules'
    candidates: list[ScoredOption] = Field(default_factory=list)
    probs: dict[str, float] = Field(default_factory=dict)  # 效用与模型概率混合后的取样分布
    jev_probs: dict[str, float] | None = None
    best_option_id: str = ''  # 规则效用最高的一项；与 option_id 不同即「非最优选择」
    thought: str = ''
    note: str | None = None  # 狼人夜里给队友的一句话


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


class Reaction(BaseModel):
    """语言之外的通道：别人发言或出事时，角色的神态反应。由规则产生，不调模型。"""

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    emote: str  # EMOTES 的键
    quip: str | None = Field(default=None, max_length=16)  # 至多十来个字的一句嘀咕
    intensity: float = Field(default=0.5, ge=0, le=1)
    event_id: int  # 触发它的事件


# --------------------------------------------------------------------------- 心智快照


class Thought(BaseModel):
    """一条念头：带数值、有来由、会过期（环世界的做法）。心情是念头之和。"""

    model_config = ConfigDict(extra='forbid')

    key: str  # 如 'accused_by:3'
    text: str  # 如「3 号当众踩我」
    value: float  # 对心情的加减
    day: int
    expires_day: int | None = None
    about: Seat | None = None
    event_id: int | None = None


class Memory(BaseModel):
    """一条记忆。只能由内核事件产生（event_id 必填），所以角色不会记得没发生过的事。"""

    model_config = ConfigDict(extra='forbid')

    event_id: int
    day: int
    kind: str  # 如 'accused_me'、'voted_me'、'vouched_me'、'death'、'claim'、'my_check'
    about: list[Seat] = Field(default_factory=list)
    text: str
    intensity: float = Field(ge=0, le=10)  # 情绪强度；槽位满了先挤掉弱的
    slot: Literal['short', 'long', 'core'] = 'short'
    private: bool = False  # 来自私有事件（查验、队友、毒药）。私有记忆不进台词简报


class MindSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    day: int
    role: Role
    suspicion: dict[Seat, float] = Field(default_factory=dict)  # 认为对方是狼的概率，0 到 1
    trust: dict[Seat, float] = Field(default_factory=dict)  # 交情：-1 记仇 到 +1 感恩
    mood: float = 50.0  # 0 到 100，50 是平常
    stress: float = 0.0  # 0 到 100
    state: Literal['steady', 'confident', 'tense', 'tilted'] = 'steady'  # tilted = 破防
    thoughts: list[Thought] = Field(default_factory=list)
    memories: list[Memory] = Field(default_factory=list)
    strategy: str = ''  # 当前打法，如 'jump_seer'、'deep_cover'、'hide_god'
    cover_role: Role | None = None
    last_thought: str = ''


class ReflectionResult(BaseModel):
    """夜间反思的产出。`deltas` 是对怀疑度的调整，写入前由规则封顶。"""

    model_config = ConfigDict(extra='forbid')

    thought: str = ''
    deltas: dict[Seat, float] = Field(default_factory=dict)
    focus: Seat | None = None
    source: Literal['rules', 'jev', 'claude'] = 'rules'
