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
salience: {death: 10, harmed: 9, rescue: 8, promise: 7, exposed: 7, insult: 6, learn_key: 6, gift: 5, talk: 3, pass: 1}
```

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
| `on_use: [效果]` | `use` 时执行；`self` 指使用者，`$target` 指对象 |
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

## arts.yaml

方案 6.12 的格式。另加 `secrecy`（`none` 默认 / `sect`）与 `teacher_roles`（谁算得上能教，例如 `[wuliang_disciple]`）。

## lexicon.yaml

```yaml
terms:                  # 不属于任何实体、却要管住的专名
  lingjiu: {name: 灵鹫宫, alias: 一个叫人害怕的去处}
banned_modern: [手机, 电脑, …]
banned_game: [血量, 经验值, 等级, 技能点, 选项, NPC, …]
```

加载器把人物、地点、物件、本事的 `name` 与 `unknown_alias` 自动并入专名表。

## characters/<id>.yaml

方案 5.4 的格式（有魂角色完整，有名角色简卡）。另：

- `inventory` 不写在卡里，写在 `items.yaml` 的 `holder`。
- `rel: {other_id: {trust, affinity, fear, regard}}`，没写的按陌生人（全 0）。
- `persona` 自动取文件名。
- 底线 `bottom_lines[].blocks[]`：`{verb, act?, fact_tag?, target?, when?}`。命中（动词相同、写了的键都匹配、`when` 成立）的动作从此人的清单里去掉。

## agendas/<id>.yaml

方案 5.7 的格式。一步的 `do` 里可写的键：`verb`，以及 `target`、`to`、`item`、`exit`、`feature`、`art`、`act`、`fact`。图外的人第一步入场的写法：

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
- 旗标以 `.` 开头的是情境局部旗标（见 `ARCHITECTURE.md` 2.1）。
- 情境动作 `actions.<name>` 的字段：`role`、`lean`、`pre`、`loud`、`lethal`、`cost`（默认 1）、`desc`（给模型看的一句话，可用 `{角色位}`）、`effects`。

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

格式由实现它们的模块定：`goals.yaml` 由 `world/replan.py`，`routines.yaml` 由 `minds/routines.py`，`opportunities.yaml` 由 `storyteller/opportunities.py`。方案 8.3、9.4、8.5 给了种子。

## style/

- `guide.md`：用自己的话写的文风要点（方案 9.7）。
- `samples.yaml`：`{场面类型: [{text, para}]}`。只收原文的子串，由管线 P6 生成；没有原文时为空。
