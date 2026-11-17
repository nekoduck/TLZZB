# 架构与模块契约

本文件是 `docs/PLAN.md`（实现方案，下称「方案」）的落地约定。方案是规格；本文件规定各模块之间的接口，让各模块可以分头实现而对得上。方案没写、这里也没写的，按「更简单的做法」自定，并记进 `DECISIONS.md`。

## 0. 总则（方案 1.1 的摘要，违反即为缺陷）

1. 世界里发生了什么，只由 `kernel/` 的确定性代码决定。模型输出只是提议，内核校验后才生效。
2. 模型只在合法清单里选。
3. 原著只管开局。任何代码都不得为了「回到原著」改动世界。`baseline.yaml` 只在事后对照时读取。
4. 没有受保护的人物和事件。
5. 不发明新机制。
6. 模型失败时游戏照常继续（方案 4.3 的退让表）。
7. `src/` 里不出现人名、地名、剧情。`src/` 只认谓词、效果、动词。

## 1. 目录

项目根就是仓库根（方案 10.2 里的 `tianlong/`）。

```text
pyproject.toml  config.toml  .env.example  DECISIONS.md  README.md
content/   prompts/   scripts/   evals/   tests/
src/tianlong/
  __main__.py          命令行（方案 10.4）
  config.py            读 config.toml 与 .env → Settings
  game.py              回合主循环（方案 4.2），与界面无关，测试可直接驱动
  content/loader.py    读 content/ → (Defs, WorldState)；content/validate.py：P7 的校验（`validate` 返回问题清单；`require_valid` 有问题就抛 `ContentInvalid`，开新局时调一次）
  kernel/              纯函数；不 import 本包任何其他模块；无网络、文件、时钟、全局可变状态
  llm/  parser/  minds/  world/  storyteller/  narrator/  store/  ui/  pipeline/
```

依赖方向单向：`ui → game → parser / minds / world / storyteller / narrator → kernel`；这五个模块可以 import `llm`。`kernel` 不 import `llm`，也不 import `content`。`llm` 不 import `kernel`。

## 2. kernel 的文件与职责

| 文件 | 内容 |
| --- | --- |
| `state.py` | 方案 5.1 的全部类型，字段名照抄，只增不减（新增字段一律带默认值） |
| `defs.py` | 不会变的内容定义（见第 3 节）：`Defs` 及其各部分的 Pydantic 类型 |
| `rng.py` | `roll(state) -> int`（1 到 100）；`pick_weighted(state, weights) -> key`。只用 `seed` 与 `rng_counter`，每次调用 `rng_counter += 1`；用 `hashlib.blake2b` 派生，不用 `random` 模块的全局状态 |
| `refs.py` | 实体引用的写法与渲染（第 5 节） |
| `predicates.py` | `holds(state, defs, pred, ctx) -> bool`：方案 5.2 的全部谓词 |
| `effects.py` | `apply_effects(state, defs, effects, ctx, *, source, step=None, offscreen=False) -> list[Event]`：方案 5.3 的全部效果。**这是改写状态的唯一入口** |
| `events.py` | `emit(...) -> Event`：唯一造 `Event` 的地方。分配连续 id；算见证者（`perception.witnesses`）；给见证者写记忆（`memory.write`）；按 5.5 改情绪与关系（`mood.on_event`）；放进 `events_tail`（保留最近 200 条） |
| `actions.py` | 动词表 `VERBS`（方案 6.1）；`ActionInst`；`legal_actions(state, defs, actor, *, for_model=False) -> list[ActionInst]`（方案 6.2）|
| `resolve.py` | `apply(state, defs, action, speech=None, *, source, offscreen=False, defer_train=False) -> list[Event]`：方案 6.3 的七步；不变量失败则整体回滚。`offscreen=True`：离场结算出来的，事件全部标 `offscreen`。`defer_train=True`：`study`、`practice`、`teach` 动手时不记进度，事件 `params` 里给 `deferred`、`per_hour`、`student`、`art`、`max_level`，由调用方随时间经 `train` 效果去记（只有 `game` 结算玩家的动作时这样传） |
| `bottom.py` | 人物底线（方案 5.4 的 `blocks`）的匹配：`legal_actions`、开口、请求裁决三处共用 |
| `checks.py` | 方案 6.4 的各项成功率与判定 |
| `conflict.py` | 方案 6.5 |
| `speech.py` | 方案 6.6：言语行为、`Request` 的建立与裁决；`speech_options(state, defs, actor) -> SpeechOptions` |
| `beliefs.py` | 方案 6.7：断言结论与信念更新的公式 |
| `perception.py` | 方案 6.8：`witnesses(...)`；方案 4.4：`player_view(state, defs, events) -> PlayerView`；称呼表；`referable(state) -> (id -> bool)`：玩家的角色说得出哪些人、物、地方、本事（知道名字或有叫法、看得见、见过经手过、到过），`/心愿` 的绑定与开口的选项共用这一份 |
| `memory.py` | 方案 5.6 的写入、salience 表、上限与淘汰（检索在 `minds/memory.py`） |
| `mood.py` | 方案 5.5 的表；情绪衰减；关系与 regard 的档位文字（给模型看的） |
| `growth.py` | 方案 6.12：`power(state, defs, char)`、研读与练习的进度、内力档 |
| `timeflow.py` | 方案 6.9：`advance_time(state, defs, ticks) -> list[Event]`（到点的 `scheduled`、状态到期、饥饿与疲惫、情绪衰减）；`run_due_scheduled(state, defs)`（不推进时间、只执行已到点的 `scheduled`，world 用）；时辰与日期的文字 |
| `danger.py` | 方案 6.10：`danger(state, defs) -> int`（眼前的敌意与动手；身上到期致死的状态不足一日至少 1、只剩两个时辰至少 2）；致命动作的判定；对玩家的「先警示后结算」 |
| `invariants.py` | 方案 6.11：`check(state, defs) -> list[str]`（空表即通过）；`liveness(state, defs) -> list[CharId]` |

### 2.1 几个核心约定

- **上下文 `Ctx`。** 谓词与效果在一个上下文里求值：`Ctx(self_id: CharId | None, roles: dict[str, CharId], situation: str | None)`。`self` 指 `ctx.self_id`，`player` 指 `state.player`，`$名字` 指 `ctx.roles[名字]`。参数里出现的其他字符串一律当作 id。
- **情境局部旗标。** 旗标名以 `.` 开头（如 `.slapped`）时，指当前情境自己的旗标，实际存为 `"<situation_id>.slapped"`。这样通用情境可以反复开场而互不干扰。canon 情境可以照方案的例子用全局旗标。
- **重新打算的队列。** 效果 `{replan: char}` 不在内核里重新打算，只把人加进 `state.replan_queue`（新增字段）。`world/replan.py` 消费这个队列。内核里凡是方案 8.3 列出的「大事」（亲近的人被擒、受伤、死去；自己中毒、被擒、得救；要紧的东西得失；salience 不低于 8 的记忆），也同样入队。
- **死亡。** `{kill: c}`：加 `dead` 状态，他的打算全部 `void`，与他有关的未了 Request 改 `expired`，他手里的东西留在原地（`loc` = 他所在地点），与他相关的人入 `replan_queue`。
- **对玩家的缓冲（方案 6.10）。** 一次会致玩家于死的效果（`kill` 或会致死的判定失败），若同一来源是第一次，降级为 `injured` 3 级或 `captive`，并在 `state.warned` 记 `kill:<来源>`；再犯才死。
- **紧张场面。** `scene_mode == 'tense'` 时动作耗时记 0，`state.step += 1`；`study`、`practice`、`teach`、`rest` 不可用。
- **散了的情境与请求。** 求人做的事是某场情境里的一招（`{verb: setpiece, params: {situation, name}}`）而那场情境散了：没答复的请求改 `expired`；答应了还没做的那一步（`req_<请求 id>`）作废，请求 `expired`，连带的许诺就此了结（不扣信任）。此后再「答应」也不插步。
- **一句答应加答应的那件事**不要放进同一次 `resolve.apply`：答应时内核把那件事插成对方眼下这一步，同一次结算里紧接着做它，不变量会当成「已作废步骤的事件」而整体回滚。调用方先结算话（1 刻的 `wait` 载着），再结算事（`minds.apply_decision`、`world.offscreen.reply`）。
- **时间。** 1 刻是最小单位，8 刻一个时辰，96 刻一日。`tick = 0` 是第一日子时初；辰时是一日的第五个时辰，所以开局 `tick = 32`。`timeflow.clock_text(tick)` 给出「第一日 辰时」这样的文字。

### 2.2 `ActionInst`

```python
class ActionInst(BaseModel):
    id: str                 # 'a01'…，legal_actions 每次调用时重新编号
    actor: CharId
    verb: str               # 方案 6.1 的动词，或 'setpiece'
    params: dict            # 键取：target, target2, exit, item, feature, art, ticks, hours, name, situation
    cost: int               # 刻；紧张场面里为 0
    loud: bool = False
    lethal: bool = False    # 方案 6.10：先警示后结算
    star: bool = False      # 方案 6.2 第 4 步
    tags: list[str] = []    # 例如 'agenda'、'situation'、'reply'、'intervene'
    desc: str               # 一句中性中文描述，实体用 ⟦id⟧ 引用（第 5 节）
    signature: str          # verb 与参数的规范串，用于 warned、去重、「上回合选过」
```

`legal_actions` 的排序：★ 动作在前，`wait` 第二，其余在后。`for_model=True` 时压到 12 条以内（方案 6.2 第 5 步），`wait` 永远保留。玩家的候选不压缩。

### 2.3 言语

`speech_options(state, defs, actor) -> SpeechOptions`：

```python
class SpeechOptions(BaseModel):
    acts: list[str]                  # 此刻可用的言语行为（方案 6.6 表）
    facts: list[FactId]              # 可以说的事实，至多 8 条（自己相信、不触底线，按与眼前话题的相关度）
    requests: list[RequestView]      # 冲自己来的请求：request, verdict_text, allowed_replies, counter_options
    star: dict | None                # 当前这一步打算若是 say：{act, to, fact?}
```

`Speech` 用方案 5.1 的类型。`resolve.apply(state, defs, action, speech)` 一并结算一个身体动作加一句话（顺序：先话后动作；话里的请求立即裁决）。只说话、不动的，`action` 传 `wait` 且耗时按 `say` 的 1 刻计（`actions.build_speech_wait(state, defs, actor)` 给出这个 `wait`；此时不另记「等」的事件）。

要旨（`Speech.gist`）是第三人称的一句话，其中的「对方」指听话的人；所求之事带了 `ask_action.desc`（例如情境里的一招「拱手赔个不是」）时，答复、许诺、牵挂里都用这句说法。内核的事实句是记录体（`⟦甲⟧对⟦乙⟧请求：要旨`），给模型当事实用；直接给玩家看的模板叙述由 `narrator.templates.speech_sentence` 重排成通顺的一句。

请求的五个类别用英文键：`trivial`（举手之劳）、`effort`（费事）、`costly`（有损己利）、`risky`（冒险）、`betray`（违背所属）。关系四档：`hostile`、`stranger`、`familiar`、`trusted`。中文标签在 `defs.world` 里。

### 2.4 世界层要用到的内核函数

- `kernel.effects.apply_effects`：情境、打算的离场结算、机会牌都只经它改世界。
- `kernel.predicates.holds`：打算的 `pre`、情境的 `when`、参照点的 `holds`。
- `kernel.actions.matches_do(action, do, ctx) -> bool`：判断一个动作是否符合打算一步的 `do`。
- `kernel.actions.path(state, defs, actor, frm, to) -> list[Exit]`：最短路（按 `cost`，跳过此人走不了的出口）。
- `kernel.timeflow.run_due_scheduled(state, defs)`：world 在不推进时间的地方（开局、情境开场与离场结算之后）补执行已到点的 `scheduled`。
- `kernel.resolve.apply(..., offscreen=True)` 与 `resolve.run_effects(..., offscreen=True)`：离场结算。
- 记账用的效果（只改 `storyteller`、回合数、候选、队列这类簿记，不改世界里的人和物）：`teller`、`turn`、`offer`、`dequeue`、`mark_char`、`diverge`、`thread` / `close_thread`、`chronicle`；布景用的 `request`、`superior`、`fact`。形状见 `effects.EFFECT_SHAPES`。

### 2.5 玩家视图（方案 4.4）

`perception.player_view(state, defs, events) -> PlayerView(clock, location, location_desc, scene_mode, events, heard, present, items_here, names, body, danger, ended)`。叙述只拿得到这个视图。其中每条事件：

```python
class ViewEvent(BaseModel):
    id: int
    text: str                 # 渲染好的事实句（玩家视角的称呼）
    actor: str | None         # 动作者的称呼
    verb: str
    outcome: str
    speech: dict | None       # {act, to, stance, gist, spoken_text, whisper}；没听清的悄悄话只有 {act: 'whisper', to, whisper}
    by_player: bool
    actor_id: str | None      # 动作者的 id
    target_ids: list[str]     # 对象的 id：params 里的 target / target2，加上说话的对象
    loc: str                  # 事发地点的 id
```

事件动词与动作动词大体同名，另有情境与效果 `emit` 出来的动词（`slap`、`laugh`……）。跟着别人换了地方的事件动词是 `follow_move`（视图靠它知道有人换了地方）；`follow` 只表示「开始跟着某人」，不换地方。

## 3. `Defs`：不会变的定义

`Defs` 由 `content/loader.py` 读 `content/` 生成，运行期只读，传给所有内核函数。存档里只记 `content_hash`（content/ 全部文件内容的 sha256）。

```python
class Defs(BaseModel):
    world: WorldRules                    # world.yaml：W 表、裁决门槛、salience 表、中文标签
    arts: dict[ArtId, ArtDef]            # arts.yaml（方案 6.12）
    cards: dict[CharId, CardDef]         # characters/*.yaml（方案 5.4）
    situations: dict[str, SituationDef]  # situations/*.yaml（方案 5.7）
    goals: GoalsDef                      # goals.yaml（方案 8.3）
    routines: dict[str, list[RoutineRule]]   # routines.yaml（方案 9.4）
    opportunities: dict[str, OpportunityDef] # opportunities.yaml（方案 8.5）
    baseline: list[BaselineMark]         # baseline.yaml（方案 8.6）
    lexicon: Lexicon                     # lexicon.yaml + 各实体的名字与叫法
    style: StyleDef                      # style/samples.yaml
    content_hash: str
```

各文件的格式见 `docs/CONTENT_FORMAT.md`。

## 4. 模型调用

### 4.1 `llm/` 的门面

```python
class LLM:                     # llm/__init__.py
    settings: Settings
    jev: JevClient | MockJev
    claude: ClaudeClient | MockClaude
    recorder: Recorder         # calls.jsonl；--replay 时按录制返回
    replayer: Replayer | None  # 只在 --replay 时有：录制源。mismatches 是与录制对不上的各处（每处一句说明），
                               # remaining() 是还没用到的录制调用（代号 → 次数）
    ledger: Ledger             # 记账与预算护栏

# Jev：async。hints 只给 Mock 用，真客户端忽略它。timeout_s：这一次调用最多等多久，到点抛 JevTimeout
# （JevError 的子类，同时是内置 TimeoutError；录成一条错误行，重放不错位）。不要在外面套 asyncio.wait_for。
answers = await llm.jev.ask(kind, state_text, questions, *, hints=None, escalated=False, timeout_s=None)

# Claude：async。mock_default / mock_text 只给 Mock 用。
obj, meta = await llm.claude.call_json(kind, system_blocks, user_text, schema, *, mock_default)
async for piece in llm.claude.stream_text(kind, system_blocks, user_text, *, mock_text): ...
```

`kind` 取调用代号：`J1`…`J6`、`C1`…`C7`、`P1`…`P6`。代号决定用哪套配置（`[models.*]`）、记账科目和录制的键。

`llm/route.py` 提供方案 4.3 的公用判断：

- `should_escalate(answer, thresholds, *, important: bool) -> bool`
- `sample_choice(probabilities, roll_fn, floor) -> str`（概率不低于 `sample_floor` 的选项里按概率取样；随机数由调用方从 `kernel.rng` 传入，保证可重放）
- `mode(settings, category) -> 'jev_first' | 'claude_first' | 'claude_only' | 'rules_only'`；类别：`classify`、`decide_soul`、`decide_named`、`replan`、`options`、`check`、`cards`，以及只有 Claude 一条路的 `narrate`（C3）、`phrase`（C4）、`pipeline`（`rules_only` 即不调模型，全用模板）。`uses_model(settings, category) -> bool`

### 4.2 Mock（方案 10.6）

- `MockJev`：`choice` 取描述以「（你本来要做的）」开头的项，否则取第一项，概率给 1；`noul` 取 `hints` 里的值，没有就是 0；`score` 取 `hints` 里的值，没有就是 1（截在刻度之内）。
- `MockClaude`：`call_json` 返回 `mock_default()`；`stream_text` 逐句吐出 `mock_text`。
- Mock 不联网，不花钱，照样录进 `calls.jsonl`。

### 4.3 各模块的入口（上层之间的接口）

```python
# parser/
intent = await parser.classify(llm, state, defs, raw)          # J1 → C1；括号与星号里的话先剥掉
bound  = parser.bind(state, defs, intent)                       # Bound(kind, action, speech, lethal, signature, reply)
# minds/
actors    = minds.pick_reactors(state, defs, events, limit=settings.limits.max_reactors)
decisions = await minds.decide_all(llm, state, defs, actors, events=events)
#   list[MindDecision(actor, action, speech, inner, display, via, escalated, important, notes, closing)]
for d in minds.order(state, decisions, events):                 # 见下「结算次序」
    events += minds.apply_decision(state, defs, d)              # 已不合法的降级为 wait；过时的话不说
d = await minds.decide_one(llm, state, defs, actor, events=(), offscreen=False)   # world.tick 的 decide 回调传 offscreen=True
minds.memory.retrieve(state, char_id, entities, k=6)
await minds.reflect.nightly(llm, state, defs)                   # C6：玩家歇够两个时辰或跨日时
# world/
events = await world.tick(llm, state, defs, decide=minds.decide_one)  # 每个时辰一次（方案 8.1 的五步）；第 5 步认定分岔
world.end_of_turn(state, defs)                                  # 每回合一次：情境的 turns、散场
world.onscene_elapsed(state, defs, ticks, credited=None)        # 时间过去后给在场者「等」的那一步记进度
events = await world.process_queue(llm, state, defs)            # 重新打算的队列（J3 / C5）
# storyteller/
storyteller.threads.update(state, defs, events)
options = await storyteller.pick_options(llm, state, defs)      # 三个 Option，见下
storyteller.note_choice(state, defs, option)                    # 玩家选了哪个
storyteller.note_action(state, defs, action, speech=None)       # 自由输入做了什么；只说了一句话时带上那句话
events  = await storyteller.pace(llm, state, defs)              # 机会牌（J6）
storyteller.chronicle.record(state, defs, events)
async for piece in storyteller.epilogue(llm, state, defs): ...  # C7
# narrator/
async for piece in narrator.narrate(llm, state, defs, view): ... # C3 流式 + 逐句检查 + J5
texts = await narrator.phrase_options(llm, state, defs, options) # C4 + 模板兜底
```

**选项。** `storyteller.Option(action, tag, score, rule, thread_ids, speech, id, signature, desc)`：

- 身体动作的选项：`action` 是内核的 `ActionInst`，其余三项照它填。
- 开口的选项（方案 2.1 的「低声劝……赔个不是」）：`speech` 是内核的 `Speech`，`action` 是 1 刻的 `wait`；`id` 在这一组候选里唯一（J4、C4 用它对号），`signature` 用来去重和记「上回合选过」，`desc` 是一句中性的意图（实体写成 `⟦id⟧`）。候选由 `storyteller/speaking.py` 按规则列出，与身体动作同一张计分表。
- 结算一律是 `resolve.apply(state, defs, option.action, option.speech, source='player')`（`game` 另加 `defer_train=True`：玩家读、练、教的进度不预付，在 `_pass_time` 里按实际过去的刻数记，被打断即止）；`game` 把三个选项经 `offer` 效果存进 `state.pending_options`（读档后原样恢复）。
- `narrator.phrase_options` 两种都认：开口的选项给 C4 的事实句写明言语行为、对谁说、要旨；模板句按言语行为取。

**结算次序（方案 4.3）。** `minds.order`：本轮事件里话是冲着他说的人最先（先答话，别人再出手），其余当事人次之，旁观者最后；同级按 `initiative` 从高到低。会让情境散场的那一招（`MindDecision.closing`）整体排在不散场的招之后，免得它一落下，同场其他人这一拍想好的事全都落空。在场的抉择里话是「答应」而所求之事此刻做得出的，动作就是那件事（答应即照做；「住手」类的请求是等）。

**分岔。** 与原著的分岔只在 `world.tick` 的第 5 步由 `world.baseline.check` 认定，经 `diverge` 效果写进 `state.divergences`；`storyteller.chronicle` 只读它。

方向标签用中文：`推进`、`人物`、`探索`、`自保`、`介入`、`成长`（方案 2.3）。

## 5. 实体引用

事件的 `fact_text`、动作的 `desc`、事实的 `text` 里提到人、地、物、本事时，写成 `⟦id⟧`。`kernel/refs.py` 负责渲染：

- `render(text, state, defs, viewer=None)`：`viewer` 为空时一律用真名（日志、年谱的全知视角）。
- `viewer == state.player` 时：玩家本人写成「你」；`id` 在 `state.known_terms` 里用真名；在 `state.term_alias` 里（新增字段，例如玩家只知道段誉姓段时为「段公子」）用那个叫法；否则用实体的 `unknown_alias`。
- 情境与机会牌的效果文字里写 `{角色位}`（如 `'{victor}一掌掴在{guest}脸上'`），内核替换成 `⟦角色所绑的 id⟧`。

## 6. 回合与时间（方案 4.2）

`game.Game.turn(raw)` 按方案 4.2 的伪代码执行。时间推进由 `game.pass_time(n)` 统一处理：逐个时辰边界推进，每跨一个时辰调用一次 `world.tick`；有动静让玩家看见或听见时，在那一刻打断（方案 6.9、8.7）。

## 7. 测试

- 除 11.6 的评测与 `scripts/api_smoke.py` 之外，全部测试不联网，用 Mock。
- 内核的单元测试用一个小世界夹具：3 个地点、4 个人（`tests/fixtures/mini/`，格式与 `content/` 相同）。
- 测试按模块放：`tests/kernel/`、`tests/llm/`、`tests/world/` ……；跨模块的放 `tests/e2e/`。
- 运行：`.venv/bin/python -m pytest -q`；检查：`.venv/bin/ruff check src tests`。

## 8. 游戏与界面之间（`game.Game` ↔ `ui/`）

`ui` 只认 `game.Game` 和 `ui/events.py` 里的界面事件，不 import 其他模块。

```python
# ui/events.py
Header(clock: str, place: str)          # 〔第一日 辰时 · 某处〕（地名按玩家知道的叫法）
Text(piece: str)                        # 叙述的一段流式文字（已过检查）
Break()                                 # 一段叙述结束
Options(items: list[str])               # 三个选项的文字，按一、二、三
Notice(text: str, level: 'info'|'warn') # 戏外提示：预算、存档成功、命令输出
Ended(reason: 'closed'|'dead'|'left')   # 收场；后事随后以 Text 流出

# game.py
class Game:
    state: WorldState; defs: Defs
    @classmethod
    async def new(cls, settings, *, seed, mock, player_name=None, run_id=None, replay_dir=None,
                  debug=False, save_name=None, validate=True) -> Game
        # 开局前跑一遍 content.validate；有问题抛 ContentInvalid（ContentError 的子类，problems 是清单），不开局。
        # load 不校验（存档按 content_hash 对的是同一份内容）。validate=False 留给故意用残缺内容的测试。
    @classmethod
    async def load(cls, settings, save_name, *, mock, debug=False, run_id=None, replay_dir=None) -> Game
    last_turn: TurnRecord | None    # 上一回合的记录：kind（turn / reply / command / warn / confirm）、interrupted、notes
    last_view: PlayerView | None    # 上一屏的玩家视图（测试拿它做视角检查）
    def start(self) -> AsyncIterator[UIEvent]           # 开场叙述与第一组选项
    def turn(self, raw: str) -> AsyncIterator[UIEvent]  # 一回合（方案 4.2）；斜杠命令也走这里
    async def aclose(self) -> None
```

`game.Game` 另管：逐刻打断的长动作（动静传到玩家耳目的那一刻停）、每回合末的自动存档、每日之始与大险之前的检查点、`runs/<id>/` 下的 `transcript.md`、`events.jsonl`、`calls.jsonl`，以及 `--replay` 的逐字重放。

重放对不上录制时（`llm.replayer.mismatches` 不空），对不上的调用照 4.3 兜底、游戏不中断；`Game` 在头一个对不上的回合末尾出一条 `warn` 的 `Notice`（处数、头一处是哪一次调用的哪个字段，不带请求内容），一局只出一次，并记进 `TurnRecord.notes`；`python -m tianlong play --replay` 跑完后把头一处与处数打到标准错误，以退出码 1 结束（录下的输入喂完而录制的调用没有用完，同样算对不上）。

斜杠命令（方案 2.7）由 `game` 分派：只读的画面（`/状态 /物品 /人物 /本事 /回顾 /帮助`）由 `ui/commands.py` 的纯函数 `(state, defs) -> str` 渲染；`/牵挂 /年谱` 由 `storyteller` 渲染；`/存档 /读档` 走 `store`；`/心愿` 走 `parser.map_wish` 加 `storyteller.threads`；`/收笔` 走收场；`/debug` 只在 `--debug` 下可用。
