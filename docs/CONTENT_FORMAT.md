# content/ 的文件格式

所有文件 UTF-8，YAML。谓词与效果的写法见方案 5.2、5.3。实体 id 用小写拼音加下划线；事实 id 以 `F_` 开头。文字里提到实体时写 `⟦id⟧`（见 `ARCHITECTURE.md` 第 5 节），情境与机会牌的效果文字里可写 `{角色位}`。

人工改过的条目可加 `locked: true`，管线重跑时不覆盖。

## world.yaml

```yaml
background: 北宋年间，大理国境内的无量山与万劫谷一带。   # 给提示词用的时代背景，一句
calendar: {ticks_per_hour: 8, hours_per_day: 12, start_tick: 32}
request:
  weights:                      # 方案 6.6 的 W 表
    trivial: {hostile: 20, stranger: 50, familiar: 70, trusted: 90}
    effort:  {hostile: 0,  stranger: 30, familiar: 50, trusted: 80}
    costly:  {hostile: -20, stranger: 10, familiar: 30, trusted: 60}
    risky:   {hostile: -40, stranger: 0,  familiar: 20, trusted: 50}
    betray:  {hostile: -60, stranger: -30, familiar: -10, trusted: 20}
  may: 60
  conditional: 40
  labels: {trivial: 举手之劳, effort: 费事, costly: 有损己利, risky: 冒险, betray: 违背所属}
  bands: {hostile: [-100, -30], stranger: [-30, 20], familiar: [20, 60], trusted: [60, 100]}
  close_roles: [same_sect, master_disciple, master_servant]   # 至少抬到 familiar 的身份关系
  sect_roles: [wuliang_disciple, shennong_member, …]          # 哪些身份算「同门、同一家」（两人都有其中同一个才算）
salience: {death: 10, harmed: 9, rescue: 8, promise: 7, exposed: 7, insult: 6, learn_key: 6, gift: 5, talk: 3, pass: 1}
labels:                         # 界面、牵挂、年谱里的说法
  role:jianhu_guest: 剑湖宫的来客      # role:<身份>：一个平白的名词短语（「成了……」「是……」都读得通），不写 ⟦id⟧
  poisoned_duanchang: ⟦duanchang_san⟧的毒   # <状态>：用在「身上的……到某时就要发作」里
deadline_statuses: []           # 除 poisoned* 之外，还有哪些带期限的状态要进「期限」类的牵挂
enmity:                         # 门派之间的敌对
  - {between: [shennong_member, wuliang_disciple], while: siege_on}
concerns:                       # 与所求有关的大事：所求的种类 → 事实标签
  clan_honor: [sect_scandal]
```

- `request.sect_roles` 不写时退回一条不认名字的规则：占着地盘的身份（某个地点的 `owner`）才算门派或人家；写成空表即任何共同身份都不算同门。来客、马帮、差使这类临时身份不要列进去。
- `labels` 里与专名表同名的身份可以不写，界面会退回去查专名表。
- `enmity`：每项 `{between: [身份甲, 身份乙], while: 旗标名?}`。`while` 的旗标为真期间（不写即一直），各带一边身份的两个人互为对头：内核的敌意判断（`danger.is_hostile`）、请求的关系档（算「敌对」）、谓词 `{enemies: [甲, 乙]}` 都认。同时带着两边身份的人不算哪一边；对那个人有私交（好感不低于 30）的不把他当对头。身份没了、旗标落了，敌对也就没了。内核的入口把这张表抄进 `state.flags['enmity']`（只拿得到状态的查询读它），内容里不要用这个旗标名。
- `concerns`：有这种所求（`drives` 的种类）的人新近相信了一条带这些标签的事实，就是「出了与他的所求有关的大事」（方案 8.3），立即进重新打算的队列。配合 `goals.yaml` 里相应的规则用（例：`honor_purge`）。
- 以 `poisoned` 开头的状态带了期限（`ticks`）就是「到期致死」：期限一到人就死；不足一日时玩家的危险等级至少 1，只剩两个时辰至少 2。

## locations.yaml

一个映射：`id → Location`，字段同方案 5.1 的 `Location`，另加：

- `handholds: true`：崖壁有树木藤蔓可挂，坠落的活命机会加 20（方案 6.4）。
- 出口 `edge: true` 时，`to` 写一个不存在的地点 id（如 `out_dali`），表示图外。
- 隐藏出口（`hidden: true`）：加载器自动生成事实 `exit:<from>:<to>`；谁相信这条事实，谁就看得见、走得了这个出口。景物的 `reveals` 可以列这条事实。

```yaml
cliff_edge:
  name: 断崖
  unknown_alias: 一处断崖
  terrain: -10
  handholds: true
  desc: {day: 崖边风大，往下看云气翻涌，看不见底。, dusk: …, night: …}
  exits:
    - {to: back_trail, cost: 8}
    - {to: valley_floor, cost: 4, check: descend}
  features:
    pine: {name: 崖边老松, desc: 一株老松斜伸出崖外，根扎在石缝里。, cover: 5}
```

## items.yaml

一个映射：`id → Item`，字段同方案 5.1 的 `Item`，开局的位置就写在条目里（`holder`，或 `loc` 加可选的 `feature`）。`props` 里常用的键：

| 键 | 含义 |
| --- | --- |
| `on_use: [效果]` | `use` 时执行；`self` 指使用者，`$target` 指对象。要分情形时用带前提、delay 0 的 `schedule`（例：小锤只在谷口敲得出名堂） |
| `cures: [状态]` | 服下即解除这些状态 |
| `inflicts: {status, ticks}` | 服下即中这个状态，`ticks` 后到期 |
| `food: true` | 吃了解饿 |
| `lethal: true` | 方案 6.10：吞下、使用前先警示 |
| `power_mod: n`、`flee_mod: n` | 冲突、逃跑的修正（方案 6.5） |
| `speed: 3` | 坐骑：赶路耗时除以它 |
| `on_obtain: [效果]` | 奇遇：第一次到手时执行（如闪电貂认主） |
| `evidence: strong` | 出示时算强凭据（信物） |
| `value: n` | 银钱的分量，讲价时用 |

活物（闪电貂、小蛇、莽牯朱蛤）也是物品，`tags` 含 `creature`。

## facts.yaml

```yaml
F_lovers:
  text: ⟦gan_guanghao⟧与⟦ge_guangpei⟧有私情，瞒着师门。
  tags: [secret, wuliang]
  gossip: false
  known_by: [gan_guanghao, ge_guangpei]     # 人的 id；'*' 表示图内人人皆知；'role:<身份>' 表示有此身份的人
```

`known_by` 明写为空（`[]`）＝开局时无人知道：这样的事实不得出现在任何人物卡、玩家、路人的 `knows` 里
（管线 P3/P4 见了就删，P7 报成问题）。不写 `known_by` 这个键的不受此限（知情人只记在人物卡的 `knows` 里）。

## arts.yaml

方案 6.12 的格式。另加 `secrecy`（`none` 默认 / `sect`）与 `teacher_roles`（谁算得上能教，例如 `[wuliang_disciple]`）。

## lexicon.yaml

```yaml
terms:                  # 不属于任何实体、却要管住的专名
  lingjiu: {name: 灵鹫宫, alias: 一个叫人害怕的去处}
banned_modern: [手机, 电脑, …]
banned_game: [血量, 经验值, 等级, 技能点, 选项, NPC, …]
```

禁用词按子串匹配，所以只收不会误伤寻常说法的整词：游戏词不收「回合」「任务」「经验」「属性」「背包」（斗了几个回合、江湖经验都是寻常话），现代词不收「小时」「老师」「医生」「新闻」。

加载器把人物、地点、物件、本事的 `name` 与 `unknown_alias` 自动并入专名表。

## characters/<id>.yaml

方案 5.4 的格式（有魂角色完整，有名角色简卡）。另：

- `inventory` 不写在卡里，写在 `items.yaml` 的 `holder`。
- `rel: {other_id: {trust, affinity, fear, regard}}`，没写的按陌生人（全 0）。
- `persona` 自动取文件名。
- 底线 `bottom_lines[].blocks[]`：`{verb, act?, fact_tag?, target?, art?, item?, …, when?}`。命中（动词相同、写了的键都匹配、`when` 成立）的动作从此人的清单里去掉；同一张表也管他不肯说的话，以及冲他来的请求里判「决不答应」的事。各键怎么比：
  - `act`：动词是 `attack` 时比的是 `intent`（`injure` / `kill`，不写按 `injure`）；`say` 时比言语行为；其余动词比参数里的 `act`。只写 `{verb: attack}` 是什么攻击都不做，写 `{verb: attack, act: kill}` 是只不下杀手。
  - `fact_tag`：所说（所涉）的事实带这个标签。
  - `target`：对象；可写 `self`、`player`、`$requester`（提出请求的人）。`say` 时对象在听的人里就算。
  - 其余的键（`art`、`item`、`exit`、`feature`、`intent`……）与动作的同名参数相等才算；写成列表时任取其一。
  - `when`：谓词，`$target` 指对象，`$requester` 指提出请求的人。

## agendas/<id>.yaml

方案 5.7 的格式。一步的 `do` 里可写的键：`verb`，以及 `target`、`to`、`item`、`exit`、`feature`、`art`、`act`、`fact`。开口相求的一步（`{verb: say, act: request | plead | order, to: 人}`）另写 `ask: {verb, params}`（请求对方做的事，`params` 里可用 `self`）与 `cls`（事情的类别，不写由内核按规则归类）；在场、离场求的都是这件事，不写 `ask` 时只是一句泛泛的请求。这句话的要旨（事件里「某人对某人请求：……」冒号后面的话）：写了 `ask` 的按所求之事拼；没写 `ask` 的在 `do` 里另写 `gist`（第三人称的一句，「对方」指听话的人，如 `gist: 劝对方罢手`），不写就只好借这一步的 `text`（那是打算的口气，读起来不像一句请求）。`do: {verb: follow, target: 人}` 的一步是持续的：到了那人身边先跟上，再跟满 `takes` 刻才算做完；跟的人离了图、死了，或自己被拿住，这一步作废。指名说给某人（`to`）的话，他不在跟前就不说：离场时这一步作废。图外的人第一步入场的写法：

```yaml
- id: envoy_enter
  text: 从图外赶到无量山
  do: {verb: wait}
  start: 288
  offscreen: [{enter: [self, mountain_path]}]
```

## situations/<id>.yaml

方案 5.7 的格式。另：

- `at`：情境发生在哪里。写地点 id，或写 `$角色位`（取那个人所在的地点）；不写就取第一个角色位所在的地点。
- 角色位写法：写死一个 id；或 `{pick: {where: 谓词}}`，在那个地点的活人里挑第一个使 `where` 成立的人，谓词里用 `$it` 指候选人，可以引用排在前面、已经绑定的角色位。
  `pick` 里可另写 `prefer: 谓词`：合 `where` 的人里先取也合 `prefer` 的（例：自告奋勇的人），没有才取第一个。
- `volunteer`（可选）：这场事里可以自告奋勇顶上的角色位（方案 3.4 例三的「由我来」）。`{role: 用 pick 挑人的角色位, to: 写死的角色位（向他开口）, grant: 身份?, cls: 类别?, urgent: true?, desc: 所求之事的说法, keys: [关键字…]}`。情境的 `when` 成立（那场事眼下就要发生）时，解析器给玩家列出这件可求的事（`desc` 里 `{volunteer}` 指请缨的人，其余 `{角色位}` 只能用写死的）；所求写成「对方等一等」带 `volunteer: {situation, role}` 说明，内核照常裁决（`cls` 是这件事对答应的人算哪一类，缺省 `effort`；`urgent` 即「正好帮到对方的急需」+30；`when` 不成立时裁决为 never）。对方答应了，请缨的人得到 `grant` 这个身份——角色位的 `pick`（`where` / `prefer`）要认它；用过之后在情境的效果里把这个身份去掉。
- 旗标以 `.` 开头的是情境局部旗标（见 `ARCHITECTURE.md` 2.1）。
- 情境动作 `actions.<name>` 的字段：`role`、`lean`、`pre`、`loud`、`lethal`、`cost`（默认 1）、`desc`（给模型看的一句话，可用 `{角色位}`）、`effects`。
  - `desc` 以做这一招的角色位开头（`'{guest}拱手赔个不是'`）：玩家自己的招写成选项时去掉主语（「拱手赔个不是」），劝别人出这一招时写成「低声劝某某拱手赔个不是」。不以角色位开头的写不成一句劝人的话，不会出现在「相劝」的选项里。
  - `lean: true` 的一次性招式要在 `pre` 里写明「这一拍还没演过」（它自己立的旗标），否则演过之后仍标 ★。
  - 没有 `lean`、不伤人（效果里没有 `injure`、`kill`、扣人或下毒的状态）、此刻做得出的招，会作为「相劝」的开口选项给玩家；效果里让玩家自己对人生出恶感（`rel` 第一个参数是玩家所在的角色位、数值为负）的招不给。
  - 同一微轮里，效果立了散场条件（`exit_when`）里看的旗标的那一招排在同场其他人的招之后结算。
- `title`、`chronicle_open`、`chronicle_close`（都可用 `{角色位}`，只写看得见的事，不预告结果）：`title` 是牵挂里这场事的说法（「某某在场中比剑，某某在一旁看着」）；后两句是年谱里开场与了结各记的一句。没写年谱句的借 `title`，都没写才用通用句。
- 判定效果 `check` 可加 `adverse: true`：这是一次「成了反而坏事」的判定（看押的人趁隙逃脱之类），走运的基线（方案 11.3）下取「不成」。

## opening.yaml

```yaml
player:
  id: player
  default_name: 阿福
  unknown_alias: 茶商伙计              # 别人不知你名时的叫法
  base_power: 0
  neili: 0
  literacy: 1
  aptitude: 1
  luck: 5
  roles: [ma_caravan_hand]
  loc: jianhu_hall
  superior: ma_wude
  knows: [F_wuliang_two_sects]
  arts: {trade: 1}
  rel: {ma_wude: {trust: 40, affinity: 30}, duan_yu: {trust: 10, affinity: 15}}
known_terms: [ma_wude, jianhu_hall]   # 玩家开局就知道名字的实体
term_alias: {duan_yu: 段公子}          # 玩家开局对某人的叫法（只知其姓）
chars:                                 # 有卡的人开局在哪；不在图内的写 onstage: false
  duan_yu: {loc: jianhu_hall}
  envoy_lingjiu: {loc: mountain_path, onstage: false}
extras:                                # 路人：没有卡，按 routines.yaml 行动
  - {id: sn_guard_1, name: 神农帮帮众, unknown_alias: 一个短衣汉子, routine: shennong_guard, loc: shennong_camp, base_power: 1, roles: [shennong_member], superior: sikong_xuan}
flags: {bout_running: true}
scheduled: []
threads: []                            # 玩家开局的牵挂
```

## baseline.yaml

方案 8.6 的格式：`[{id, text, holds, by}]`。

## goals.yaml、routines.yaml、opportunities.yaml

格式由实现它们的模块定，写在各文件开头的注释里：`goals.yaml` 由 `world/goals.py`（重新打算的候选：所求 × 处境 → 目标与步骤模板，绑定方法 `captor_of`、`cure_for`、`safe_place`、`away_from`、`pick` 等），`routines.yaml` 由 `minds/routines.py`，`opportunities.yaml` 由 `storyteller/opportunities.py`。方案 8.3、9.4、8.5 给了种子。

`goals.yaml` 另有：绑定方法 `fact_subject: {tag, where}`（他相信的、带这个标签的事实里提到的人）、`threat_here: self`（身边怀敌意而且真有威胁的人：比他强，或眼下的打算正冲着他来）；规则键 `pressing: true`（等不得的事：这条规则成立时候选里不给「照旧」——规则与 Mock 模式下由事件触发的重新打算否则总是选「照旧」）、`cooldown: 刻数`（选中过之后隔这么久才再进候选）。

`safe_place` / `away_from` 挑的是最近的、没有人对他怀敌意的别处，并避开对头正要去的地方；两个互相敌视的人同时要躲时不会挑中同一处。

## 效果的补充（方案 5.3 之外）

内核另认这些效果（形状见 `kernel/effects.py` 的 `EFFECT_SHAPES`，校验器按它查）。前一组可以写在内容里（开局布景、测试夹具），后一组是程序记账用的，内容里不要写。

| 效果 | 含义 |
| --- | --- |
| `{request: {frm?, to, kind?, action: {verb, params}, action_class?, evidence?, leverage?, status?, condition?, id?}}` | 建一桩请求（`kind`：`request` / `plead` / `order`；`question` 时写 `about`）。`status: agreed` 即当场答应 |
| `{superior: [人, 上司 \| null]}` | 此人从此听命于谁 |
| `{fact: {id, text, tags?, gossip?}}` | 添一条事实 |
| `{step: [人, 一步]}`、`{agenda: [人, 打算]}` | 往打算最前面插一步；换掉整份打算 |
| `{diverge: …}`、`{thread: …}`、`{close_thread: …}`、`{chronicle: …}`、`{teller: [键, 值]}` | 分岔、牵挂、年谱、讲述者的簿记（world / storyteller 用） |
| `{turn: n}`、`{offer: [...]}`、`{dequeue: 人}`、`{mark_char: [人, 字段, 值?]}`、`{progress: [人, 刻]}` | 回合数、当前的三个选项、重新打算的队列、人物的记账字段、「等」的进度（game / world 用） |

## config.toml 的 `[route]`

不属于 `content/`，但跑内容时常要改：每类调用走哪条路，取 `jev_first` / `claude_first` / `claude_only` / `rules_only`。

| 键 | 调用 | 说明 |
| --- | --- | --- |
| `classify` | J1 → C1 | 玩家输入的分类 |
| `decide_soul`、`decide_named` | J2 → C2 | 有魂、有名人物的抉择 |
| `replan` | J3 → C5 | 重新打算 |
| `options` | J4 | 三选项的权衡 |
| `check` | J5 | 叙述的逐句核对 |
| `cards` | J6 | 机会牌 |
| `narrate` | C3 | 叙述；只有 Claude 一条路，`rules_only` 时全用模板句 |
| `phrase` | C4 | 选项措辞；同上 |
| `pipeline` | P1…P6 | 离线管线；`rules_only` 时只跑不用模型的步骤（P0、P7） |

## style/

- `guide.md`：用自己的话写的文风要点（方案 9.7）。
- `samples.yaml`：`{场面类型: [{text, para}]}`。只收原文的子串，由管线 P6 生成；没有原文时为空。
