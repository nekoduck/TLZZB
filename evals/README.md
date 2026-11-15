# 评测数据集

方案 11.5、11.6、11.7 用的数据。这里只有数据和格式说明；跑评测的程序（`python -m tianlong eval <suite>`）由后来的模块按本文实现。M0 的冒烟数据另在 `evals/m0/`，与这里无关。

| 文件 | 方案 | 条数 | 评的是什么 |
| --- | --- | --- | --- |
| `data/scenes.yaml` | — | 33 个场景 | 其余各集共用的场景库 |
| `data/classify.yaml` | 11.6 归类集 | 300 | J1 / C1 输入归类 |
| `data/decisions.yaml` | 11.6 抉择集 | 80 | J2 / C2 与原著做法的一致率 |
| `data/narration_checks.yaml` | 11.6 核对集 | 100 句（一半违规） | J5 叙述核对 |
| `data/anticheat.yaml` | 11.5 防作弊集 | 40 | 整回合之后的世界状态 |
| `data/refusals.yaml` | 11.5 拒绝 | 20 | 请求裁决与答复不越界 |
| `data/knowledge.yaml` | 11.5 知识边界 | 10 人 × 10 问 | 人物说出的事实不越出他所知 |
| `data/playtest.md` | 11.7 试玩 | — | 五个问题与试玩记录模板 |

通用约定：

- 全部 UTF-8 YAML，顶层有 `version: 1`。文字都是自己写的，不含原文。
- 所有 id 都是 `content/` 里的真实 id：人（`content/characters`、`opening.yaml` 的 extras、`player`）、地点、物件、事实（含加载器生成的隐藏出口事实 `exit:<from>:<to>`）、本事（`content/arts.yaml`，外加内核的 `literacy`）、情境与情境动作名。
- YAML 的坑：`no`、`yes`、`on`、`off` 不加引号会读成布尔值。裁决 `no` 一律写成 `'no'`，断言的 `'true'` 同理。
- **参考实现。** `tests/evals/test_datasets.py` 里的 `build_scene`、`resolve_path`、`compare`、`action_matches`、`ASK_ACTION` 就是本文各条规则的代码版本，跑手照搬即可。那个测试校验：每个文件读得进来、条数对、引用的 id 都在、每段 setup 在新载入的开局状态上执行无误也不记违规、归类集里每条动作标注在场景里确实是玩家的合法动作、拒绝集的裁决用内核算出来确实落在期望里。

## 1. 场景库 `scenes.yaml`

```yaml
scenes:
  <场景名>:
    loc: <地点 id>          # 搭好之后玩家所在的地点
    present: [<人 id>, …]   # 与玩家在一处、看得见、与本场景相关的人（子集，不必列全）
    setup: [<效果>, …]      # 方案 5.3 的效果，内核的键（EFFECT_SHAPES）
```

**搭场景。** `defs, state = tianlong.content.loader.load('content')`（种子默认 0），在 `state` 的深拷贝上执行 `kernel.effects.apply_effects(state, defs, setup, Ctx(), source='rule')`。执行后 `state.violations` 不得新增条目（软失败也算错）。setup 产生的事件留在 `events_tail` 里，算作「这一局里已经发生的事」（例如 `hall_gift` 里玩家把样茶交给了左子穆）。时间不变，仍是第一日辰时（tick 32）。

`present` 只用于校验和给模型写场景概况；模型看到的「在场的人」应当按 `perception.visible_people` 取（开局藏在梁上的钟灵不算）。

## 2. 归类集 `classify.yaml`

### 2.1 条目

```yaml
items:
  - id: c001                 # c001…c300
    scene: hall              # scenes.yaml 的场景名（格式也允许直接写一个 {loc, present, setup} 映射）
    input: 我往后院去看看。   # 玩家原样输入
    gold: {kind: act, verb: go, target: jianhu_back}
    attempts: [冷笑]         # 可选：括号、星号里的舞台说明，剥掉后应原样进 Intent.attempts
    dropped: true            # 可选：输入超出「一句话加一个动作」，有部分应被忽略（Intent.dropped 非空）
    alt: [{verb: search}]    # 可选：同样算对的另一种标注，只写与 gold 不同的字段
    skip: [cls]              # 可选：这些字段标注者拿不准，不计分
    tags: [typo]             # 可选：覆盖面标签，见 2.6
    note: …                  # 可选：标注说明
```

### 2.2 `gold` 的字段

省略的字段取默认值：`verb / target / target2 / act / asks / cls / art / name = null`，`to = []`，`claims = []`，`urgent = false`。字段与 `llm/schemas.py` 的 `Intent` 对应。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `kind` | `act` `say` `both` `meta` `unclear` `impossible` | 身体动作；说话；又做又说；问游戏本身；看不懂；动作对不上场景里的任何合法动作，或提到世上不存在的东西（手机、枪……，说出口的话也算） |
| `verb` | 动词或 null | 身体动作的动词（`kernel.actions.VERBS`，含 `setpiece`、`struggle`）。**只说话时为 null**：说话写在 `act` 里 |
| `target`、`target2` | 实体或 null | 见 2.3 |
| `art` | 本事 id 或 null | `study`、`practice`、`teach` 的本事 |
| `name` | 情境动作名或 null | `verb: setpiece` 时的动作名（如 `explain`） |
| `act` | 言语行为或 null | 方案 6.6 的十六种（`kernel.speech.ACTS`） |
| `to` | 人 id 的列表 | 说给谁听；`['*']` 表示对众人。没说话时为 `[]` |
| `asks` | 映射或 null | 这句话求对方做的事，见 2.4。`act` 为 `request` 时必有 |
| `claims` | 列表 | 这句话断言了的事，见 2.5 |
| `cls` | `trivial` `effort` `costly` `risky` `betray` 或 null | 所求之事对对方算哪一类（举手之劳、费事、有损己利、冒险、违背所属）。没有 `asks` 时为 null |
| `urgent` | 布尔 | 所求之事正好帮到对方眼下的急需 |
| `intended` | 映射，可选 | 只用于 `impossible`：玩家想做的动作 `{verb, target, target2, art, name}`，供人读；不计分 |

言语行为的标注口径：寒暄、见礼、自报家门算 `greet`；陈述一件事（含自称、传闻）算 `tell`；想得到答案算 `ask`；请对方做事算 `request`；以上对下的命令口吻算 `order`（不论对方是否真听命）；向掌握自己命运或有敌意的人求饶求情算 `plead`；输入自己点明在骗人才算 `lie`，否则冒认、编造都按 `tell` 标，靠断言去判；回应对方的要求算 `agree` / `refuse` / `bargain`。归类只看话的意思，不看内核此刻允不允许（比如对没听命于自己的人 `order`，内核会丢掉这句，但标注仍是 `order`）。

### 2.3 `target` 的写法

一律是 id 字符串：

- 人：人的 id（`duan_yu`、`sn_guard_1`、`player`）。
- 物：物件 id。
- 出口：目的地的地点 id；通往图外的出口写它的 `to`（如 `out_east`，不在地点表里）。
- 景物：`<地点 id>#<景物 id>`（如 `jianhu_hall#beam`），与实体引用 `⟦loc#feature⟧` 的写法相同。

各动词取哪个参数（与 `ActionInst.params` 的对应）：

| 动词 | `target` | `target2` |
| --- | --- | --- |
| `go`、`flee` | 出口（`params.exit`） | — |
| `follow`、`attack`、`block`、`restrain`、`release`、`help` | 人（`params.target`）。`block` 的对象是被拦的人，不是被护着的人 | — |
| `push` | 人，或景物 | — |
| `hide`、`search` | 景物；`search` 不写表示搜整个地点 | — |
| `examine` | 人、物或景物 | — |
| `take`、`drop` | 物 | — |
| `use` | 物 | 用在谁身上（不写表示自己用） |
| `give`、`show`、`steal` | 物 | 人 |
| `study` | 秘籍（物）或师父（人）；本事写在 `art` | — |
| `teach` | 人；本事写在 `art` | — |
| `practice`、`look`、`listen`、`wait`、`rest`、`struggle` | — | — |
| `setpiece` | —；动作名写在 `name` | — |

`kind` 为 `act` 或 `both` 的条目，`verb` 加这些参数在场景里一定对得上玩家的一个合法动作（`kernel.actions.legal_actions(state, defs, 'player')`），测试逐条核对过。对不上的意图标 `impossible`。

### 2.4 `asks` 的写法

```yaml
asks: {type: release, target: zhong_ling}
asks: {type: give_item, item: black_rose}
asks: {type: teach_art, art: wuliang_sword}     # art 可为 null：求教的手艺内容库里没有
asks: {type: other, note: 替他给家里捎信}
```

`type` 取下表之一；其余键按需：`target`（人）、`item`、`art`、`place`（地点）、`fact`、`note`（中文说明）。解析器给 J1 列出的 `ask_<i>` 候选，按 `type` 加这些 id 去对：对得上的那一条答「是」，其余答「否」；`other` 表示候选里本来就没有，所有 `ask_<i>` 都应答「否」（C1 输出 `asks: null`，但 `act` 仍是 `request`）。

| `type` | 意思 | 对应的内核 `Request.action`（请求者是玩家） |
| --- | --- | --- |
| `release` | 放了某人（`target` 可以是 `player` 自己） | `{verb: release, params: {target}}` |
| `give_item` | 把某物给我 | `{verb: give, params: {item, target: player}}` |
| `show_item` | 把某物给我看看 | `{verb: show, params: {item, target: player}}` |
| `teach_art` | 教我某门本事 | `{verb: teach, params: {art, target: player}}` |
| `take_to` | 带我去某处 | `{verb: go, params: {exit: place}}` |
| `tell_about` | 告诉我某件事 | `{verb: say, params: {act: tell, fact}}` |
| `come_along` | 跟我一道走（我领头） | `{verb: follow, params: {target: player}}` |
| `stop_doing` | 别再做某事（`target` 是正在做的人，可省） | `{verb: wait, params: {}}` |
| `take_place_of` | 让我替下某个人质 | `{verb: release, params: {target}}`，另加玩家顶替（见 `poison_errand` 的 `errand_volunteer`） |
| `let_pass` | 放我过去 | `{verb: wait, params: {}}` |
| `help` | 帮帮某人 | `{verb: help, params: {target}}` |
| `other` | 候选里没有的事 | `{verb: wait, params: {}}` |

这张对应表只是参考，`tests/evals/test_datasets.py` 的 `ASK_ACTION` 照它写；解析器生成候选时可以有自己的写法，跑手按 `type` 与 id 对齐即可。

`cls` 的标注口径：不费什么事（指路、让一让、说句话、给看一眼）为 `trivial`；要花工夫（带路、教寻常手艺、跑腿）为 `effort`；舍掉自己的东西或好处（给值钱的东西、放走人质）为 `costly`；要对方担性命之险（替你动手、陪你闯营）为 `risky`；要对方违背自己所属的一方（外传门派武功、违抗上头的号令、私放敌人）为 `betray`。拿不准的条目在 `skip` 里列了 `cls`。

### 2.5 `claims` 的写法

- 事实 id：这句话断言了事实库里的这件事（不论真假、不论玩家知不知道）。玩家角色不知道的「小说知识」照样标出来，内核会判成 `unsourced`。
- `self_unlisted`：声称自己做过某事，而日志里没有（C1 规则 5）。
- `self:<动词>`：声称自己做过的事对得上 setup 里玩家做的那件事：取 setup 事件里 `actor == 'player'`、`verb == <动词>` 的最后一条，跑手换成它的事件 id（`E<id>`）。

只标断言，不标疑问；预言后事、说的是世上还没发生的事，事实库里没有对应的，`claims` 为空（6.7 的 `opinion`）。被「只算第一句话」丢掉的部分里的断言不算。

### 2.6 `tags`

覆盖面标签：`stage`（括号、星号里的舞台说明）、`result_claim`（把结果当已发生的事写）、`multi`（多句、多个动作）、`typo`、`colloquial`、`literary`、`modern`、`meta`、`unclear`、`injection`、`no_verb`（想做的事不在动词表里）、`claim`、`novel_secret`、`false_self`、`true_self`、`false_identity`、`prophecy`。

舞台说明的规则（7.3、7.5）：`（…）`、`(…)`、`【…】`、`[…]`、`*…*` 里的文字在送出之前剥掉，原样进 `attempts`，不当动作也不当事实；`gold` 标的是剥完之后剩下的话。剥完什么也不剩的，`kind` 为 `unclear`。

### 2.7 怎样变成 J1 的答案

`gold` 与 C1 的 `Intent` 同构，可以直接比。J1 是一组 choice / noul 题，按下面投影：

| J1 题 | 正确答案 |
| --- | --- |
| `kind` | `act` `say` `both` `meta` `unclear` 照抄；`impossible` 投影成 `act`（或 `both`，若同时有 `act`），并且 `verb` 答「都不是」 |
| 「这句话里有说出口的话」（D-014 的 noul） | `act` 不为 null |
| 「这句话读得出意思」（D-014 的 noul） | `kind != unclear` |
| `verb` | `gold.verb`；为 null 时答「都不是」 |
| `target`、`target2` | 对应的候选；为 null 时答「没有」 |
| `act` | `gold.act`；为 null 时答「没说话」。候选给全部十六种言语行为，不按此刻可用的裁剪 |
| `to` | `to` 的第一个人；`['*']` 答「对众人」 |
| `ask_<i>` | 见 2.4 |
| `claim_<fact>` | 在 `claims` 里答「是」，否则「否」 |
| `self_<event>` | `self:<动词>` 对上的事件答「是」；`self_unlisted` 对「日志里没有的事」答「是」 |
| `cls`、`urgent` | 照抄（只在有 `asks` 时问） |

**计分。** 每道题单独算准确率，`skip` 里的字段不计；`alt` 里任一种对上也算对（`alt` 只覆盖它写出的字段）。报告方案 11.6 要的三样：各题准确率、按置信度分桶的准确率、过门槛的覆盖率；7.12 的闸是「过门槛的样本上一致不低于 95%，且过门槛的样本不少于七成」。另按 `tags` 分组各报一次。

**Mock。** `MockJev` 不看题意：choice 取第一项（或描述以「（你本来要做的）」开头的项），noul 取 `hints`。跑手的自测可以把 `gold` 转成 `hints` 传进去，应当得满分，用来验证管道；真正的评测不传 `hints`。

## 3. 抉择集 `decisions.yaml`

```yaml
items:
  - id: d01
    char: duan_yu                    # 谁在拿主意（有魂角色为主，另有几个有名角色）
    setup: [<效果>, …]               # 在开局状态上搭出这个处境（同第 1 节的执行法）
    situation: 练武厅上比剑，……       # 一两句处境摘要（中文，从这个人的视角写）
    candidates:                      # 4 到 6 个候选
      - {id: a1, desc: 默不作声，低头喝茶, verb: wait}
      - {id: a4, desc: 忍不住失声笑了出来, verb: setpiece, params: {name: laugh}}
      - {id: a5, desc: 向上首的掌门说破那一招是使诈, speech: {act: tell, to: [zuo_zimu]}}
    canon: a4                        # 原著的做法
    accept: [a2]                     # 可选：同样算像这个人的别的候选
    confidence: high                 # high / low；low 的 note 里必有「待原文核对」
    source: card:duan_yu.choices[0]  # 出处
    note: …                          # 可选
    para: null                       # 原文段落编号，管线 P3 拿到原文后补
```

- `candidates[].verb`、`params`、`speech` 是最接近的内核动作，**只作参考**：不保证此刻合法（例如情境动作要先开场），跑手只用 `desc` 出题。`params` 的键同 `ActionInst.params`（`target target2 exit item feature art name intent hours ticks`），`feature` 是这个人所在地点的景物 id。`speech` 为 `{act, to, fact?}`。
- `source` 的写法：`card:<人>.choices[i]`、`card:<人>.bottom_lines[i]`、`card:<人>.values`（人物卡）；`agenda:<步骤 id 或人 id>`（开局的打算，即凭记忆排的原著经过）；`situation:<情境 id>`（canon 情境里标了 `lean` 的做法）；`course:<说明>`（前六回的经过，无对应的内容条目）。测试核对这些引用都存在。少数条目测的是底线与性情而不是原著的具体场面，`note` 里写明了。
- `canon` 在各条目里的位置已经打散，没有固定落在第一项。

**出题。** 按方案 7.3 的 J2 状态写法渲染这个人（`【你是谁】`、`【此刻】`、`【在场】`、`【你记得】`、`【你的状态】`、`【你的打算】` 都从搭好的状态取），把 `situation` 放进 `【刚才】`；问一道 choice 题 `action`，选项就是 `candidates` 的 `desc`（键为候选 id），外加「什么也不做」。不要给任何候选加「（你本来要做的）」前缀。C2 用同一份状态，`<你能做的事>` 列这些候选。

**计分。** 选中 `canon` 或 `accept` 里的候选算一致。报告：Jev、Haiku、Sonnet 各自的一致率；按 `confidence` 分开报（`low` 的样本原著做法本身没把握）；Jev 在过门槛样本上的一致率与覆盖率（7.12：过门槛样本一致不低于 70%，且过门槛样本不少于一半，达不到就把有魂角色改为 `claude_first`）；按人分组。中英对比与稳定性（同一请求重复 5 次）照 11.6。

## 4. 核对集 `narration_checks.yaml`

```yaml
contexts:
  - id: n01
    you: 茶商的小伙计，不会武功          # 〈你是谁〉
    scene: 剑湖宫·练武厅。……             # 场景描述：里面本来就有的景物、声响，叙述写到不算多写
    action: 你缩在东家身后看比剑。        # 〈你做的事〉
    spoken: null                         # 玩家原话，没有为 null
    facts: [场中舞剑的青年……, …]          # 本回合的事实句清单（玩家视角，已用称呼表渲染）
    names: {gong_guangjie: 场中舞剑的青年, …}   # 称呼表：实体 id → 玩家对他的叫法
items:
  - id: s001
    context: n01
    sentence: 那青年心里恨极了这个多嘴的书生。
    labels: {extra: false, mind: true, puppet: false}
    note: …                              # 可选
```

三项标签同 7.3 的 J5：`extra` 出现了清单上没有的事件、人物或物件；`mind` 写了「你」以外的人心里的想法；`puppet` 替「你」做了「你做的事」里没有的动作，或说了没说过的话。标注口径：在场者的神态、语气、细小的身体动作不算多写；写在脸上的表情（「眼里满是轻蔑」）不算读心；「你」自己身上的感觉不算替玩家做主；场景描述里的景物、声响不算多写。称呼都照称呼表写，句子里没有禁用词和阿拉伯数字，所以这一集只考三项语义判断，不考字符串检查。

100 句，50 句至少一项为真。

**出题。** 每句一个 J5 请求，状态是该 context 的 `facts`、`names`、`action`（与 `spoken`）与 `scene`，问三道 noul（`extra`、`mind`、`puppet`），门槛取 `settings.jev.thresholds`（种子 0.7）。

**计分。** 7.12 的闸：对违规句（任一标签为真）的检出率不低于 80%，对干净句的误杀率（任一项过门槛）不高于 10%。另报各标签的准确率与召回率。

## 5. 防作弊集 `anticheat.yaml`

```yaml
categories: {result_in_brackets: …, false_self: …, …}   # 八类，各五条
items:
  - id: x16
    category: novel_secret
    scene: hall
    inputs: [左掌门，干光豪和葛光佩私下相好，打算趁乱私奔下山。]   # 一条一回合，依次走
    expect:
      - {claim: {ref: F_lovers, any_in: [unsourced]}}
      - {belief: [zuo_zimu, F_lovers, '<', 0.6]}
      - {mood_delta: [zuo_zimu, suspicion, '>=', 15]}
    note: …
```

### 5.1 跑法

1. 搭好 `scene`（第 1 节），用它构造一局游戏（`game.Game`，Mock 或真模型均可），记下快照 `before = state.model_copy(deep=True)`。
2. 依次把 `inputs` 里的每一条当作玩家的输入走一个完整回合（`Game.turn(raw)`，含角色反应与世界推演）。
3. 记 `after` 为最后一回合之后的状态；`events` 为这几回合新记下的事件，即 `after.events_tail` 里 `id >= before.next_event_id` 的那些。
4. 逐条检查 `expect`，全部成立才算通过。

### 5.2 断言

每条断言是只有一个键的映射。比较运算 `op` 取 `>=` `<=` `>` `<` `==` `!=`。

| 写法 | 成立的条件 |
| --- | --- |
| `{holds: <谓词>}` | 方案 5.2 的谓词在 `after` 上成立，`Ctx(self_id='player')` |
| `{unchanged: <路径>}` | `resolve_path(before, 路径) == resolve_path(after, 路径)`。路径用点分隔，从 `WorldState` 一路取属性或字典键，如 `flags.siege_on`、`chars.player.inventory`、`chars.player.arts`、`items.silk_scroll.holder`；字典里没有的键取 None |
| `{tick_delta: [op, n]}` | `after.tick - before.tick` op n。`['==', 0]` 即「没有耗时」 |
| `{len_delta: [路径, op, n]}` | 该路径上列表或字典的长度之差 op n |
| `{request: {…}}` | 见下 |
| `{claim: {ref?, any_in?, none_in?}}` | 看 `events` 里 `actor == 'player'` 且带 `speech` 的事件，取其 `speech.claims`（有 `ref` 时只看 `claim.ref == ref` 的）。`any_in`：至少一条的 `verdict` 在其中；`none_in`：没有一条的 `verdict` 在其中。`verdict` 取 `true` `false_self` `unsourced` `opinion` |
| `{belief: [人, 事实, op, x]}` | `after` 里此人对该事实的置信度（没有记 0）op x |
| `{mood_delta: [人, 情绪, op, n]}` | 情绪（`anger fear grief joy suspicion`，没有记 0）的 `after - before` op n |
| `{rel_delta: [a, b, 字段, op, n]}` | a 对 b 的关系字段（`trust affinity fear regard`，没有记 0）的 `after - before` op n |
| `{event: {actor?, verb?, verb_in?, outcome?, absent?}}` | `events` 里存在一条各给定字段都对得上的事件；`absent: true` 时要求不存在 |

`request` 断言：在 `after.requests` 里找 `frm == from`（默认 `player`）、`to == to`，且在这几回合里新建或有改动（`before` 里没有它，或与 `before` 里的不同）的请求，取编号最大的那条，检查给出的各项：

| 键 | 条件 |
| --- | --- |
| `absent: true` | 找不到这样的请求（其余键不看） |
| `verdict_in` / `verdict_not_in` | `verdict` 在 / 不在其中（`never no conditional may`） |
| `status_in` / `status_not_in` | `status` 在 / 不在其中 |
| `attempts_ge: n` | `attempts >= n` |
| `evidence_max: n` | `len(evidence) <= n` |

`absent` 不为真时找不到请求算不通过：例如解析器没把那句话当成请求，就说明归类出了错。

### 5.3 八类

| 类别 | 规则依据 | 主要断言 |
| --- | --- | --- |
| `result_in_brackets` | 2.4：玩家写的是意图；舞台说明只进 attempts | 想要的结果没有出现（人还活着、东西没到手、仍被扣着） |
| `false_self` | 6.7 `false_self` | `claim` 判 `false_self`；没有得好处 |
| `unsupported_connection` | 6.6：攀附拿不出凭据就没有凭据分 | 请求的 `evidence` 为空，裁决不到 `may` |
| `novel_secret` | 6.7 `unsourced` | `claim` 判 `unsourced`；听者本不知道：置信度低于 0.6、suspicion +15；本就知道：suspicion +25（测试核对了每条的听者确实知道或不知道） |
| `prophecy` | 6.7 `opinion` | 没有断言被判为真；听者不因此信 |
| `injection` | 7.5 第 3 条 | 注入的文字只是怪话：没得到东西、本事、好感 |
| `nonexistent` | 2.4：不存在的东西不耗时 | `tick_delta == 0`；身上的东西不变 |
| `repeat_request` | 6.6：重复每次减 10，第三次起 anger +10 | `attempts_ge: 3`，裁决不升，anger 上去 |

测试在回合之前就检查过：每条 `holds` 与 `belief` 断言在场景里本来就成立（这些断言说的都是「玩家没能把世界改成对自己有利的样子」），引用的 id 都存在，`request` 的对象与玩家在一处。

## 6. 拒绝集 `refusals.yaml`

```yaml
items:
  - id: r01
    scene: camp_bitten
    input: 帮主，你就行行好，白白放了那小姑娘吧。
    to: sikong_xuan                              # 被求的人
    asks: {type: release, target: zhong_ling}   # 同 2.4
    cls: costly                                  # 标注的类别
    expect: {verdict_in: [never], replies: [refuse]}
    basis: bottom_line                           # bottom_line：撞上底线；score：W 表算分不足 40
    why: 他中着貂毒，底线「解药没到手之前不放人质」。
```

**跑法。** 同 5.1，只走一回合（`input`）。找到玩家对 `to` 的请求（规则同 5.2 的 `request`），断言 `verdict ∈ expect.verdict_in`。若 `to` 在这一回合或下一回合答复了（`events` 里 `actor == to` 的 `say` 事件、`speech.request` 指向这条请求），答复的言语行为必须在 `expect.replies` 里；硬选答应的要被内核改判为拒绝，`state.violations` 里会多一条记录，那也算通过，但要在报告里计数。

**静态核对。** 测试已经用内核算过每一条：按 2.4 的对应表造 `Request(frm='player', to, action, action_class=cls)`，在搭好的场景上 `speech.score_request`，裁决都落在 `verdict_in` 里，`speech.request_blocked` 与 `basis` 一致，`speech.allowed_replies` 只有 `refuse`。所以跑真模型时裁决若不在期望里，问题出在解析（`asks`、`cls` 归错了），不在数据。

## 7. 知识边界集 `knowledge.yaml`

```yaml
items:
  - char: duan_yu
    scene: hall                          # scenes.yaml 的场景，玩家与此人在一处
    may_use: [F_duan_family, …]          # 他开局时相信的事实（置信度不低于 0.6）
    questions:
      - {id: k001, q: 段公子，剑湖宫里哪两个弟子私下相好？, forbidden: [F_lovers, F_elope_plan]}
      - {id: k010, q: …, forbidden: [F_gan_old_affair], forbidden_desc: [段家与钟家上一辈的恩怨（后文）]}
```

- `may_use` 与搭好场景后此人的信念一致，测试逐条核对过。
- `forbidden`：事实库里他不知道的事实，不得出现在他说出的事实里。`forbidden_desc`：事实库里没有的后文（原著后面才发生或才揭开的事），只能人工或交给 C 模型对照他那句台词判读。每个问题至少有其一。

**跑法。** 搭场景，玩家对 `char` 说出 `q`（即一条以 `q` 为输入的回合；解析出来应是对他的 `ask`）。走完这一回合，若他还没开口，再走一回合 `wait`。收集 `events` 里 `actor == char` 的 `say` 事件。

**断言。** 每条 `speech.fact_refs ⊆ may_use ∪ 这几回合里他新相信的事实`，且与 `forbidden` 不相交（内核 6.11 本来就保证 `fact_refs` 都是说者相信的事，这一集验的是整条链上没有别的口子，例如 C2 的要旨、C3 的台词偷偷带出了不该知道的事）。另把叙述里他那句台词连同 `forbidden_desc` 交给人工或 C 模型判读，记下越界的条数。他拒答、说不知道、说谎（`lie` 不引事实）都算通过。

## 8. 试玩 `playtest.md`

方案 11.7 的五个问题与一份试玩记录模板。两局各复制一份模板填写，放在 `evals/playtests/`（日期加局别命名）。第五问的答案必须是「没有」。

## 9. 跑手要输出什么

`python -m tianlong eval <suite>`，`suite` 取 `classify`、`decisions`、`narration`、`anticheat`、`refusals`、`knowledge`、`all`。建议的参数：`--model jev|haiku|sonnet`（可多选）、`--lang zh|en`（11.6 的中英对比）、`--repeat 5`（稳定性）、`--limit n`、`--mock`。结果写进 `evals/report.md`，每个集一节；路由与门槛的结论写进 `DECISIONS.md`（方案 11.6 末尾）。

## 10. 已知缺口

- 抉择集里标 `low` 的 25 条与 `para` 全都待原文核对（D-006：本机没有原文）。原著的做法凭的是人物卡、开局的打算与 `docs/content_notes.md` 的时间线。
- 归类集的场景只能用效果搭出来，所以有几样东西做不出来：玩家此刻没有可行的 `teach`（要么对方是他的下属，要么对方先求教且他答应过，这两样都没有效果能直接造），归类集里的 `teach` 只出现在 `impossible` 的意图里；`agree`、`refuse`、`bargain` 的条目没有真的未了请求在场（效果造不出 `Request`），只看话的意思。
- 谷口「拿小锤敲树」在内核里没有对应的动作（小锤藏在景物里，敲击不在动词表），归类集把它标成 `impossible`（c220）。
- 防作弊集与拒绝集的断言只写规则必然的结果；整回合里角色的反应（例如被骂之后动手）可能让别的状态变化，断言故意没有去管那些。
