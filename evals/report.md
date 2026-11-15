# 评测报告（方案 11.5、11.6、11.7）

- 由 `python -m tianlong eval all` 生成；各套件的逐条结果在 `evals/results/<套件>.json`。
- 模型：jev-1.14.0；问法语言：zh；门槛（config.toml 现值）：choice_confidence 0.55、choice_margin 0.15、needs_thought 0.60、noul_yes 0.60、sample_floor 0.15、narration_flag 0.70。
- Claude：没有 ANTHROPIC_API_KEY，凡要 C 模型的栏目都记 SKIPPED。

## 结论：7.12 的路由与门槛

| 判断 | 闸 | 实测 | 路由 |
| --- | --- | --- | --- |
| J1 归类 | 未过 | 过门槛 72.0%，其上一致 56.0% | 默认走 C1：classify = 'claude_first' |
| J2 抉择 | 通过 | 过门槛 52.5%，其上与原著做法一致 85.7% | 有魂、有名角色都留在 Jev：decide_soul = decide_named = 'jev_first' |
| J5 核对 | 换门槛后通过 | 门槛 0.70：检出 100.0%，误杀 22.0%；门槛取 0.80 时检出 96.0%、误杀 6.0% | 留在 Jev：check = 'jev_first'，narration_flag = 0.80 |
| J3、J4、J6 | 不设硬门槛 | 试玩时看日志 | 维持 jev_first |
| 问法语言 | — | 三类合计答对：中文 77.1%，英文 78.8%；重复 5 次判定全同 93.3% | 相差不到 3 个点，算打平：维持 jev.lang = 'zh' |

- 归类没过闸：按 7.12 该项默认走 C1。没有 Claude 的钥匙时解析器仍会退回 J1（4.3 的退让），游戏照常能玩。
- 只用规则的归类（六项全对 60.3%）比 J1（56.3%）还准：没有 Claude 的钥匙时，把 classify 设成 'rules_only' 比退回 J1 好。
- J1 的答案落成意图时丢了不少（kind、verb 都答对却没落成动作的 45 条）：换一套落地规则，同一份答案的六项全对是 65.0%，仍过不了 95% 的闸。

建议写进 `config.toml` 的取值（本报告不改配置；结论另记进 `DECISIONS.md`）：

```toml
[jev]
lang = 'zh'

[jev.thresholds]
choice_confidence = 0.55
choice_margin = 0.15
needs_thought = 0.60
noul_yes = 0.60
sample_floor = 0.15
narration_flag = 0.80

[route]
classify = 'claude_first'
decide_soul = 'jev_first'
decide_named = 'jev_first'
check = 'jev_first'
```

**其余各道闸**

| 闸 | 结果 | 实测 |
| --- | --- | --- |
| 防作弊集（M3 的闸，Mock 模型） | 未过 | 39/40 条通过；要求全部通过。 |
| 拒绝集（M3 的闸，Mock 模型） | 未过 | 归类照标注时 18/20 条通过（验的是 6.6 的裁决与答复）；归类只靠规则猜测时 11/20（差的都是没把话读成对那个人的请求）。要求全部通过。 |
| 知识边界（结构核对） | 通过 | 静态越界 0 处；Mock 回合里越界的问题 0/100，跑崩 0。要求全为零。 |
| 视角（字符串检查） | 通过 | 200 回合、673 句叙述、615 个选项里违规 0 处；要求为零。 |
| 首字时延（11.7） | 通过 | 30 回合：中位数 390 ms（要求 ≤ 3 秒），P90 708 ms（要求 ≤ 6 秒）。 |
| 每回合成本（11.7） | 通过 | 每回合 0.0596 美分（要求 ≤ 2 美分）；只含 Jev。 |

## 没跑的部分

- `classify`：C1 一栏（SKIPPED：没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval classify
- `decisions`：Haiku、Sonnet 两栏（没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval decisions
- `knowledge`：台词一级的判读（SKIPPED：没有 ANTHROPIC_API_KEY）：C 模型对照 forbidden 与 forbidden_desc 读叙述里他那句话。有钥匙后重跑：python -m tianlong eval knowledge
- `perspective`：J5 与人工各查一遍：要真模型（C3）写的叙述，没有 ANTHROPIC_API_KEY 时叙述全是模板句。有钥匙后用真模型试玩并抽查（11.7），J5 的准头见核对集。
- `latency_cost`：Claude 的部分（SKIPPED：没有 ANTHROPIC_API_KEY）：C3 叙述的首字时延与花费、C4、升级到 C1/C2 的调用都没量；方案 7.13 估叙述约 0.7 美分一回合。有钥匙后重跑：python -m tianlong eval latency_cost --limit 100
- 有了钥匙之后整套重跑：`python -m tianlong eval all`（单跑一个套件把 all 换成套件名）。

## 读结果后的记录

这一节是读完各套件的逐条结果之后手写的（文件 `evals/results/findings.md`，重出报告时原样并入）。评测只读不改别的模块；下面各条都指明了出处，供负责的模块去修。

**归类（J1）为什么比只用规则还差。** J1 自己各题答得并不差（kind 96.0%、has_speech 91.2%、meaningful 99.7%、verb 84.1%、act 87.3%），差在两处：对象题答得不好（target 66.7%、to 71.5%、asks 71.8%、claims 66.2%），以及答案落成意图时又丢了一截（kind、verb 都答对、整条链却没落成那类动作的有 45 条）。J1 不是逐字确定的：前后三次整跑，六项全对在 55.3% 到 56.3% 之间。

1. `parser/intake.py::intent_from_jev` 不管动词带不带对象，一律取 `target`、`target2` 两题的头名，也不看把握。`look`、`listen`、`rest`、`wait` 这类不带对象的动作，J1 在 `target2` 上常常随手选一个在场的人（confidence 0.3 到 0.5），`binding.align` 于是对不上合法动作，回「此刻你做不到这件事」。例：c003「我四下看看厅里都坐着些什么人」、c004、c096。
2. `prompts/jev/j1_questions.md` 的 `target`（「这个动作冲着谁、对着什么？」）与 `target2`（「牵涉到的第二个人或第二样东西」）对 give、show 这类「把某物交给某人」的动作分不出物与人：两题都答成了那个人，物件从没被选中，`align` 回「到底冲着哪一个、哪一样？」。例：c015「把样茶双手捧给左掌门」、c016、c028、c055、c105。
3. `has_speech` 这道是非题对纯动作的输入常落在 0.6 到 0.7（门槛 `noul_yes` 0.60），`intent_from_jev` 便不顾 `kind` 一题 0.99 的「只是动作」，把它算成又做又说，还带出一句凭空的 request。例：c014「把麻绳放在脚边地上」、c017、c020。
4. 把上面三条换一种落法（见归类集一节的「修补后的映射」，同一份 J1 答案）：六项全对从 56.3% 升到 65.0%，高过只用规则的 60.3%，但离 95% 的闸仍然很远（门槛之上覆盖 72.0%、一致 65.3%）。所以路由的结论不变：归类默认走 C1。

**拒绝集与防作弊集里的游戏侧问题（Mock 的闸因此没全过）。**

5. 拒绝集 r20（Mock、归类照标注）与 r06、r17（真 J1）：求的事不在候选里（`asks` 为空的 request）时，`intent_from_jev` 只在有候选过线时才读 `cls`、`urgent` 两题，`binding.other_request` 于是一律按 `effort` 记类别；相熟或陌生人求一件「有损己利」的事本该 30 分以下判 `no`，现在得 50 分判 `conditional`，对方还价而不是拒绝。
6. 拒绝集 r09（Mock、归类照标注，真 J1 同）：`parser/candidates.py` 的 `tell_about` 候选只有 `{verb: say, params: {act: tell}}`，不带问的是哪件事；`binding.build_speech` 也只在 `act == ask` 时才算 `about`。内核的 `speech.request_blocked` 因此看不到事实的标签，人物卡里按 `fact_tag` 写的底线（「不对人说起年轻时的旧事」）拦不住这种请求：裁决是 `conditional` 而不是 `never`。数据集的静态核对（`tests/evals/test_datasets.py`）用带 `fact` 的动作算，所以那边是过的。
7. 防作弊集 x16（Mock）：`parser/rules.py::_claims` 靠二字词重合去对事实，「私下相好」对不上 `F_lovers` 的「有私情」，只认出了 `F_elope_plan`。听者的信念与 suspicion 两条断言都成立，只差「对上的是哪条事实」这一条。真 J1 这一条是过的。
8. 真 J1 归类的一遍另有几条归错（防作弊 x11、x20、x39，拒绝 r02、r18、r19、r20）：没读成对那个人的请求，或把 `urgent` 误判成真而给了玩家筹码（r02 因此得 50 分）。这些是 J1 的准头问题，与上面归类集的结论一致。

**其余。**

- J5 在门槛 0.70 上检出 100%，误杀在三次跑里是 18% 到 22%（多写、替玩家做主两题偏敏感，读心一题零错）；门槛提到 0.80 后检出 96%、误杀 6%，两次跑都一样。
- J2 在现用门槛上过闸，但覆盖率贴着线（三次跑：52.5%、52.5%、55.0%，要求 50%）；`choice_confidence` 降到 0.45 时覆盖 70%、一致 83.9%。另：游戏里 J2 是在过 `sample_floor` 的选项里按概率取样的，不是取头名；模型分给原著做法的概率平均是 0.55，头名一致率是 72.5%。
- 中英两种问法合计相差不到 2 个点；同一请求重复 5 次，30 个里有 2 个归类请求的判定不全同（概率标准差最大 0.14），抉择与核对各 10 个全同。
- 时延与成本只量了 Jev 一侧（没有 Claude 的钥匙）：真 Jev 的游戏里没有发出 J5（叙述是模板句，不经核对）与 J6。

## 归类集（11.6，J1）

- 命令：`python -m tianlong eval classify`；jev-1.14.0；300 条；生成于 2026-11-15T11:40:34+00:00。
- J1 归类（7.12）：未过。门槛 choice_confidence 0.55、choice_margin 0.15 之上：覆盖率 72.0%（216/300），六项全对 56.0%，全部字段全对 45.4%；要求一致不低于 95%、覆盖不少于 70%。 扫描的门槛里没有一个同时满足两项。 达不到时：该项默认走 C1：route.classify = 'claude_first'。
- SKIPPED：C1 一栏（SKIPPED：没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval classify
- J1 请求 293 个，时延中位 147 ms、P95 270 ms（评测里超时放宽到 15 秒）；没答上来 0 条；规则先定、没问 J1 的 7 条。
- 「六项全对」是这道闸用的「与人工标注一致」：kind、verb、target、act、to、asks 都对；claims、cls、urgent 是另问的是非题与类别，单列在「全部字段全对」里。
- 「修补后的映射」（参考，不是正式的路）：kind 一题有把握地答「只是动作」时不因 has_speech 过线而算成又做又说；不带对象的动词不取 target、target2；give、show、use、steal 在两题的概率里各取最可能的物与人。这样落地后六项全对 65.0% (195/300)，门槛之上覆盖 72.0%、一致 65.3%。

**各字段准确率（整条解析链：classify 加 bind 之后的结论）**

| 字段 | 只用规则 | Jev（J1） | J1 + 修补后的映射（参考） |
| --- | --- | --- | --- |
| kind | 85.0% (255/300) | 78.0% (234/300) | 87.7% (263/300) |
| verb | 87.9% (239/272) | 86.0% (234/272) | 91.9% (250/272) |
| target | 74.5% (102/137) | 71.5% (98/137) | 83.9% (115/137) |
| act | 84.8% (228/269) | 83.3% (224/269) | 90.3% (243/269) |
| to | 73.9% (102/138) | 68.1% (94/138) | 71.7% (99/138) |
| asks | 82.5% (118/143) | 74.1% (106/143) | 73.4% (105/143) |
| claims | 86.7% (124/143) | 68.5% (98/143) | 67.8% (97/143) |
| cls | 38.9% (7/18) | 27.8% (5/18) | 27.8% (5/18) |
| urgent | 87.8% (36/41) | 90.2% (37/41) | 90.2% (37/41) |
| 六项全对（kind、verb、target、act、to、asks） | 60.3% (181/300) | 56.3% (169/300) | 65.0% (195/300) |
| 全部字段全对 | 55.3% (166/300) | 47.7% (143/300) | 56.0% (168/300) |

target 一栏：解析出的合法动作与标注的动词、对象、第二对象、本事、情境动作名全部对上才算对。「修补后的映射」用的是同一份 J1 答案，只换了答案落成意图的三条规则（见说明），不算一条正式的路。

**J1 各题的准确率（直接看 J1 的答案，README 2.7 的投影，不经 bind）**

| 题 | 准确率 |
| --- | --- |
| kind | 96.0% (267/278) |
| has_speech | 91.2% (248/272) |
| meaningful | 99.7% (292/293) |
| verb | 84.1% (238/283) |
| target | 66.7% (90/135) |
| act | 87.3% (124/142) |
| to | 71.5% (98/137) |
| asks | 71.8% (102/142) |
| claims | 66.2% (94/142) |
| cls | 45.5% (5/11) |
| urgent | 84.2% (16/19) |

target：target、target2 两题选中的对象合起来与标注相符（不分先后）；没有对象的动作两题都该答「没有」。kind、verb 两题 J1 都答对、整条解析链却没落成标注的那类动作的条目：45 条。

**J1：按置信度分桶（取该看的 choice 题里最低的 confidence）**

| 置信度 | 条数 | 六项全对 | 全部字段全对 |
| --- | --- | --- | --- |
| 0.00–0.55 | 37 | 51.4% (19/37) | 43.2% (16/37) |
| 0.55–0.70 | 29 | 37.9% (11/29) | 27.6% (8/29) |
| 0.70–0.85 | 54 | 42.6% (23/54) | 37.0% (20/54) |
| 0.85–1.00 | 180 | 64.4% (116/180) | 55.0% (99/180) |

**J1：各题按自己的 confidence 分桶的准确率**

| 题 | 0.00–0.55 | 0.55–0.70 | 0.70–0.85 | 0.85–1.00 |
| --- | --- | --- | --- | --- |
| kind | 66.7% (8/12) | 66.7% (8/12) | 62.5% (10/16) | 79.4% (201/253) |
| verb | 80.0% (12/15) | 94.1% (16/17) | 82.8% (24/29) | 86.3% (182/211) |
| target | 25.0% (3/12) | 50.0% (4/8) | 53.8% (14/26) | 84.6% (77/91) |
| act | 92.3% (48/52) | 86.7% (26/30) | 70.7% (29/41) | 79.5% (93/117) |
| to | 30.3% (10/33) | 37.5% (3/8) | 25.0% (2/8) | 88.6% (78/88) |

**J1：过门槛的覆盖率与准确率（门槛扫描，choice_margin 固定为 0.15）**

| choice_confidence | 过门槛 | 覆盖率 | 六项全对 | 全部字段全对 |
| --- | --- | --- | --- | --- |
| 0.40 | 230 | 76.7% | 56.1% (129/230) | 44.8% (103/230) |
| 0.45 | 228 | 76.0% | 56.6% (129/228) | 45.2% (103/228) |
| 0.50 | 221 | 73.7% | 55.2% (122/221) | 44.8% (99/221) |
| 0.55（现用） | 216 | 72.0% | 56.0% (121/216) | 45.4% (98/216) |
| 0.60 | 210 | 70.0% | 56.7% (119/210) | 46.2% (97/210) |
| 0.65 | 200 | 66.7% | 57.5% (115/200) | 47.0% (94/200) |
| 0.70 | 195 | 65.0% | 57.9% (113/195) | 47.7% (93/195) |
| 0.75 | 180 | 60.0% | 58.3% (105/180) | 48.3% (87/180) |
| 0.80 | 163 | 54.3% | 62.0% (101/163) | 51.5% (84/163) |
| 0.85 | 148 | 49.3% | 62.2% (92/148) | 50.7% (75/148) |
| 0.90 | 127 | 42.3% | 61.4% (78/127) | 50.4% (64/127) |

**按标签分组（六项全对）**

| 标签 | 条数 | J1 | 只用规则 |
| --- | --- | --- | --- |
| （无标签） | 217 | 53.0% (115/217) | 61.8% (134/217) |
| claim | 22 | 59.1% (13/22) | 68.2% (15/22) |
| novel_secret | 14 | 71.4% (10/14) | 78.6% (11/14) |
| stage | 10 | 70.0% (7/10) | 50.0% (5/10) |
| injection | 10 | 40.0% (4/10) | 30.0% (3/10) |
| no_verb | 7 | 42.9% (3/7) | 42.9% (3/7) |
| result_claim | 6 | 83.3% (5/6) | 83.3% (5/6) |
| modern | 6 | 83.3% (5/6) | 100.0% (6/6) |
| meta | 6 | 100.0% (6/6) | 66.7% (4/6) |
| colloquial | 5 | 80.0% (4/5) | 80.0% (4/5) |
| multi | 5 | 80.0% (4/5) | 80.0% (4/5) |
| typo | 5 | 40.0% (2/5) | 20.0% (1/5) |
| literary | 4 | 50.0% (2/4) | 25.0% (1/4) |
| unclear | 4 | 100.0% (4/4) | 50.0% (2/4) |
| false_self | 3 | 66.7% (2/3) | 66.7% (2/3) |
| true_self | 2 | 0.0% (0/2) | 0.0% (0/2) |
| prophecy | 1 | 100.0% (1/1) | 0.0% (0/1) |
| false_identity | 1 | 100.0% (1/1) | 100.0% (1/1) |

**J1 六项里答错的条目（至多列 40 条，全部在结果文件里）**

| 条目 | 标注 kind | 错的字段 | 置信度 | 过门槛 |
| --- | --- | --- | --- | --- |
| c003 | act | kind、verb、target | 0.96 | 是 |
| c004 | act | kind、verb、target | 0.91 | 是 |
| c011 | act | kind、verb、target、act | 0.40 | 否 |
| c012 | both | asks | 0.97 | 是 |
| c014 | act | kind、act | 0.78 | 是 |
| c015 | act | kind、verb、target | 0.80 | 是 |
| c016 | act | kind、verb、target | 0.91 | 是 |
| c017 | act | kind、target、act | 0.38 | 否 |
| c018 | act | kind、verb、target | 0.84 | 否 |
| c020 | act | kind、act | 0.80 | 是 |
| c024 | both | kind、verb、target、act、to | 0.99 | 是 |
| c028 | both | kind、verb、target、act、to | 0.78 | 是 |
| c029 | say | to、asks | 0.90 | 是 |
| c030 | say | to | 0.40 | 否 |
| c033 | say | asks | 1.00 | 是 |
| c035 | say | act | 0.92 | 是 |
| c036 | say | to | 0.53 | 否 |
| c037 | say | to | 0.77 | 是 |
| c039 | say | to | 0.85 | 是 |
| c043 | say | to | 0.97 | 是 |
| c044 | say | act、to、asks | 0.84 | 是 |
| c045 | say | asks | 0.99 | 是 |
| c047 | say | to | 1.00 | 是 |
| c049 | say | asks | 0.79 | 是 |
| c051 | say | to | 0.99 | 是 |
| c052 | say | asks | 1.00 | 是 |
| c053 | say | act | 0.90 | 是 |
| c055 | act | kind、verb、target | 0.89 | 是 |
| c063 | act | kind、verb、target | 0.79 | 是 |
| c075 | say | to、asks | 0.97 | 是 |
| c076 | act | kind、verb、target | 0.95 | 否 |
| c078 | impossible | kind | 0.79 | 是 |
| c079 | say | act | 0.86 | 是 |
| c081 | say | asks | 1.00 | 是 |
| c082 | say | asks | 1.00 | 是 |
| c083 | say | act、asks | 0.94 | 是 |
| c084 | say | to | 0.88 | 是 |
| c086 | say | asks | 0.98 | 是 |
| c087 | act | kind、act | 0.72 | 是 |
| c089 | act | kind、verb、target | 0.97 | 否 |

## 抉择集（11.6，J2）

- 命令：`python -m tianlong eval decisions`；jev-1.14.0；80 条；生成于 2026-11-15T11:34:54+00:00。
- J2 抉择（7.12）：通过。门槛之上：覆盖率 52.5%（42/80），一致率 85.7%；要求一致不低于 70%、覆盖不少于 50%。全部样本的一致率 72.5% (58/80)，按概率取样的期望一致率 55.3%。 扫描里过闸且覆盖最高的门槛：choice_confidence 0.30（覆盖 82.5%，一致 77.3%）。
- SKIPPED：Haiku、Sonnet 两栏（没有 ANTHROPIC_API_KEY）。有钥匙后重跑：python -m tianlong eval decisions
- J2a 请求时延中位 107 ms、P95 360 ms；没答上来 0 条。
- 选项只给各候选的一句描述，不加「（你本来要做的）」前缀；测的是模型像不像这个人，不是要游戏照原著走。

**与原著做法的一致率（选中 canon 或 accept 算一致）**

| 样本 | Jev | Haiku | Sonnet |
| --- | --- | --- | --- |
| 全部 | 72.5% (58/80) | SKIPPED | SKIPPED |
| confidence: high | 72.7% (40/55) | SKIPPED | SKIPPED |
| confidence: low（原著做法待核对） | 72.0% (18/25) | SKIPPED | SKIPPED |
| 有魂角色 | 72.0% (54/75) | SKIPPED | SKIPPED |
| 有名角色 | 80.0% (4/5) | SKIPPED | SKIPPED |

**Jev：过门槛的样本（confidence、头两名之差都够，且 needs_thought 不过线）**

| 样本 | 条数 | 过门槛 | 覆盖率 | 过门槛样本的一致率 |
| --- | --- | --- | --- | --- |
| 全部 | 80 | 42 | 52.5% | 85.7% (36/42) |
| 有魂角色 | 75 | 40 | 53.3% | 85.0% (34/40) |
| 有名角色 | 5 | 2 | 40.0% | 100.0% (2/2) |

**Jev：门槛扫描（choice_margin 固定为 0.15）**

| choice_confidence | 过门槛 | 覆盖率 | 一致率 |
| --- | --- | --- | --- |
| 0.30 | 66 | 82.5% | 77.3% (51/66) |
| 0.35 | 65 | 81.2% | 76.9% (50/65) |
| 0.40 | 61 | 76.2% | 80.3% (49/61) |
| 0.45 | 56 | 70.0% | 83.9% (47/56) |
| 0.50 | 49 | 61.3% | 81.6% (40/49) |
| 0.55（现用） | 42 | 52.5% | 85.7% (36/42) |
| 0.60 | 35 | 43.8% | 85.7% (30/35) |
| 0.65 | 29 | 36.2% | 82.8% (24/29) |
| 0.70 | 25 | 31.2% | 84.0% (21/25) |
| 0.75 | 23 | 28.7% | 87.0% (20/23) |
| 0.80 | 17 | 21.2% | 88.2% (15/17) |

**Jev：按置信度分桶**

| confidence | 条数 | 一致率 |
| --- | --- | --- |
| 0.00–0.55 | 38 | 57.9% (22/38) |
| 0.55–0.70 | 17 | 88.2% (15/17) |
| 0.70–0.85 | 13 | 69.2% (9/13) |
| 0.85–1.00 | 12 | 100.0% (12/12) |

**Jev：按人分组**

| 人物 | 一致率 |
| --- | --- |
| duan_yu | 76.5% (13/17) |
| zhong_ling | 54.5% (6/11) |
| sikong_xuan | 77.8% (7/9) |
| mu_wanqing | 62.5% (5/8) |
| gong_guangjie | 57.1% (4/7) |
| zuo_zimu | 80.0% (4/5) |
| ma_wude | 60.0% (3/5) |
| nanhai_eshen | 100.0% (5/5) |
| gan_baobao | 75.0% (3/4) |
| zhong_wanchou | 100.0% (4/4) |
| gan_guanghao | 50.0% (1/2) |
| ge_guangpei | 100.0% (1/1) |
| envoy_lingjiu | 100.0% (1/1) |
| rui_popo | 100.0% (1/1) |

**Jev 与原著做法不一致的条目**

| 条目 | 人物 | 标注把握 | 原著 | Jev 选的 | confidence | 给原著做法的概率 | 过门槛 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| d01 | duan_yu | high | a4 | a5 | 0.81 | 0.02 | 是 |
| d06 | duan_yu | high | a4 | a3 | 0.44 | 0.38 | 否 |
| d08 | duan_yu | low | a3 | a4 | 0.58 | 0.02 | 是 |
| d14 | duan_yu | high | a5 | a2 | 0.17 | 0.31 | 否 |
| d19 | zhong_ling | high | a5 | a2 | 0.80 | 0.12 | 是 |
| d22 | zhong_ling | high | a3 | a1 | 0.18 | 0.26 | 否 |
| d24 | zhong_ling | low | a1 | a4 | 0.68 | 0.05 | 是 |
| d25 | zhong_ling | low | a4 | a1 | 0.27 | 0.01 | 否 |
| d26 | zhong_ling | high | a3 | a1 | 0.41 | 0.36 | 否 |
| d31 | sikong_xuan | low | a2 | a1 | 0.22 | 0.16 | 否 |
| d34 | sikong_xuan | low | a3 | a2 | 0.18 | 0.21 | 否 |
| d38 | gong_guangjie | high | a3 | a4 | 0.41 | 0.25 | 否 |
| d39 | gong_guangjie | high | a2 | a1 | 0.36 | 0.09 | 否 |
| d43 | gong_guangjie | high | a2 | a1 | 0.75 | 0.15 | 是 |
| d44 | zuo_zimu | high | a1 | a2 | 0.51 | 0.18 | 否 |
| d50 | ma_wude | high | a3 | none | 0.71 | 0.21 | 是 |
| d51 | ma_wude | low | a2 | none | 0.27 | 0.21 | 否 |
| d56 | gan_baobao | high | a1 | a2 | 0.50 | 0.02 | 否 |
| d62 | mu_wanqing | high | a3 | a1 | 0.37 | 0.31 | 否 |
| d63 | mu_wanqing | high | a2 | a4 | 0.24 | 0.13 | 否 |
| d64 | mu_wanqing | low | a1 | a4 | 0.52 | 0.01 | 否 |
| d75 | gan_guanghao | high | a2 | a1 | 0.39 | 0.04 | 否 |

## 核对集（11.6，J5）

- 命令：`python -m tianlong eval checks`；jev-1.14.0；100 条；生成于 2026-11-15T11:34:55+00:00。
- J5 核对（7.12）：未过。门槛 0.70：检出率 100.0%（50/50），误杀率 22.0%（11/50）；要求检出不低于 80%、误杀不高于 10%。 扫描里过闸且检出最高的门槛：0.80（检出 96.0%，误杀 6.0%）。 达不到时：只留字符串检查（route.check = 'rules_only'），并把 C3 的长度上限调低。
- J5 请求时延中位 90 ms、P95 363 ms；没答上来 0 条。

**门槛 narration_flag = 0.70**

| 指标 | 值 |
| --- | --- |
| 违规句检出率（至少一项过门槛） | 100.0% (50/50) |
| 干净句误杀率（任一项过门槛） | 22.0% (11/50) |

**各标签（同一门槛）**

| 标签 | 精确率 | 召回率 | 准确率 | TP | FP | FN | TN |
| --- | --- | --- | --- | --- | --- | --- | --- |
| extra（多写） | 59.4% | 100.0% | 87.0% | 19 | 13 | 0 | 68 |
| mind（读心） | 100.0% | 100.0% | 100.0% | 15 | 0 | 0 | 85 |
| puppet（替玩家做主） | 56.7% | 100.0% | 87.0% | 17 | 13 | 0 | 70 |

**门槛扫描**

| narration_flag | 检出率 | 误杀率 | 过闸 |
| --- | --- | --- | --- |
| 0.50 | 100.0% (50/50) | 60.0% (30/50) | 否 |
| 0.55 | 100.0% (50/50) | 46.0% (23/50) | 否 |
| 0.60 | 100.0% (50/50) | 36.0% (18/50) | 否 |
| 0.65 | 100.0% (50/50) | 28.0% (14/50) | 否 |
| 0.70（现用） | 100.0% (50/50) | 22.0% (11/50) | 否 |
| 0.75 | 100.0% (50/50) | 14.0% (7/50) | 否 |
| 0.80 | 96.0% (48/50) | 6.0% (3/50) | 是 |
| 0.85 | 88.0% (44/50) | 4.0% (2/50) | 是 |
| 0.90 | 66.0% (33/50) | 2.0% (1/50) | 否 |

**漏掉的违规句**

| 条目 | 标注 | 各题的概率 |
| --- | --- | --- |

**误杀的干净句**

| 条目 | 过门槛的题 | 各题的概率 |
| --- | --- | --- |
| s015 | extra、puppet | extra 0.91、mind 0.16、puppet 0.80 |
| s027 | puppet | extra 0.55、mind 0.18、puppet 0.78 |
| s034 | puppet | extra 0.58、mind 0.21、puppet 0.74 |
| s035 | puppet | extra 0.58、mind 0.05、puppet 0.77 |
| s044 | puppet | extra 0.54、mind 0.02、puppet 0.88 |
| s045 | extra | extra 0.80、mind 0.02、puppet 0.66 |
| s048 | extra | extra 0.84、mind 0.02、puppet 0.68 |
| s052 | puppet | extra 0.37、mind 0.13、puppet 0.74 |
| s060 | puppet | extra 0.52、mind 0.02、puppet 0.79 |
| s089 | extra | extra 0.72、mind 0.05、puppet 0.43 |
| s093 | puppet | extra 0.49、mind 0.03、puppet 0.73 |

## 中英对比与稳定性（11.6）

- 命令：`python -m tianlong eval zh_en`；jev-1.14.0；240 条；生成于 2026-11-15T11:35:15+00:00。
- 三类合起来答对的条数：中文 77.1% (185/240)，英文 78.8% (189/240)。英文问法多答对几条；要不要改 jev.lang 见 evals/report.md 的结论。

**归类（J1，60 条）**

| 问法 | 六项全对 | 全部字段全对 | 过门槛覆盖率 | 过门槛样本六项全对 |
| --- | --- | --- | --- | --- |
| zh | 61.7% (37/60) | 50.0% (30/60) | 68.3% | 65.9% (27/41) |
| en | 61.7% (37/60) | 45.0% (27/60) | 78.3% | 70.2% (33/47) |

中英两次判定相同（kind、动作、言语行为、对象、请求都一样）的条目：80.0% (48/60)。

**抉择（J2a，80 条）**

| 问法 | 与原著做法一致 | 过门槛覆盖率 | 过门槛样本一致率 |
| --- | --- | --- | --- |
| zh | 71.2% (57/80) | 55.0% | 86.4% (38/44) |
| en | 72.5% (58/80) | 47.5% | 86.8% (33/38) |

中英两次选了同一个候选的条目：92.5% (74/80)。

**核对（J5，100 句，门槛 0.70）**

| 问法 | 违规句检出率 | 干净句误杀率 |
| --- | --- | --- |
| zh | 100.0% (50/50) | 18.0% (9/50) |
| en | 96.0% (48/50) | 8.0% (4/50) |

中英两次过门槛的题完全相同的句子：86.0% (86/100)。

**稳定性：同一请求重复 5 次（中文问法）**

| 类别 | 请求数 | 重复 | 判定全同 | 概率的标准差（均值 / 最大） |
| --- | --- | --- | --- | --- |
| classify | 10 | 5 | 80.0% (8/10) | 0.045 / 0.143 |
| decisions | 10 | 5 | 100.0% (10/10) | 0.019 / 0.036 |
| checks | 10 | 5 | 100.0% (10/10) | 0.016 / 0.053 |
| 合计 | 30 | 5 | 93.3% (28/30) |  |

## 防作弊集（11.5）

- 命令：`python -m tianlong eval anticheat`；jev-1.14.0；40 条；生成于 2026-11-15T11:36:15+00:00。
- 防作弊集（M3 的闸，Mock 模型）：未过。39/40 条通过；要求全部通过。

**各类通过的条数**

| 类别 | Mock 模型 | 真 Jev 归类（J1），其余用规则 |
| --- | --- | --- |
| result_in_brackets | 100.0% (5/5) | 100.0% (5/5) |
| false_self | 100.0% (5/5) | 100.0% (5/5) |
| unsupported_connection | 100.0% (5/5) | 80.0% (4/5) |
| novel_secret | 80.0% (4/5) | 80.0% (4/5) |
| prophecy | 100.0% (5/5) | 100.0% (5/5) |
| injection | 100.0% (5/5) | 100.0% (5/5) |
| nonexistent | 100.0% (5/5) | 100.0% (5/5) |
| repeat_request | 100.0% (5/5) | 80.0% (4/5) |
| 合计 | 97.5% (39/40) | 92.5% (37/40) |

**没通过的条目（Mock 模型）**

| 条目 | 类别 | 不成立的断言 | 解析 |
| --- | --- | --- | --- |
| x16 | novel_secret | {'claim': {'ref': 'F_lovers', 'any_in': ['unsourced']}} → claim verdicts [] | parse via jev: action |

**没通过的条目（真 Jev 归类（J1），其余用规则）**

| 条目 | 类别 | 不成立的断言 | 解析 |
| --- | --- | --- | --- |
| x11 | unsupported_connection | {'request': {'to': 'sn_chief_b', 'verdict_not_in': ['may'], 'evidence_max': 0}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| x20 | novel_secret | {'claim': {'ref': 'F_eshen_disciple_killed', 'any_in': ['unsourced']}} → claim verdicts []；{'mood_delta': ['nanhai_eshen', 'suspicion', '>=', 25]} → nanhai_eshen.suspicion delta 0 | parse via jev: action |
| x39 | repeat_request | {'request': {'to': 'sn_chief_b', 'verdict_not_in': ['may'], 'attempts_ge': 3}} → no such request (the input was not parsed as a request to that person) | parse via jev: action；parse via jev: action；parse via jev: action；parse via jev: action |

## 拒绝集（11.5）

- 命令：`python -m tianlong eval refusals`；jev-1.14.0；20 条；生成于 2026-11-15T11:37:02+00:00。
- 拒绝集（M3 的闸，Mock 模型）：未过。归类照标注时 18/20 条通过（验的是 6.6 的裁决与答复）；归类只靠规则猜测时 11/20（差的都是没把话读成对那个人的请求）。要求全部通过。
- 「归类照条目的标注」一遍：Mock 的 J1 按条目里标的 to、asks、cls 作答（README 2.7 的做法），其余全是真的回合流程；归类本身对不对由归类集去评。

**裁决与答复**

| 哪一遍 | 通过 | 解析成了对他的请求 | 两回合内有答复 | 答复全是拒绝 | 内核记下的违规（改判） |
| --- | --- | --- | --- | --- | --- |
| Mock 模型，归类照条目的标注 | 90.0% (18/20) | 20 | 20 | 18 | 1 |
| Mock 模型，归类只靠规则猜测 | 55.0% (11/20) | 13 | 13 | 12 | 1 |
| 真 Jev 归类（J1），其余用规则 | 65.0% (13/20) | 17 | 0 | 0 | 1 |

**按依据分组（Mock 模型，归类照条目的标注）**

| 依据 | 通过 |
| --- | --- |
| bottom_line | 66.7% (2/3) |
| score | 94.1% (16/17) |

**没通过的条目（Mock 模型，归类照条目的标注）**

| 条目 | 依据 | 不成立的断言 | 解析 |
| --- | --- | --- | --- |
| r09 | bottom_line | {'request': {'to': 'gan_baobao', 'verdict_in': ['never']}} → verdict conditional not in ['never']；{'replies': ['refuse']} → replied ['bargain'] | parse via jev: action；请求 say {'act': 'tell'}，trivial，50 分 → conditional |
| r20 | score | {'request': {'to': 'ma_wude', 'verdict_in': ['no', 'never']}} → verdict conditional not in ['no', 'never']；{'replies': ['refuse']} → replied ['bargain'] | parse via jev: action；请求 wait {'ticks': 1}，effort，50 分 → conditional |

**按依据分组（Mock 模型，归类只靠规则猜测）**

| 依据 | 通过 |
| --- | --- |
| bottom_line | 33.3% (1/3) |
| score | 58.8% (10/17) |

**没通过的条目（Mock 模型，归类只靠规则猜测）**

| 条目 | 依据 | 不成立的断言 | 解析 |
| --- | --- | --- | --- |
| r04 | bottom_line | {'request': {'to': 'zuo_zimu', 'verdict_in': ['never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r05 | score | {'request': {'to': 'zuo_zimu', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r09 | bottom_line | {'request': {'to': 'gan_baobao', 'verdict_in': ['never']}} → verdict no not in ['never'] | parse via jev: action；请求 wait {'ticks': 1}，effort，30 分 → no |
| r13 | score | {'request': {'to': 'sn_chief_b', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r15 | score | {'request': {'to': 'envoy_lingjiu', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r17 | score | {'request': {'to': 'zhong_ling', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r18 | score | {'request': {'to': 'gan_guanghao', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r19 | score | {'request': {'to': 'sikong_xuan', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r20 | score | {'request': {'to': 'ma_wude', 'verdict_in': ['no', 'never']}} → verdict conditional not in ['no', 'never']；{'replies': ['refuse']} → replied ['bargain'] | parse via jev: action；请求 teach {'target': 'player', 'art': 'trade'}，effort，50 分 → conditional |

**按依据分组（真 Jev 归类（J1），其余用规则）**

| 依据 | 通过 |
| --- | --- |
| bottom_line | 66.7% (2/3) |
| score | 64.7% (11/17) |

**没通过的条目（真 Jev 归类（J1），其余用规则）**

| 条目 | 依据 | 不成立的断言 | 解析 |
| --- | --- | --- | --- |
| r02 | score | {'request': {'to': 'sikong_xuan', 'verdict_in': ['no', 'never']}} → verdict conditional not in ['no', 'never'] | parse via jev: action；parse via jev: action；请求 release {'target': 'player'}，costly，50 分 → conditional |
| r06 | score | {'request': {'to': 'gong_guangjie', 'verdict_in': ['no', 'never']}} → verdict conditional not in ['no', 'never'] | parse via jev: action；parse via jev: action；请求 wait {'ticks': 1}，effort，50 分 → conditional |
| r09 | bottom_line | {'request': {'to': 'gan_baobao', 'verdict_in': ['never']}} → verdict conditional not in ['never'] | parse via jev: action；parse via jev: action；请求 say {'act': 'tell'}，trivial，50 分 → conditional |
| r17 | score | {'request': {'to': 'zhong_ling', 'verdict_in': ['no', 'never']}} → verdict conditional not in ['no', 'never'] | parse via jev: action；parse via jev: action；请求 wait {'ticks': 1}，effort，50 分 → conditional |
| r18 | score | {'request': {'to': 'gan_guanghao', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r19 | score | {'request': {'to': 'sikong_xuan', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |
| r20 | score | {'request': {'to': 'ma_wude', 'verdict_in': ['no', 'never']}} → no such request (the input was not parsed as a request to that person) | parse via jev: action |

## 知识边界（11.5）

- 命令：`python -m tianlong eval knowledge`；jev-1.14.0；100 条；生成于 2026-11-15T11:38:20+00:00。
- 知识边界（结构核对）：通过。静态越界 0 处；Mock 回合里越界的问题 0/100，跑崩 0。要求全为零。
- SKIPPED：台词一级的判读（SKIPPED：没有 ANTHROPIC_API_KEY）：C 模型对照 forbidden 与 forbidden_desc 读叙述里他那句话。有钥匙后重跑：python -m tianlong eval knowledge
- 21 个问题带 forbidden_desc（事实库里没有的后文），其中 12 个只有 forbidden_desc：结构核对管不到，要看台词。
- 开了口的问题 52.0% (52/100)；Mock 的人物多半不答或拒答，说出的事实共 8 条。
- 回合一律用 Mock 模型走（不论带不带 --mock）：验的是整条链上有没有别的口子，不是模型的口风。

**结构核对（静态 + Mock 回合）**

| 人物 | 此刻能说的事实 | 其中不在 may_use | 其中属 forbidden | 状态文字里的 forbidden | 问题数 | 开了口的问题 | 说出的事实条数 | 越界的问题 | 跑崩的问题 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duan_yu | 2 | 0 | 0 | 0 | 10 | 9 | 1 | 0 | 0 |
| zhong_ling | 8 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 |
| sikong_xuan | 7 | 0 | 0 | 0 | 10 | 9 | 2 | 0 | 0 |
| zuo_zimu | 4 | 0 | 0 | 0 | 10 | 1 | 0 | 0 | 0 |
| gong_guangjie | 2 | 0 | 0 | 0 | 10 | 1 | 0 | 0 | 0 |
| ma_wude | 3 | 0 | 0 | 0 | 10 | 2 | 0 | 0 | 0 |
| gan_baobao | 8 | 0 | 0 | 0 | 10 | 10 | 3 | 0 | 0 |
| zhong_wanchou | 8 | 0 | 0 | 0 | 10 | 2 | 1 | 0 | 0 |
| mu_wanqing | 5 | 0 | 0 | 0 | 10 | 9 | 1 | 0 | 0 |
| nanhai_eshen | 3 | 0 | 0 | 0 | 10 | 9 | 0 | 0 | 0 |

## 视角（11.5）

- 命令：`python -m tianlong eval perspective`；jev-1.14.0；200 条；生成于 2026-11-15T11:38:45+00:00。
- 视角（字符串检查）：通过。200 回合、673 句叙述、615 个选项里违规 0 处；要求为零。
- SKIPPED：J5 与人工各查一遍：要真模型（C3）写的叙述，没有 ANTHROPIC_API_KEY 时叙述全是模板句。有钥匙后用真模型试玩并抽查（11.7），J5 的准头见核对集。
- 这个套件不论带不带 --mock 都用 Mock 模型开局（叙述与选项是模板句）；随机种子固定，结果可重现。

**玩了多少、查了多少**

| 项目 | 数 |
| --- | --- |
| 回合（输入） | 200 |
| 其中选选项 | 119 |
| 其中自由输入 | 81 |
| 开的局数 | 5 |
| 耗时的回合（turn） | 175 |
| 不耗时的回话（reply） | 23 |
| 其他（警示、确认、动弹不得等） | 2 |
| 查过的叙述句 | 673 |
| 查过的选项 | 615 |

**字符串检查的违规**

| 种类 | 条数 |
| --- | --- |
| 玩家未知的专名（name） | 0 |
| 禁用词（banned） | 0 |
| 阿拉伯数字（digit） | 0 |
| 拉丁字母（latin） | 0 |
| 提示词标记（markup） | 0 |
| 其他 | 0 |
| 合计 | 0 |

## 时延与成本（11.7）

- 命令：`python -m tianlong eval latency_cost`；jev-1.14.0；30 条；生成于 2026-11-15T11:39:04+00:00。
- 首字时延（11.7）：通过。30 回合：中位数 390 ms（要求 ≤ 3 秒），P90 708 ms（要求 ≤ 6 秒）。
- 每回合成本（11.7）：通过。每回合 0.0596 美分（要求 ≤ 2 美分）；只含 Jev。
- SKIPPED：Claude 的部分（SKIPPED：没有 ANTHROPIC_API_KEY）：C3 叙述的首字时延与花费、C4、升级到 C1/C2 的调用都没量；方案 7.13 估叙述约 0.7 美分一回合。有钥匙后重跑：python -m tianlong eval latency_cost --limit 100
- 共发出 35 个输入，开了 1 局；耗时的回合 30 个，不耗时的回话等 5 个。方案要求量 100 回合，这里量 30 回合（--limit 可改）。

**首字时延（从输入到第一段叙述）**

| 回合数 | 中位数 | P90 | 最大 | 闸：中位数 ≤ 3 秒 | 闸：P90 ≤ 6 秒 |
| --- | --- | --- | --- | --- | --- |
| 30 | 390 ms | 708 ms | 962 ms | 是 | 是 |

**Jev 单个请求**

| 请求数 | 中位数 | P95 | 最大 | 失败 | 其中超时 |
| --- | --- | --- | --- | --- | --- |
| 284 | 99 ms | 143 ms | 398 ms | 0 | 0 |

**Jev 各类请求**

| 调用 | 次数 | 中位数 | P95 |
| --- | --- | --- | --- |
| J1 | 11 | 110 ms | 141 ms |
| J2a | 178 | 99 ms | 144 ms |
| J2b | 46 | 98 ms | 121 ms |
| J3 | 18 | 88 ms | 162 ms |
| J4 | 31 | 102 ms | 135 ms |

**每回合的用量（只算耗时的回合）**

| 每回合请求数 | 每回合输入 token | 每回合花费（美分） | 闸：≤ 2 美分 | 全程花费（美元） |
| --- | --- | --- | --- | --- |
| 9.4 | 14186 | 0.0596 | 是 | 0.01883 |
