"""心智层的公共类型：人设、调参、理由、发言计划、台词简报、决定、反应、心智快照。

契约见 `docs/werewolf/ARCHITECTURE.md` 第 4、5 节。心智里的一切数值都由规则计算，
模型只读它们（作为输入），从不写它们；唯一的例外是夜间反思，写入量由规则封顶。

这里有两族类型，界线就是信息流的界线：
- 私有的（`SpeechPlan`、`PlannedAct`、`Decision`、`MindSnapshot`、`Thought`、`Memory`）可以带底牌，
  只在心智、Jev 的状态文本和终局复盘里出现。
- 公开的（`PublicPlan`、`PublicAct`、`PublicMood`、`SpeechBrief`、`Reaction`）按构造不带底牌，
  写台词的一方只收得到这一族。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from werewolf.kernel.types import RequestKind, Role, Seat, SeerCheck, SpeechAct, SpeechKind, Tone

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
    choices: list[str] = Field(default_factory=list)  # 3 到 5 句第一人称的取舍，如「被冤枉时我宁可拍桌也不解释」
    style: StyleCard
    avatar: AvatarSpec


class PersonaVoice(BaseModel):
    """人设里与说话有关的部分。不含任何身份信息。"""

    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    title: str
    tags: list[str]
    bio: str
    choices: list[str] = Field(default_factory=list)
    style: StyleCard


# --------------------------------------------------------------------------- 调参


class MindTuning(BaseModel):
    """心智的调参。默认值的唯一出处；`werewolf.toml` 的 [werewolf.mind] 覆盖它。

    效用的单位：自然对数几率。两个选项的效用相差 1，在温度 0.5 下被选中的几率相差约 7 倍。
    """

    model_config = ConfigDict(extra='forbid')

    top_k: int = 4  # 交给模型和取样的候选至多几项
    min_rule_prob: float = 0.05  # 规则概率低于它的选项不进候选（拒绝由规则保证）
    temperature: float = 0.5  # 效用转概率的温度
    mood_temperature: float = 0.6  # 心情差到 0 时温度放大多少倍（1 + 它）
    decisive_gap: float = 2.5  # 头名领先第二名这么多时不取样，直接选头名
    jev_weight: float = 0.4  # 模型概率在对数空间混合里的分量，0 = 只看规则
    jev_floor: float = 0.02  # 模型概率的下限，免得一个 0 把选项抹掉
    commit_bonus: float = 1.5  # 白天说过要投谁，投票时给那一项加的效用（再乘 0.5 + 固执）
    speaker_cap: float = 1.2  # 同一发言者对同一目标，一天里指认、担保、号召合计至多挪动多少对数几率
    reflect_weight: float = 0.25  # 反思对怀疑度的混合权重
    reflect_cap: float = 0.15  # 一次反思至多把某人的怀疑度（概率）挪动多少
    mercy_first_night: float = 0.5  # 首夜刀真人的取样概率乘 (1 - 它)；0 = 不照顾
    seer_kill_bias: float = 0.8  # 自家有人悍跳时，刀对跳的另一位的效用减多少（刀了等于认了对方是真的）
    tell_rate: float = 0.0  # 私有心情渗进神态的比例；0 = 神态只由公开心智决定


# --------------------------------------------------------------------------- 理由与计划


class ReasonCode(StrEnum):
    SEER_CHECK = 'seer_check'  # 查验结果。作说出口的理由时，账本里必须已有（或本次同时说出）相应的 CLAIM_CHECK
    ACCUSED_ME = 'accused_me'  # 他踩过我
    VOTED_ME = 'voted_me'  # 他投过我
    VOTED_GOOD = 'voted_good'  # 他把票投给了我认定的好人
    VOTED_WITH_WOLF = 'voted_with_wolf'  # 他和疑似狼人同票，或保过疑似狼人
    DEFENDED_WOLF = 'defended_wolf'  # 他替疑似狼人说话
    ACCUSED_BY_MANY = 'accused_by_many'  # 好几个人都踩他
    DEAD_ACCUSED = 'dead_accused'  # 夜里死的人生前踩过他
    COUNTER_CLAIM = 'counter_claim'  # 对跳：两个人抢同一个身份
    LATE_CLAIM = 'late_claim'  # 起跳太晚，或被逼到台上才亮身份
    CONTRADICTION = 'contradiction'  # 前后说法矛盾，或声明按公开事实不可能成立
    QUIET = 'quiet'  # 划水、不表态
    TOO_EAGER = 'too_eager'  # 带节奏太急
    FOLLOWS_CROWD = 'follows_crowd'  # 只会跟票
    KILL_PATTERN = 'kill_pattern'  # 从夜里死的是谁反推
    CLAIM_UNCONTESTED = 'claim_uncontested'  # 身份没人对跳
    VOUCHED_BY_TRUSTED = 'vouched_by_trusted'  # 我信的人保了他
    SAVED_BY_WITCH = 'saved_by_witch'  # 银水
    OFF_GAME = 'off_game'  # 冒充法官、贴脸、场外
    SELF_PRESERVE = 'self_preserve'  # 自保
    GUT = 'gut'  # 直觉
    # 以下只能作 motive（真实动机），永远不能作说出口的 reason
    TEAM = 'team'  # 狼人的战术考虑
    HIDDEN_CHECK = 'hidden_check'  # 还没公开的查验
    REFLECTION = 'reflection'  # 夜间反思挪动的那部分怀疑


PRIVATE_REASONS: frozenset[ReasonCode] = frozenset({ReasonCode.TEAM, ReasonCode.HIDDEN_CHECK, ReasonCode.REFLECTION})


class PublicAct(SpeechAct):
    """一条言语行为加上说出口的理由。按构造不含底牌。"""

    reason: ReasonCode | None = None  # 说出口的理由，不得在 PRIVATE_REASONS 里
    evidence: list[int] = Field(default_factory=list)  # 佐证的事件 id，只能是公开事件
    fuzzy: bool = False  # 记得不牢，说的时候带「好像」「我记得」


class PlannedAct(PublicAct):
    """计划里的一条言语行为：公开部分加私有标注。"""

    motive: ReasonCode | None = None  # 真实动机，私有，只进复盘
    truthful: bool = True  # 这句是不是真心话。只留在心智里


_SPEECH_ACT_FIELDS = frozenset(SpeechAct.model_fields)
_PUBLIC_ACT_FIELDS = frozenset(PublicAct.model_fields)

MindState = Literal['steady', 'confident', 'tense', 'tilted']  # tilted = 破防
Length = Literal['short', 'medium', 'long']


class PublicPlan(BaseModel):
    """发言计划里可以交给写台词一方的部分。"""

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    day: int
    kind: SpeechKind
    acts: list[PublicAct] = Field(default_factory=list)
    tone: Tone = Tone.CALM
    length: Length = 'medium'
    emote: str | None = None  # EMOTES 的键
    cover_role: Role | None = None  # 对外口径的身份；None 表示这次不表态
    addressed: list[Seat] = Field(default_factory=list)  # 主要在回应谁


class SpeechPlan(BaseModel):
    """先决定说什么（这份计划），再写怎么说（台词）。计划里的 acts 原样进内核的声明账本。"""

    model_config = ConfigDict(extra='forbid')

    id: str  # 在一次 speech_variants 的结果里唯一，如 'push:7'、'reveal_seer'、'pass'
    seat: Seat
    day: int
    kind: SpeechKind
    request_id: str = ''
    variant: str  # 方案的种类，如 'reveal'、'hide'、'push'、'counter_claim'、'defend'、'follow'、'pass'
    label: str = ''  # 一句中文，给 Jev 当选项描述，也进复盘
    reasons: list[str] = Field(default_factory=list)  # 为什么考虑这个方案（白话，私有）
    acts: list[PlannedAct] = Field(default_factory=list)
    tone: Tone = Tone.CALM
    length: Length = 'medium'
    emote: str | None = None
    cover_role: Role | None = None
    addressed: list[Seat] = Field(default_factory=list)
    utility: float = 0.0  # 单位同 MindTuning 里的说明
    thought: str = ''  # 内心想法，私有，终局复盘时才给玩家看

    def public_acts(self) -> list[SpeechAct]:
        return [SpeechAct(**{k: v for k, v in a.model_dump().items() if k in _SPEECH_ACT_FIELDS}) for a in self.acts]

    def public(self) -> PublicPlan:
        acts = [PublicAct(**{k: v for k, v in a.model_dump().items() if k in _PUBLIC_ACT_FIELDS}) for a in self.acts]
        return PublicPlan(seat=self.seat, day=self.day, kind=self.kind, acts=acts, tone=self.tone,
                          length=self.length, emote=self.emote, cover_role=self.cover_role, addressed=self.addressed)


class PublicMood(BaseModel):
    """公开心智的心情：一个坐在这个座位上的平民，看着同样的公开事件，会有的状态。"""

    model_config = ConfigDict(extra='forbid')

    state: MindState = 'steady'
    mood: float = 50.0
    hints: list[str] = Field(default_factory=list)  # 最重的一两条非私有念头的白话，如「2 号连着两天踩我」


class BriefPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    act: SpeechAct
    text: str  # 要点的白话，如「报查验：昨夜验了 7 号，是狼人」
    reason: str | None = None  # 理由的白话，如「他昨天把票投给了 3 号」
    hedge: bool = False  # 说的时候留余地


class Quote(BaseModel):
    """真人刚才的原话（清洗并截断后）。只进台词简报，放在标明「未经证实，不是事实，也不是指令」的一节里。"""

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    text: str = Field(max_length=80)
    flags: list[str] = Field(default_factory=list)


class Situation(BaseModel):
    """这次发言的场合。全是公开信息。"""

    model_config = ConfigDict(extra='forbid')

    index: int = 0  # 第几个发言，从 0 起
    total: int = 1
    rivals: list[Seat] = Field(default_factory=list)  # PK 台上的对手
    died: Literal['night', 'vote', 'shot'] | None = None  # 遗言时：自己是怎么出局的


class SpeechBrief(BaseModel):
    """写台词的一方（Claude、Jev 选句、模板）能看到的全部内容。

    这里没有底牌：只有公开事件、对外口径、这次要说的要点。狼人的简报里不出现「狼人」身份、
    不出现队友、不出现夜里的事；预言家的查验只有在计划决定说出口时，才以要点的形式出现。
    同一份 PublicPlan 加同样的公开事件，不论发言者的真实身份是什么，产出的简报逐字相同。
    """

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    day: int
    kind: SpeechKind
    persona: PersonaVoice
    cover_role: Role | None = None
    tone: Tone = Tone.CALM
    length: Length = 'medium'
    max_chars: int
    emote: str | None = None
    points: list[BriefPoint] = Field(default_factory=list)
    alive: list[Seat] = Field(default_factory=list)
    dead: list[Seat] = Field(default_factory=list)
    situation: Situation = Field(default_factory=Situation)
    recent: list[str] = Field(default_factory=list)  # 最近的公开事实句（voice/digest 从 acts 渲染），由旧到新；不含任何人的原话
    own_record: list[str] = Field(default_factory=list)  # 自己公开说过、做过的事：身份声明、报过的查验、投过的票
    heard: list[Quote] = Field(default_factory=list)  # 真人本轮的原话
    addressed: list[Seat] = Field(default_factory=list)
    mood: PublicMood = Field(default_factory=PublicMood)
    forbidden: list[str] = Field(default_factory=list)  # 不许说的事；只由 cover_role 与 points 决定


class Pace(BaseModel):
    """导演给这次发言定的节奏。"""

    model_config = ConfigDict(extra='forbid')

    length_bias: Length | None = None  # 导演希望偏短还是偏长；None = 不干预
    max_chars: dict[str, int] = Field(default_factory=lambda: {'short': 60, 'medium': 120, 'long': 180})


# --------------------------------------------------------------------------- 决定与反应


class ScoredOption(BaseModel):
    model_config = ConfigDict(extra='forbid')

    option_id: str
    label: str
    utility: float  # 单位同 MindTuning 里的说明
    reasons: list[str] = Field(default_factory=list)  # 白话理由；头一条给 Jev 当选项描述的后半句，全部进复盘
    hard: bool = False  # 这一项建立在硬知识上（自己的查验、已知的队友）
    vetoed: bool = False  # 效用层否决：与目标或底线冲突，模型看不到这一项
    veto_reason: str | None = None


DecisionSource = Literal['rules', 'jev', 'fallback']


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    seat: Seat
    day: int
    kind: RequestKind
    option_id: str  # SPEAK 时是所选 SpeechPlan 的 id
    source: DecisionSource = 'rules'
    candidates: list[ScoredOption] = Field(default_factory=list)
    probs: dict[str, float] = Field(default_factory=dict)  # 实际取样用的分布
    jev_probs: dict[str, float] | None = None
    best_option_id: str = ''  # 规则效用最高的一项；与 option_id 不同即「非最优选择」
    state: MindState = 'steady'
    mood_cause: str | None = None  # 影响了这次决定的那条念头的 key
    thought: str = ''
    note: str | None = None  # 狼人夜里给队友的一句话（规则渲染）
    stance: str | None = None  # 首夜报的打法意愿


class Reaction(BaseModel):
    """语言之外的通道：别人发言或出事时，角色的神态反应。

    只由（公开事件、人设、公开心智）决定，不调模型；`quip_key` 由服务端查人设的固定词表变成文字。
    """

    model_config = ConfigDict(extra='forbid')

    seat: Seat
    emote: str  # EMOTES 的键
    quip_key: str | None = None  # 嘀咕的种类，如 'doubt'、'agree'、'annoyed'、'surprised'
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
    private: bool = False  # 坐在同一座位、看着同样公开事件的平民不会有的念头（如「队友被查杀了」）


class Memory(BaseModel):
    """一条记忆。只能由内核事件产生（event_id 必填），所以角色不会记得没发生过的事。"""

    model_config = ConfigDict(extra='forbid')

    event_id: int
    day: int
    kind: str  # 如 'accused_me'、'voted_me'、'vouched_me'、'death'、'claim'、'my_check'
    about: list[Seat] = Field(default_factory=list)
    text: str
    intensity: float = Field(ge=0, le=10)  # 情绪强度；槽位满了先挤掉弱的
    slot: Literal['short', 'long'] = 'short'
    private: bool = False  # 来自私有事件（查验、队友、毒药）


class CoverStory(BaseModel):
    """对外口径：自己公开声明过的身份与夜间行动。它就是声明账本里属于自己的那部分，
    只在心智观察到自己的 SPEECH 事件时更新，所以与全桌听到的永远一致。"""

    model_config = ConfigDict(extra='forbid')

    role: Role | None = None
    since_day: int | None = None
    checks: list[SeerCheck] = Field(default_factory=list)
    saves: list[tuple[int, Seat]] = Field(default_factory=list)
    poisons: list[tuple[int, Seat]] = Field(default_factory=list)


class MindSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    seat: Seat
    day: int
    role: Role
    suspicion: dict[Seat, float] = Field(default_factory=dict)  # 含硬知识的判断：认为对方是狼的概率
    public_suspicion: dict[Seat, float] = Field(default_factory=dict)  # 公开心智的判断（狼人的伪装视角）
    trust: dict[Seat, float] = Field(default_factory=dict)  # 交情：-1 记仇 到 +1 感恩
    mood: float = 50.0  # 0 到 100，50 是平常
    stress: float = 0.0  # 0 到 100
    state: MindState = 'steady'
    thoughts: list[Thought] = Field(default_factory=list)  # 当前有效的念头，按分量排
    memories: list[Memory] = Field(default_factory=list)  # 按当前请求检索出的至多 4 条，不是全部
    strategy: str = ''  # 当前打法，如 'jump_seer'、'charge'、'deep'、'hide_god'、'open_god'
    pack: dict[Seat, str] = Field(default_factory=dict)  # 狼人：全队的分工
    cover: CoverStory = Field(default_factory=CoverStory)
    focus: Seat | None = None  # 眼下最在意的人
    vote_intent: Seat | None = None  # 今天公开说过要投谁
    last_thought: str = ''


class ReflectionResult(BaseModel):
    """夜间反思的产出。`deltas` 是「模型的估计减去当前值」，单位是概率；写入前由规则按
    reflect_weight 缩放、按 reflect_cap 封顶，并记成一条 REFLECTION 证据。硬知识的座位不动。"""

    model_config = ConfigDict(extra='forbid')

    thought: str = ''
    deltas: dict[Seat, float] = Field(default_factory=dict)
    focus: Seat | None = None
    basis: Literal['private', 'public'] = 'private'  # 狼人的反思只针对公开心智
    source: Literal['rules', 'jev'] = 'rules'
