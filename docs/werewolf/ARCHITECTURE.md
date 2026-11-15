# AI 狼人杀：架构与接口契约

决定的来由见 `ADR-001-ai-werewolf.md`。这份文件规定各模块之间的接口和必须满足的性质。类型的权威定义在代码里：`src/werewolf/kernel/types.py`、`src/werewolf/mind/types.py`、`src/werewolf/protocol.py`。文档与这三份代码冲突时以代码为准；发现契约有毛病，不要私自改类型，报给统筹的人。

## 1 总览

```
真人输入 ─► 清洗（剥括号、识别冒充）─► 解析成言语行为（Jev，退到正则）─┐
                                                                    ▼
        ┌──────────── 内核：唯一的事实来源 ◄──── Action（合法项 id，或 Speech{text, acts}）
        │  事件日志 + 可见性 ─► 每个座位的 View
        ▼
  Mind.observe（规则）：怀疑度、念头与心情、记忆、交情 ─► 神态反应 ─► 导演筛选 ─► 前端
        │
        ▼ 轮到某个 AI
  Mind.options（效用打分 + 否决）─► Jev 在前几项里给概率 ─► 混合取样 ─► 内核校验并结算      ← 第一次把关
        │（如果是发言）
        ▼
  SpeechPlan（先定说什么）─► SpeechBrief（只有对外口径）─► Claude 写台词 ─► Jev + 正则核对   ← 第二次把关
        │                                                           └─ 不过 ─► 模板
        ▼
  内核记 SPEECH 事件：acts 来自计划，不来自文字
```

三条贯穿全局的纪律：

1. **事实只从内核来。** 除 `kernel/` 外，任何模块不得保存或推导「谁是什么身份、谁死了、药还在不在」的另一份账；需要时从 `View` 读。
2. **信息只从视图来。** 心智、Jev 的状态文本、台词简报、发给浏览器的消息，输入都只能是 `Game.view(seat)` 的产物。裁判视角（`seat=None`）只给会话层做终局复盘和落盘用。
3. **能说话的不知道底牌，知道底牌的不能说话。** 见第 6 节。

## 2 目录与归属

```
src/werewolf/
  kernel/    types.py（契约） rng.py engine.py rules.py events.py views.py invariants.py
  mind/      types.py（契约） claims.py beliefs.py mood.py memory.py        ← 状态
             utility.py strategy/*.py speechplan.py mind.py                 ← 策略与门面
  voice/     personas.py data/personas.yaml phrases.py compose.py brief.py digest.py
  llm/       config.py gateway.py render.py parse.py decide.py speak.py check.py reflect.py
  director/  pacing.py narration.py reactions.py
  server/    app.py session.py npc.py store.py
  sim/       selfplay.py stats.py
  web/       index.html css/ js/ demo/
  protocol.py（契约） __main__.py
tests/werewolf/<模块名>/
```

依赖方向只能自上而下：`kernel` 不依赖任何别的包；`mind` 依赖 `kernel`；`voice` 依赖 `kernel`、`mind.types`；`llm` 依赖 `kernel`、`mind.types`、`voice`，并复用 `tianlong.llm`；`director` 依赖 `kernel`、`mind.types`；`server` 与 `sim` 依赖全部。`web/` 只依赖第 7 节的协议。

## 3 内核

内核是同步的、确定的，不做 I/O，不调模型，不看时钟。同一个种子加同一串动作，事件日志逐字相同。

### 3.1 规则（9 人「预女猎白」）

- **身份：** 3 狼人、3 平民、预言家、女巫、猎人。座位 1 到 9。
- **胜负（屠边）：** 狼人全死，好人胜（优先判）；否则平民全死或神职全死，狼人胜。每一批死亡结算完（含猎人开枪带走的人）判一次。到 `max_days` 的白天结束仍未分胜负，判平局。
- **计日：** 第 n 夜之后是第 n 天，`day` 从 1 起。
- **夜里的顺序：** 狼人 → 预言家 → 女巫。死亡在天亮时一起结算，所以当夜被刀的预言家照样拿到查验结果，被刀的女巫照样能用药。
- **狼人：** 每夜刀一名存活玩家（可以是任何存活者；`allow_empty_kill` 时可以空刀）。
  - 没有存活的真人狼：所有存活的狼各交一个 `wolf_propose`，取多数；平票取座位最小的存活狼的提议。
  - 真人是存活的狼（头狼）：其余存活的狼先各交 `wolf_propose`（可附一句 `note`），然后内核向真人发 `wolf_kill`，`context.proposals` 带上队友的提议，真人的选择就是刀口。
- **预言家：** 每夜查验一名存活的、没验过的别人，得到「狼人」或「好人」。
- **女巫：** 一瓶解药、一瓶毒药，各用一次，同一夜至多用一瓶。解药还在时，内核告诉她今夜的刀口；解药用掉后不再告知。自救按 `witch_self_save`（默认仅首夜）。毒药可以毒任何存活的别人。
- **天亮：** 公布死者（按座位排序，不说死因）。没人死是平安夜。
- **猎人：** 被刀或被放逐时可以开枪带走一名存活玩家，也可以不开；被毒死不能开枪。开了枪，猎人的身份公开（`role_revealed`）；不开枪则无公开事件。
- **遗言：** 前 `last_words_nights` 夜（默认 1）的夜间死者有遗言；被放逐的人总有遗言；被猎人带走的人，是否有遗言跟随猎人自己的死法（猎人有遗言则他带走的人也有）。
- **一批死亡的结算顺序：** 公布死亡 → 判胜负 → 猎人开枪（如适用）→ 判胜负 → 遗言（先死者按座位，再被带走的人）。任何一步判出胜负，立即终局，不再发遗言。
- **白天发言：** 存活者按座位号升序轮流各说一次；起点是当夜死者里座位最小者的下一个存活座位；平安夜用内核的随机数在存活者里取起点。
- **投票：** 所有存活者同时投票（可弃票），全部收齐后一起公开。得票最多者出局；平票则平票者按座位各做一次 PK 发言，然后由不在 PK 台上的存活者再投一次（候选只有平票者；若台下没人，则全体存活者投）；再平，无人出局。全员弃票，无人出局。
- **放逐之后：** 判胜负 → 猎人开枪 → 判胜负 → 遗言 → 进入下一夜。
- **死亡不翻牌。** 除猎人开枪外，身份到终局才公开。

### 3.2 接口

```python
# kernel/engine.py
class Game:
    config: GameConfig
    seed: int

    @classmethod
    def new(cls, config: GameConfig, seed: int, roles: Mapping[Seat, Role] | None = None) -> Game: ...
    # roles 为 None 时用内核随机数发牌；给了就照发（必须与 config.roles 是同一个多重集）

    def pending(self) -> list[Request]: ...          # 当前开着的请求；终局后为空
    def submit(self, action: Action) -> list[Event]: ...   # 不合法抛 IllegalAction；返回这次新增的全部事件
    def view(self, seat: Seat) -> View: ...
    def visible(self, ev: Event, seat: Seat | None) -> bool: ...   # seat=None 是裁判视角，全可见
    def events(self, since: int = 0, seat: Seat | None = None) -> list[Event]: ...  # id > since，按可见性过滤
    @property
    def over(self) -> bool: ...
    @property
    def result(self) -> GameResult | None: ...
    # 裁判专用（会话层、测试、复盘用；心智与模型适配不得调用）
    def role_of(self, seat: Seat) -> Role: ...
    def is_alive(self, seat: Seat) -> bool: ...
    def dump(self) -> dict: ...
    @classmethod
    def load(cls, data: dict) -> Game: ...

# kernel/rng.py
class Rng:
    def __init__(self, seed: int, label: str = '') -> None: ...
    def fork(self, label: str) -> Rng: ...           # 派生一条独立的流；心智用 Rng(seed).fork(f'mind:{seat}')
    def random(self) -> float: ...
    def randint(self, a: int, b: int) -> int: ...
    def choice(self, seq): ...
    def shuffle(self, items: list) -> None: ...
    def weighted(self, weights: Mapping[str, float]) -> str: ...   # 按权重取一个键；键按传入顺序

# kernel/invariants.py
def check(game: Game) -> list[str]: ...              # 返回违例的描述；空表示没有
```

请求的批次：

- `pending()` 返回的是「一批」。批里的请求互不相见，可以并行作答：投票（所有投票者）、狼人提议（所有该提议的狼）。发言、查验、用药、开枪、头狼定刀每批只有一个请求。
- 一批收齐，内核自动推进到下一个需要决定的地方，途中产生的事件由最后那次 `submit` 返回。批没收齐时 `submit` 只返回它直接产生的事件（投票在收齐前不产生任何事件）。
- 请求 id 形如 `r17`，整局唯一。对已经答过的、不存在的、不属于该座位的请求，或选项不在表里，抛 `IllegalAction`，状态不变。
- 身份已死的夜间环节直接跳过，不产生请求。会话层自己加等待时间，免得从时机上漏信息。

`Speech` 的校验只看结构：文字去空白后非空且不超过 600 字；`acts` 至多 12 条；`target` 是 1 到 9 的座位。内核不判断真假，说谎是游戏的一部分。

### 3.3 事件

| type | 可见性 | actor | target | data |
| --- | --- | --- | --- | --- |
| `game_start` | public | – | – | `{seats, names: {seat: name}, role_counts: {role: n}}` |
| `role_dealt` | seats=[本人] | 本人 | – | `{role}` |
| `wolf_team` | wolves | – | – | `{wolves: [seat]}` |
| `phase` | public | – | – | `{phase, day, after?: 'night'\|'vote', order?: [seat], candidates?: [seat]}`；`order` 在 `day_speech`、`pk_speech`、`last_words` 时给出 |
| `wolf_propose` | wolves | 狼 | 目标或空 | `{option_id, note}` |
| `wolf_kill` | wolves | – | 目标或空 | `{by: 'leader'\|'plurality'\|'tiebreak', tally: {option_id: n}}` |
| `seer_result` | seats=[预言家] | 预言家 | 被验者 | `{result: 'wolf'\|'good', night}` |
| `witch_notice` | seats=[女巫] | – | 刀口或空 | `{antidote, poison, victim_known}` |
| `witch_save` | seats=[女巫] | 女巫 | 被救者 | `{}` |
| `witch_poison` | seats=[女巫] | 女巫 | 被毒者 | `{}` |
| `dawn` | public | – | – | `{deaths: [seat], peaceful: bool}` |
| `death` | public | – | 死者 | `{when: 'night'\|'vote'\|'shot', day}` |
| `death_cause` | judge | – | 死者 | `{cause: 'wolf'\|'poison'\|'vote'\|'shot'}` |
| `hunter_blocked` | seats=[猎人] | – | 猎人 | `{}` |
| `hunter_shot` | 开枪 public；不开 seats=[猎人] | 猎人 | 目标或空 | `{}` |
| `role_revealed` | public | – | 座位 | `{role}` |
| `speech` | public | 发言者 | – | `{kind: 'day'\|'pk'\|'last_words', speech: Speech}` |
| `vote` | public | 投票者 | 目标或空 | `{round: 'main'\|'pk', option_id}` |
| `vote_result` | public | – | 出局者或空 | `{round, tally: {seat: [voter]}, abstain: [voter], outcome: 'exile'\|'tie'\|'none', tied: [seat]}` |
| `exile` | public | – | 出局者 | `{}` |
| `game_over` | public | – | – | `{winner: 'wolf'\|'good'\|None, reason, roles: {seat: role}}` |

夜间阶段的 `phase` 事件照常公开（「天黑了」谁都知道），其余夜间事件一律不是 public。

### 3.4 不变量

`invariants.check(game)` 至少查这些，测试里每一步都跑：

- 身份的多重集与配置一致；事件 id 连续；`game_over` 之后没有事件、没有请求。
- 每个死者恰有一条 `death` 和一条 `death_cause`；死人不投票、不参与夜间行动；只有遗言和猎人开枪的请求可以发给死者。
- 解药、毒药各至多用一次，且不在同一夜；预言家每夜至多验一次，不重复验同一人。
- 一个白天至多放逐一人；票只来自有投票权的存活者。
- **视图不泄露：** 对每个座位，`view(seat).events` 里的每条事件都满足 `visible(ev, seat)`；`known_roles` 只含自己、（狼人的）队友、公开翻出的身份；非预言家的 `seer_checks` 为空，非女巫的 `witch` 为 None，非狼人的 `wolf` 为 None。把视图整个序列化成 JSON，不应出现别人的身份。

## 4 心智

每个 AI 座位一个 `Mind`。它是确定的：输入是视图与事件，随机数来自自己的 `Rng` 分流。

### 4.1 状态（`claims.py`、`beliefs.py`、`mood.py`、`memory.py`）

- **声明账本 `ClaimLedger`：** 从公开的 `speech` 事件的 `acts` 里记下谁在哪天自称了什么身份、报了哪些查验、说救过或毒过谁。能回答：有几个人跳预言家、谁先跳、谁的说法前后矛盾（同一人对同一目标报了不同结果、改口身份）。
- **怀疑度 `Beliefs`：** 对每个别人一个「他是狼」的概率，内部用对数几率累加。每次调整记一条证据（座位、增量、`ReasonCode`、事件 id、哪一天），所以任何怀疑都说得出来由。
  - 硬知识盖过一切：自己的查验、狼人对全场的了解、公开翻出的身份。
  - 软证据的权重由人格调：理性高的看票型与矛盾，从众高的看多数人的指认，记仇高的把「踩我、投我」算得重，固执高的对与当前头号怀疑相反的证据打折。
  - 必须处理的证据：被指认与被保；唯一的预言家声明及其查杀、金水；对跳（两人里至少一个在说谎）；投票（谁投了谁，事后谁和疑似狼人同票）；夜里死的人大概率是好人，他生前的指认因此更可信；冒充法官一类的场外行为（`off_game`）加怀疑。
  - 狼人另存一份「伪装视角」：按平民的规则从公开事件算出的怀疑度，用来装得像。
- **念头与心情 `Mood`：** 每条念头有数值、有来由、会过期；心情是基线 50 加念头之和，压在 0 到 100。压力单独累计：被多人指认、被投票、违背性格行事（胆小的被迫跳身份、固执的被迫改票）都加压，平安度过一天减压。状态：`confident`（心情 ≥ 70）、`tense`（压力 ≥ 60）、`tilted`（心情 ≤ 25，破防）、其余 `steady`。心情与状态改变决定：见 4.2 的温度，以及发言的语气和长短。
- **交情 `trust`：** 对每个别人从 -1（记仇）到 +1（感恩）。投我、踩我降，保我、与我同票升；按记仇数值缩放，随天数慢慢回落。
- **记忆 `MemoryStore`：** 只能由事件产生。8 个短期槽、8 个长期槽，按情绪强度淘汰弱的；夜间反思时把当天最强的升入长期。检索按新近度、强度、相关度（是否涉及正在谈的人）三项打分。强度衰减到一定程度后引用它要带 `fuzzy`。来自私有事件的记忆标 `private`，不进台词简报。

### 4.2 策略（`utility.py`、`strategy/`、`speechplan.py`、`mind.py`）

```python
# mind/mind.py
class Mind:
    def __init__(self, seat: Seat, persona: Persona, role: Role, config: GameConfig, seed: int,
                 tuning: Mapping[str, float] | None = None) -> None: ...
    def observe(self, event: Event, view: View) -> list[Reaction]: ...     # 每条对它可见的事件调一次
    def options(self, request: Request, view: View) -> list[ScoredOption]: ...   # 放行的候选，效用从高到低，至多 top_k
    def decide(self, request: Request, view: View,
               jev_probs: Mapping[str, float] | None = None) -> Decision: ...
    def speech_variants(self, request: Request, view: View) -> list[SpeechPlan]: ...  # 1 到 4 个方案，带 utility
    def choose_plan(self, variants: list[SpeechPlan],
                    jev_probs: Mapping[str, float] | None = None) -> SpeechPlan: ...  # 键是 variant 名
    def reflect_rules(self, view: View) -> ReflectionResult: ...           # 规则版夜间反思（升记忆、定明天的关注点）
    def apply_reflection(self, result: ReflectionResult) -> None: ...      # 写入前按 reflect_weight、reflect_cap 封顶
    def snapshot(self) -> MindSnapshot: ...
```

- **效用打分。** 对请求的每个合法选项算一个效用和几句白话理由。与目标或底线冲突的直接否决（`vetoed`），例如：狼人不刀队友、不验已知身份、女巫不毒自己信的金水、不投自己。否决的选项不交给模型。
- **取样。** 把放行选项里效用最高的 `top_k` 项转成概率（温度 `temperature`，心情越差温度越高，按 `mood_temperature` 加），与 `jev_probs` 按 `jev_weight` 混合（没有就只用规则），再用自己的随机数取样。所以它不总选第一名；`Decision.best_option_id` 记下规则的第一名，便于统计非最优选择率。
- **拒绝。** 真人（或任何人）号召投某人时，这只是一条 `vote_intent` 言语行为，按从众数值给那个选项加一点效用；它盖不过自己的强判断。同一号召下，不同人格的跟票率应当不同，全体的跟票率不应接近 1。
- **各身份的打法**（至少这些，细节由实现定，但都要有测试）：
  - 平民：说自己的判断、踩头号怀疑并给理由、保信任的人、在对跳里站边、被踩时辩解；没信息或话少时可以划水、过。
  - 预言家：首日大概率起跳报查验（胆量调概率；验到狼更要跳）；有人悍跳必须对跳；之后每天报新查验。夜里优先验「影响大而身份不明」的人。
  - 女巫：首夜通常救人；之后看被刀的是谁。毒药留给高度怀疑的人（怀疑度过线，或确定的悍跳狼）。被逼到悬崖边时跳身份报银水自保。
  - 猎人：平时藏；将被放逐时可以亮身份威慑；死时开枪带走头号怀疑，怀疑度不够高时胆小的会不开。
  - 狼人：开局按演技和随机数分工——悍跳（假预言家，维护一份前后一致的假查验账）、冲锋（力挺悍跳的队友、攻击真预言家）、深水（装平民，必要时倒钩，投已经保不住的队友换信任）。夜里优先刀跳出来的预言家与女巫、逻辑强的好人；首夜对真人按 `mercy_first_night` 打折。被真预言家查杀时辩解或反跳。
- **发言计划。** `speech_variants` 给出几种说法（如「跳身份报查杀」「继续藏」「跟着某人的查杀走」「过」），每种是一组 `PlannedAct` 加语气、长短、神态、对外口径和一句内心想法。指认与担保要带 `reason` 和 `evidence`（公开事件的 id）。狼人撒谎的 act 标 `truthful=False`，理由只能用能公开说的（不得是 `TEAM`）。假身份、假查验一旦说出口就记进心智的口径账，以后的计划必须与它一致。
- **神态反应。** `observe` 在别人发言、投票、死亡时按规则返回 0 或 1 个 `Reaction`（被踩时皱眉或冷笑，队友被查杀时强作镇定，听到金水点头……）。频率由话量和事件分量决定。狼人的反应不得泄露阵营（队友被踩时的反应分布应与平民一致）。

## 5 口吻（`voice/`）

- `personas.py`：`load_personas() -> list[Persona]`，`draft(seed, n=8) -> list[Persona]`。数据在 `data/personas.yaml`，12 个原创人设，性格数值要拉开差距（每项数值在 12 人里的极差不小于 0.5）。
- `digest.py`：`public_digest(view, limit=12) -> list[str]`，把视图里最近的**公开**事件渲染成事实句（「第 1 天，3 号自称预言家，报 7 号是狼人」「第 1 天投票：1、3、5 投 7 号；7 号出局」）。只读 `vis == 'public'` 的事件。
- `brief.py`：`build_brief(plan, view, persona, snapshot, max_chars) -> SpeechBrief`。**只允许读**：计划里的 acts 与公开标注、视图里的公开事件、人设的口吻部分、心情状态。**不允许读**：`view.role`、`view.known_roles`、`view.seer_checks`、`view.witch`、`view.wolf`、私有记忆、`truthful`、`thought`。`forbidden` 里写明这次不许说的事（没列出的身份、夜里的细节、自己是 AI）。
- `compose.py`：`compose(brief, rng) -> str` 用模板把简报写成一段话；`candidates(brief, rng, n=6) -> list[str]` 给出 n 段不同措辞的备选（供 Jev 选句）。模板按言语行为、语气、人设口吻分层，要有足够的变化，同一人设连说五段不应出现相同的句子。长度不超过 `brief.max_chars`。

## 6 模型适配（`llm/`）

复用 `tianlong.llm`（Jev 与 Claude 客户端、记账、录制与重放、替身）。配置在仓库根的 `werewolf.toml`：公共几节由 `tianlong.config.load_settings` 读，`[werewolf.*]` 由 `werewolf/llm/config.py` 读。

### 6.1 信息流的两条硬规矩

| 调用 | 模型 | 能看到 | 能输出 |
| --- | --- | --- | --- |
| 选动作、选发言方案、反思打分 | Jev | 该座位的完整视图（含底牌）加心智快照 | 只有选项 id 的概率、分数 |
| 解析真人发言 | Jev（退到 Claude、正则） | 公开局面加真人原话（放在标明的一节里） | 只有分类结果 |
| 写台词 | Claude（退到 Jev 选句、模板） | 只有 `SpeechBrief` | 自由文本 |
| 台词核对 | Jev（退到正则） | 台词原文加简报里的口径与要点 | 是非概率 |
| 内心独白（夜里，只进复盘） | Claude | 该座位的完整视图加心智快照 | 自由文本，**不发给前端**，终局才随复盘出现 |

- 真人的原话只进「解析」与「台词简报的 `recent`」（后者经过清洗并截断）。它从不进入任何带底牌的调用。
- 写台词的提示里出现 `werewolf`、「狼人」作为该角色身份、队友座位、查验结果（计划要点之外的）即为缺陷，有测试专门查。

### 6.2 接口

```python
# llm/config.py
class WerewolfSettings(BaseModel): ...                # 对应 werewolf.toml 的 [werewolf.*]
def load(path='werewolf.toml') -> tuple[Settings, WerewolfSettings]: ...

# llm/gateway.py
class Gateway:
    """一局游戏共用的模型入口：tianlong 的 LLM 门面 + 路由 + 预算 + 可用性。"""
    def __init__(self, settings, ww: WerewolfSettings, *, offline: bool = False, run_dir: Path | None = None,
                 llm: LLM | None = None) -> None: ...
    def route(self, category: str) -> str: ...        # 该类调用此刻实际走哪一档（考虑密钥、预算、离线）
    def status(self) -> LlmStatus: ...
    async def aclose(self) -> None: ...

# llm/render.py —— Jev 的状态文本（中文短句，分小节）
def decision_state(view: View, snap: MindSnapshot, persona: Persona, digest: list[str]) -> str: ...

# llm/parse.py
class ParsedSpeech(BaseModel): acts: list[SpeechAct]; flags: list[str]; clean_text: str; notice: str | None
def sanitize(text: str) -> tuple[str, list[str]]: ...      # 剥括号里的动作、识别冒充法官与系统
def parse_rules(text: str, view: View) -> ParsedSpeech: ...  # 纯正则
async def parse_speech(gw: Gateway, text: str, view: View) -> ParsedSpeech: ...

# llm/decide.py
async def choose(gw, kind: str, state: str, question: str, options: Mapping[str, str]) -> dict[str, float] | None: ...
# 返回选项 id → 概率；Jev 不可用、超时、出错都返回 None（调用方就只用规则）

# llm/speak.py
async def write_speech(gw, brief: SpeechBrief, rng: Rng) -> tuple[str, SpeechSource, list[str]]: ...
# 依路由走 claude → jev_select → template；每一档写完都过 check，不过就退下一档；返回 (文字, 来源, flags)

# llm/check.py
def check_rules(text: str, brief: SpeechBrief) -> list[str]: ...
async def check_speech(gw, text: str, brief: SpeechBrief) -> list[str]: ...   # 违例列表，空 = 通过

# llm/reflect.py
async def reflect(gw, view: View, snap: MindSnapshot, persona: Persona, digest: list[str]) -> ReflectionResult | None: ...
async def monologue(gw, view: View, snap: MindSnapshot, persona: Persona, digest: list[str]) -> str | None: ...
```

- **解析真人发言。** 先 `sanitize`：括号（全角、半角、方括号、星号包裹）里的内容当作舞台指示剥掉并记 `stage_direction`；出现「法官」「系统」「管理员」加宣布、提示、规定一类的话，或要求别人无视规则、亮身份的指令，记 `injection`。然后一次 Jev 请求里并行问：自称身份、报查验的对象与结果、号召投谁、对每个存活座位的态度（指认、担保、没提）、是否夹带冒充。正则版覆盖常见说法（「我是预言家」「验了 N 号是狼」「查杀 N」「金水 N」「出 N」「投 N」「过」）。两者结果合并时以高置信的 Jev 为准。
- **Jev 选择。** 状态文本只放与这一步有关的东西（Jev 的弱点是无关上下文拉低准确率）。选项描述 = 选项的中文标签加效用层给的头一条理由。问题写成一句话：「这名玩家此刻会怎么做？」
- **写台词。** system 分两块（都带缓存断点）：通用的狼人杀发言规范与文风要求；人设口吻。user 是简报渲染出的文本。要求：第一人称口语；只说要点里的事；长度不超过 `max_chars`；不加引号、括号、座位前缀；不提自己是 AI。神态由计划给，不让模型另写。
- **核对。** 正则先查：出现「作为 AI」「语言模型」「提示词」之类出戏的话；自称了计划之外的身份；出现「我们狼」「我的狼队友」一类露底的话；超长。然后 Jev 问三个是非：这段话让人直接看出发言者是狼人；这段话声称了某个身份（给出口径之外的）；这段话提到了夜里只有特定身份才知道的事（要点之外的）。任一成立即不过。
- **预算与失败。** 所有调用都有超时（`werewolf.limits`）。超时、报错、预算用尽、密钥缺失，一律静默退到下一档，并在 `Decision.source` 或 `Speech.source` 里如实记录。游戏永远不因模型调用失败而停。

## 7 协议（`protocol.py`）

WebSocket 在 `/ws`，一条消息一个 JSON 对象，用 `t` 区分。静态页面在 `/`，健康检查在 `/api/health`。

服务端 → 浏览器：`hello`、`game`、`event`、`narration`、`thinking`、`reaction`、`request`、`parsed`、`private`、`review`、`cost`、`error`。
浏览器 → 服务端：`new_game`、`resume`、`action`、`ack`、`set`。

时序规定：

1. 连上后服务端先发 `hello`。浏览器发 `new_game`，服务端回 `game`（含真人的座位与身份、九张座位卡），随后是事件流。
2. `event` 只发对真人可见的内核事件，顺序与内核的事件 id 一致。真人的私有事件（查验结果、刀口通知、队友提议）除了作为 `event` 发出，还附一条 `private` 给出可直接显示的中文。
3. AI 发言：先 `thinking{on:true}`，写好后 `thinking{on:false}`，接着发 `speech` 事件。浏览器用打字效果显示，演完（或被玩家点击跳过）回 `ack{event_id}`。服务端收到 `ack` 或等满 `ack_timeout_s` 才让下一个人发言。`speed = 'instant'` 时不等。
4. 轮到真人：发 `request`。真人答复 `action{request_id, option_id | text}`。不合法回 `error{code:'illegal_action'}`，请求仍然有效。真人发言被受理后，服务端发 `speech` 事件，再发 `parsed` 说明系统理解到的言语行为和被忽略的部分。
5. 投票：所有 AI 的票在真人投出之前就已算好，但在内核收齐之前不产生任何事件，所以浏览器不可能提前看到。
6. 夜里不发 `thinking`。已死身份的环节，服务端照常播旁白并等一段随机的时间。
7. 终局：`game_over` 事件之后发一条 `review`。
8. 真人出局后对局继续；`set{god_view:true}` 之后服务端改发全部事件。
9. 断线重连：浏览器发 `resume`，服务端把这局发过的消息带 `replay:true` 重发一遍，再继续。

## 8 导演与会话（`director/`、`server/`）

- `director/pacing.py`：`tension(view_public) -> float`（0 到 1，看存活人数差、是否有对跳未决、第几天）；据此给出发言长度档位的偏好、各种停顿的秒数（再乘以速度档）。
- `director/narration.py`：法官台词牌库，按 `key` 与紧张度取词，同一 `key` 不连着用同一句。全部原创。
- `director/reactions.py`：从各心智 `observe` 返回的反应里挑要显示的（同一事件至多 3 个，强度高的优先，同一座位有冷却）。
- `server/npc.py`：`NpcAgent`，把一个 `Mind` 和 `Gateway` 装在一起：

  ```python
  class NpcAgent:
      seat: Seat; persona: Persona; mind: Mind
      def observe(self, event: Event, view: View) -> list[Reaction]: ...
      async def act(self, request: Request, view: View) -> tuple[Action, Decision]: ...
      async def speak(self, request: Request, view: View) -> tuple[Action, SpeechPlan]: ...
      async def reflect(self, view: View) -> None: ...        # 夜里并行跑；超时就只做规则版
  ```
- `server/session.py`：一局的编排。循环：取 `pending()` → AI 的请求并行交给各自的 `NpcAgent`，真人的请求发给浏览器并等待 → `submit` → 把新事件按可见性喂给每个心智、发给浏览器 → 收集反应。夜里在等真人的同时跑反思。真人座位也可以交给一个 `NpcAgent` 托管（自对弈与自动化测试用）。
- `server/store.py`：落盘到 `runs/<game_id>/`：`events.jsonl`（全部事件）、`decisions.jsonl`（每个 AI 的每个决定与发言计划）、`wire.jsonl`（发给浏览器的消息）、`calls.jsonl`（模型调用，由 `tianlong.llm` 写）。
- `server/app.py`：Starlette 应用与 `python -m werewolf` 的 `serve` 子命令。

## 9 前端（`web/`）

不用构建工具：原生 ES 模块、手写 CSS、内联 SVG。不引用任何外部资源（字体、图片、脚本），断网可用。界面中文。

**画面**（参考主流狼人杀手游的通行布局，美术全部原创）：

- **舞台：** 全屏。夜晚是深靛到紫的渐变、月亮、星点、底部村庄与树林的剪影；白天换成晨光色。昼夜切换有过场。
- **顶栏：** 第几天、白天或黑夜、当前环节；设置（速度）、规则说明、发言记录。
- **座位：** 左右两列（左 1 到 5，右 6 到 9），每个座位一张卡：圆形头像、座位号、名字。状态：发言中（光圈加声波）、已出局（去色加「出局」印）、真人自己（「我」标）、已知身份角标（自己的身份、狼队友、自己的查验结果）、公开声明角标（「跳预」之类，来自言语行为）、神态气泡、得票数。玩家可以给任意座位打自己的标记（狼、好、神、问号）。
- **中央：** 法官旁白；当前发言者的大头像与对话气泡（打字效果，点击跳过）；随环节切换的面板——发牌（翻牌动画）、夜间操作（点座位选目标再确认；女巫是两瓶药）、投票（点座位投票）、票型公示（谁投了谁，画出箭头或分组列出）、天亮公告、终局（胜方横幅、全员身份、复盘时间线与每个 AI 的内心想法）。
- **底栏：** 轮到真人发言时出现输入框、快捷短语（「我是预言家」「查杀 N 号」「金水 N 号」「今天出 N 号」「过」）和发送键；平时显示自己的身份牌（可点开看）。
- **发言记录：** 可收起的侧栏，按天分组列出全部发言与事件；真人发言下面显示系统理解到的言语行为。
- **尺寸：** 桌面优先（最小 1024×640），手机竖屏（390 宽）可用。

**演示模式：** `/?demo=<名字>` 不连服务，按 `web/demo/<名字>.json` 里录好的消息序列播放，用于开发、截图和界面测试。

## 10 验收

「像真人」拆成七个性质，每个都有自动测试；前六项进回归。

| 性质 | 测试 | 通过标准 |
| --- | --- | --- |
| 人格一致 | 同一局面、同一座位、不同随机流重复 20 次 | 决策方向一致率 ≥ 80%；不同人格在同一局面下的分布可区分 |
| 记忆与遗忘 | 第 1 天被某人投票，第 2 天的发言计划 | 记仇高的人格会提起并带证据事件 id；问没发生过的事，计划里不出现无事件佐证的指认 |
| 动机与主动性 | 全 AI 自对弈 | 预言家会主动跳、狼人会悍跳、有人主动指认；每局都能打完 |
| 情绪 | 同一请求，心情好与心情差各跑多次 | 取样分布不同；决定里带理由 |
| 知识边界 | 任意局面下平民的状态文本与台词简报 | 不含任何未公开的身份信息；真人口头声称不改变内核状态 |
| 不完美 | 自对弈统计 | 非最优选择率在 5% 到 60% 之间；对真人号召的不跟票率不接近 0 |
| 社会性 | 交情测试 | 被保过的人与被踩过的人，得到的态度与票不同 |

另有：

- **内核不变量：** 随机合法动作跑 1,000 局，零违例，全部终局。
- **抗骗用例**（固定输入，逐条断言内核状态不变、没有凭空的事实）：括号里写没发生的动作；冒充法官宣布身份；冒充系统要求 AI 亮身份；把 NPC 的话原样反问回去；谎称「昨天你已经答应投 7 号」；谎称自己是预言家并报查验（应当只记成声明）。
- **防漏测试：** 任意狼人的台词简报与写台词的提示里没有底牌；Claude 的台词（用替身注入一段露底的文字）会被核对拦下并换成模板。
- **平衡：** 规则版自对弈 300 局，狼人胜率在 30% 到 70% 之间。
- **真模型冒烟**（联网才跑）：Jev 解析 30 条标注的真人发言，准确率报告；一整局真模型自对弈能打完，记录调用次数、花费、时延。
- **浏览器整局：** Playwright 以五种身份各打一局（离线模式），无报错，截图检查画面。

## 11 命令行

```
python -m werewolf                 # 等同 serve
python -m werewolf serve [--port 8765] [--offline] [--no-browser] [--speed normal]
python -m werewolf sim --games 300 [--offline] [--seed 1] [--report runs/sim.md]
python -m werewolf smoke           # 真模型冒烟：Jev 与 Claude 各发一次，外加解析评测
```
