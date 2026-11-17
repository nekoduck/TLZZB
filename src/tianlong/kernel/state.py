"""世界状态的全部类型（方案 5.1）。字段名照抄，只增不减；新增字段一律带默认值。

整个 ``WorldState`` 能序列化成 JSON，这就是存档。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

Tick = int  # 1 刻。8 刻 = 1 时辰，96 刻 = 1 日
CharId = str
LocId = str
ItemId = str
FactId = str
ArtId = str

TICKS_PER_HOUR = 8
HOURS_PER_DAY = 12
TICKS_PER_DAY = TICKS_PER_HOUR * HOURS_PER_DAY


@dataclass
class Ctx:
    """谓词与效果的求值上下文（ARCHITECTURE 2.1）。

    ``self`` 指 ``self_id``，``player`` 指 ``state.player``，``$名字`` 指 ``roles[名字]``；
    以 ``.`` 开头的旗标属于 ``situation`` 这一场情境。
    """

    self_id: CharId | None = None
    roles: dict[str, CharId] = field(default_factory=dict)
    situation: str | None = None

    def with_roles(self, **extra: CharId) -> Ctx:
        roles = dict(self.roles)
        roles.update(extra)
        return Ctx(self.self_id, roles, self.situation)


class Exit(BaseModel):
    to: LocId
    cost: Tick
    pre: dict | None = None  # 谓词，见 5.2
    hidden: bool = False  # 要先发现才能走
    check: str | None = None  # 要过的险：climb / descend / leap，见 6.4
    edge: bool = False  # 通往图外；走出去即收笔（2.6）
    # 新增
    fall_to: LocId | None = None  # 失足落到哪里；不写时 descend 落到 to，其余落回原处


class Feature(BaseModel):  # 地点里可以查看、搜索的东西
    id: str
    name: str
    desc: str = ""
    reveals: list[FactId] = []  # 查看后获知的事实
    contains: list[ItemId] = []  # 开局时藏在里面的东西（运行时以 Item.feature 为准）
    cover: int = 0  # 藏身加值，0 到 30
    pre: dict | None = None  # 看得出名堂的前提，例如识字
    # 新增
    push: list[dict] = []  # 推它时执行的效果；为空则推不动


class Location(BaseModel):
    id: LocId
    name: str
    unknown_alias: str = ""  # 玩家不知其名时的叫法
    exits: list[Exit] = []
    features: dict[str, Feature] = {}
    desc: dict[str, str] = {}  # 键为 day / dusk / night
    loud_to: list[LocId] = []  # 巨响传得到的地点
    terrain: int = 0  # 逃跑修正，-20 到 +20
    owner: str | None = None  # 属于哪一方（身份名）；外人擅入会被盘问
    # 新增
    handholds: bool = False  # 崖壁有树木藤蔓可挂：坠落的活命机会 +20
    traces: list[str] = []  # 效果 trace 留下的痕迹；look / examine 时看得到


class Relation(BaseModel):
    trust: int = 0  # 信任，-100 到 100
    affinity: int = 0  # 好感，-100 到 100
    fear: int = 0  # 惧怕，0 到 100
    regard: int = 0  # 眼里对方的斤两：最近一次亲见或听说的 power
    impression: str = ""  # 一句印象，夜间反思时更新，只给模型看


class Status(BaseModel):
    kind: str  # 见 6.9
    until: Tick | None = None
    level: int = 1
    source: CharId | None = None
    # 新增
    noted: bool = False  # 毒发前的征兆是否已经出现过


class Belief(BaseModel):
    confidence: float  # 0 到 1
    source: str  # saw / told:<char> / inferred
    tick: Tick


class MemoryItem(BaseModel):
    tick: Tick
    kind: Literal["saw", "heard", "told", "did", "reflection"]
    text: str
    entities: list[str]
    event: int | None = None
    salience: int  # 1 到 10，按事件类型查表（5.6）


class Step(BaseModel):  # 打算里的一步
    id: str
    text: str  # 一句话：要做什么
    at: LocId | None = None  # 在哪里做；人不在那里就先走过去
    do: dict  # 动作模式 {verb, …}
    pre: dict | None = None  # 前提：动手那一刻不成立，这一步作废（8.2）
    start: Tick | None = None  # 最早何时动手
    deadline: Tick | None = None  # 到这时还没做成，作废
    takes: Tick = 4  # 离场时做这一步要多久，不含赶路
    offscreen: list[dict] = []  # 离场时的结算效果；为空则按动词走通用结算（8.2）
    origin: Literal["canon", "replan", "rule"] = "canon"
    state: Literal["pending", "done", "void"] = "pending"


class Agenda(BaseModel):  # 一个人此刻的打算
    goal: str  # 一句话：想要什么
    done_when: dict | None = None  # 成了的判据
    steps: list[Step] = []
    cursor: int = 0
    progress: Tick = 0  # 当前这一步已经花了多久
    status: Literal["active", "waiting", "done"] = "active"
    # 新增
    waiting_for: str = ""  # status 为 waiting 时，写明在等什么（6.11 活性约束）
    # 离场结算给当前这一步记的账（8.2）：换了一步、换了打算就清掉
    en_route: bool = False  # 动身前的前提查过成立，人已经上路：途中不再查，到了动手时再查
    doubt: Tick | None = None  # 动身前查到前提不成立的那一刻：到下个时辰仍不成立才作废


class Character(BaseModel):
    id: CharId
    name: str
    unknown_alias: str  # 玩家不知其名时的称呼
    tier: Literal["soul", "named", "extra"]  # 有魂 / 有名 / 路人（1.3）
    base_power: int = 0  # 入局时的武功底子，0 平民 … 5 绝顶；实际 power 按 6.12 算
    neili: int = 0  # 内力，0 到 100
    arts: dict[ArtId, int] = {}  # 各门本事学到第几层（6.12）
    art_progress: dict[ArtId, Tick] = {}  # 下一层已经下了多少功夫
    literacy: int = 0  # 0 不识字，1 粗识，2 通文墨，3 饱学
    aptitude: int = 1  # 悟性，0 到 3
    luck: int = 0  # 气运，0 到 20，只加在保命的判定上（6.4）
    roles: list[str] = []  # 身份
    loc: LocId
    onstage: bool = True  # False：还在图外，或已离开地图
    superior: CharId | None = None  # 听命于谁
    statuses: list[Status] = []
    inventory: list[ItemId] = []
    rel: dict[CharId, Relation] = {}
    drives: list[str] = []  # 长久的所求；改主意时由它生出新目标（8.3）
    agenda: Agenda | None = None
    beliefs: dict[FactId, Belief] = {}
    memory: list[MemoryItem] = []
    mood: dict[str, int] = {}  # anger / fear / grief / joy / suspicion，各 0 到 100
    persona: str | None = None  # 人物卡文件名；路人为 None
    initiative: int = 0
    # 新增
    awake_since: Tick = 0  # 上次醒来的时刻（6.9 疲惫）
    last_meal: Tick = 0  # 上次吃东西的时刻（6.9 饥饿）
    following: CharId | None = None  # 正跟着谁（follow）
    routine: str | None = None  # 路人的作息表名（routines.yaml）
    last_replan: Tick | None = None  # 上次重新打算的时刻（同一人每时辰至多一次）
    reflect_mark: int = 0  # 上次夜间反思时 memory 的条数
    owes: dict[CharId, int] = {}  # 欠谁的人情（被救、被帮），请求裁决时算筹码
    comforted: dict[CharId, int] = {}  # 谁在哪个时辰安慰过他（5.5：同一人每时辰一次）


class Item(BaseModel):
    id: ItemId
    name: str
    unknown_alias: str = ""  # 认不出时的叫法，例如「一卷帛书」
    holder: CharId | None = None
    loc: LocId | None = None  # 与 holder 二选一
    feature: str | None = None  # 藏在该地点的哪件景物里
    tags: list[str] = []  # token / poison / antidote / food / mount / manual / creature …
    teaches: list[ArtId] = []  # 秘籍教什么
    props: dict = {}
    # 新增
    in_world: bool = True  # False：还没出现，或已被毁掉（spawn / remove）


class Fact(BaseModel):
    id: FactId
    text: str  # 一句话
    tags: list[str] = []
    gossip: bool = False  # 知情的路人会主动说起
    # 新增
    contradicts: list[FactId] = []  # 与哪些事实相悖（6.7「与自己确信的事相悖」）


class Claim(BaseModel):
    text: str
    ref: str | None = None  # 对上的事实或事件 id
    verdict: Literal["true", "false_self", "unsourced", "opinion"]


class Speech(BaseModel):
    act: str  # 言语行为，见 6.6
    to: list[CharId]
    stance: str = "plain"  # 语气：plain / warm / cold / angry / pleading / mocking / evasive
    fact_refs: list[FactId] = []  # 说出的事实
    request: str | None = None  # 涉及的 Request
    gist: str = ""  # 第三人称的一句要旨；角色的由内核按模板拼出
    spoken_text: str | None = None  # 只有玩家有：清洗后的原话
    claims: list[Claim] = []  # 只有玩家有
    whisper: bool = False
    # 新增
    names: list[str] = []  # 这句话里直呼其名的实体（6.8：专名要听到才知道）
    ask_action: dict | None = None  # request / plead / order：想让对方做的事 {verb, params}
    action_class: str | None = None  # 所求之事的类别（trivial / effort / costly / risky / betray）
    evidence: list[str] = []  # 出示的物件 id（强凭据看 props.evidence）
    leverage: dict[str, Any] = {}  # 筹码：{reward: n, urgent: bool, threat: bool}
    condition: dict | None = None  # bargain：开出的条件 {verb, params}
    promise_do: dict | None = None  # promise：许下要做的事（动作模式）
    deadline: Tick | None = None  # promise 的期限
    about: str | None = None  # ask：问的是哪件事或哪个人


class Event(BaseModel):
    id: int
    tick: Tick
    loc: LocId
    actor: CharId | None
    verb: str
    params: dict
    outcome: Literal["ok", "fail", "blocked"]
    fact_text: str  # 内核按模板生成的中性事实句
    speech: Speech | None = None
    witnesses: dict[CharId, Literal["saw", "heard"]]
    source: Literal["player", "mind", "agenda", "situation", "storyteller", "rule"]
    step: str | None = None  # 出自某人打算里的哪一步
    offscreen: bool = False  # 离场结算出来的
    # 新增
    kinds: list[dict] = []  # 事件的性质（5.5、5.6）：{k, who, by}
    salience: int = 1  # 当事人眼里的分量（5.6 的表）
    loud: bool = False


class Request(BaseModel):
    id: str
    frm: CharId
    to: CharId
    action: dict  # 想让对方做的事：{verb, params}
    action_class: str  # trivial / effort / costly / risky / betray
    evidence: list[str] = []
    attempts: int = 1
    score: int = 0
    verdict: Literal["never", "no", "conditional", "may"] = "no"
    status: Literal["open", "agreed", "refused", "countered", "expired"] = "open"
    condition: dict | None = None  # 还价时对方开出的条件：一个动作
    # 新增
    kind: Literal["request", "question", "plead", "order"] = "request"
    created: Tick = 0
    leverage: int = 0


class Promise(BaseModel):  # 新增：许诺（6.6）
    id: str
    frm: CharId  # 许诺的人
    to: CharId  # 被许诺的人
    text: str = ""
    do: dict | None = None  # 动作模式；许诺的人做了匹配的动作即为守诺
    deadline: Tick | None = None
    status: Literal["open", "kept", "broken"] = "open"
    request: str | None = None


class SituationState(BaseModel):  # 正在进行的情境（5.7）
    id: str
    kind: str  # 情境定义的名字
    loc: LocId
    roles: dict[str, CharId]  # 角色位 → 人
    turns: int = 0


class Thread(BaseModel):  # 玩家的牵挂（8.4）
    id: str
    kind: Literal["deadline", "promise", "lead", "event", "wish"]
    text: str
    refs: list[str] = []
    weight: int = 3  # 1 到 5
    expires: Tick | None = None
    closed: bool = False


class ChronicleEntry(BaseModel):  # 年谱的一条（8.7）
    tick: Tick
    text: str
    event: int | None = None
    known_to_player: bool = False


class Divergence(BaseModel):  # 与原著的分岔（8.6）
    mark: str
    tick: Tick
    known_to_player: bool = False
    cause_event: int | None = None


class WorldState(BaseModel):
    tick: Tick
    seed: int
    rng_counter: int = 0
    content_hash: str
    player: CharId
    locations: dict[LocId, Location]
    chars: dict[CharId, Character]
    items: dict[ItemId, Item]
    facts: dict[FactId, Fact]
    flags: dict[str, Any] = {}
    requests: dict[str, Request] = {}
    situations: dict[str, SituationState] = {}
    scheduled: list[dict] = []  # {at: Tick, effects: [...], pre: 谓词}
    scene_mode: Literal["calm", "tense"] = "calm"
    step: int = 0  # 紧张场面里的步数
    known_terms: list[str] = []  # 玩家已知的专名：人、地、功夫、物
    threads: dict[str, Thread] = {}
    chronicle: list[ChronicleEntry] = []
    divergences: list[Divergence] = []
    storyteller: dict = {}
    pending_options: list[dict] = []
    warned: list[str] = []  # 已经警示过的致命动作
    events_tail: list[Event] = []  # 最近 200 条；全量在 events.jsonl
    ended: Literal["closed", "dead", "left"] | None = None
    # 新增
    version: int = 1
    next_event_id: int = 1  # 下一条事件的 id（连续）
    turn: int = 0  # 回合数，由 game 递增
    replan_queue: list[CharId] = []  # 待重新打算的人（world/replan.py 消费）
    term_alias: dict[str, str] = {}  # 玩家对某实体的特别叫法（只知其姓时）
    visited: list[LocId] = []  # 玩家到过的地点
    examined: list[str] = []  # 玩家细看过的东西：'char:x' / 'item:x' / 'feature:loc#f'
    violations: list[str] = []  # 回滚与违规的日志（保留最近 100 条）
    promises: dict[str, Promise] = {}
    counters: dict[str, int] = {}  # 各类 id 的计数器（R、P、T、情境实例）


EVENTS_TAIL_MAX = 200
VIOLATIONS_MAX = 100


def is_alive(c: Character) -> bool:
    return not any(s.kind == "dead" for s in c.statuses)


def get_status(c: Character, kind: str) -> Status | None:
    for s in c.statuses:
        if s.kind == kind:
            return s
    return None


def has_status(c: Character, kind: str) -> bool:
    return any(s.kind == kind for s in c.statuses)


def injury_level(c: Character) -> int:
    s = get_status(c, "injured")
    return s.level if s else 0


def next_id(state: WorldState, prefix: str) -> str:
    n = state.counters.get(prefix, 0) + 1
    state.counters[prefix] = n
    return f"{prefix}{n}"


def log_violation(state: WorldState, text: str) -> None:
    state.violations.append(f"[{state.tick}] {text}")
    if len(state.violations) > VIOLATIONS_MAX:
        del state.violations[: len(state.violations) - VIOLATIONS_MAX]
