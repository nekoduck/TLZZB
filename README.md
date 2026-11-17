# 天龙八部·无量山与万劫谷：终端里的纯文字沙盒

一个在终端里运行的单人文字游戏。开局是《天龙八部》开篇的那个上午：无量山剑湖宫比剑，每个人怀着原著里的心思、带着原著里的东西、站在原著里的位置。你是随茶商上山的小伙计，不会武功。从你的第一个回合起，世界不再跟着原著走，只跟着各人的打算、世界的规则和你每一回合做的事走。没有主线，没有预设的结局，没有受保护的人物。

每一回合：一段叙述，三个选项；也可以不选，直接写下你想做的事、想说的话。

规格在 [docs/PLAN.md](docs/PLAN.md)（实现方案，权威）；规格没写、实现时自己拿的主意逐条记在 [DECISIONS.md](DECISIONS.md)；两份架构决定在 [docs/adr/](docs/adr/)。

## 快速开始

需要 Python 3.12 以上。

```bash
uv venv                                  # 或 python3.12 -m venv .venv
source .venv/bin/activate
uv pip install -e '.[dev]'               # 或 pip install -e '.[dev]'

python -m tianlong play --mock           # 不联网、不花钱：确定性的替身模型 + 模板文字
```

`--mock` 下人物照各自的打算行事，叙述和选项是模板句，够把规则世界从头玩到尾。要真模型写字、拿主意，配上密钥再去掉 `--mock`：

```bash
cp .env.example .env                     # .env 不进版本库
# 在 .env 里填：
#   JEV_API_KEY=...         快系统（TypeSafe 的 Jev；也认 JEV_KEY）
#   ANTHROPIC_API_KEY=...   慢系统（Claude）
python -m tianlong play
```

- 只有 Jev 的密钥也能玩：人物的抉择、选项的权衡、叙述的核对走 Jev，文字退回模板句（方案 4.3 的退让表：任何一次模型调用失败，游戏照常继续）。
- 每类调用走哪条路、门槛、模型名、单局预算（默认 5 美元，到顶暂停并询问）都在 `config.toml`。
- 原著文本只有离线的内容管线用得着：放在 `corpus/tianlong.txt`（`config.toml` 的 `[paths] corpus`；不进版本库）。仓库里的 `content/` 是按方案第 9 节的种子手写的，不含原文，没有原著也能玩。

## 游戏里的命令

以斜杠开头，不消耗游戏时间（方案 2.7）：

| 命令 | 作用 |
| --- | --- |
| `/状态` | 你此刻的处境：身份、伤病、时辰与地点 |
| `/物品` | 身上带着什么 |
| `/人物` | 你认识的人，以及他们待你如何 |
| `/本事` | 学过的本事与火候 |
| `/牵挂` | 还没了结的事：期限、承诺、眼前事、心愿、线索 |
| `/回顾` | 最近几回合的经过 |
| `/年谱` | 至今的大事，以及已经认定的与原著的分岔 |
| `/心愿 你想要的事` | 立一个自己的心愿，之后的选项会照应它 |
| `/存档 名字`、`/读档 名字` | 存、读；`/读档 检查点 [序号]` 从检查点重来；`/读档` 不带名字列出现有的存档与检查点 |
| `/收笔` | 就此收场，写后事与分岔清单 |
| `/帮助`、`/退出` | |
| `/debug` | 只在 `--debug` 下：内核状态、本回合的模型调用、调试用的作弊命令 |

选项按 `一`、`二`、`三`（或 `1`、`2`、`3`）选。走出地图、做要命的事之前会先问一句；死了可以从检查点（每日之始、大险之前）重来。

## 命令行

```text
python -m tianlong play [--seed N] [--mock] [--replay runs/<id>] [--debug] [--name 名字]
                        [--save 名字] [--load 名字] [--plain] [--width N]
python -m tianlong pipeline <p0..p7|all> [--mock] [--direct] [--baseline]   # 离线内容管线（方案 9.1）
python -m tianlong baseline [--seeds 20] [--days 12] [--lucky] [--mock] [--strict]
                                                     # 基线跑：没有玩家，世界自己走（方案 11.3）
python -m tianlong eval [套件名|all|report] [--mock] [--limit N] [--lang zh|en] [--model jev-…]
                                                     # 方案第 11 节的评测；不给套件名就列出套件
python scripts/api_smoke.py [--skip-eval]            # 两个模型客户端的冒烟
```

- 全局选项 `--config path/to/config.toml`（默认取当前目录下的）。
- 每一局的日志在 `runs/<id>/`：`transcript.md`（你看到的全文）、`events.jsonl`（全量事件，含你不在场时发生的）、`calls.jsonl`（每次模型调用）。`--replay runs/<id>` 按录下的调用与输入逐字重放。
- 存档在 `saves/<局名>/`：`state.json` 每回合自动覆盖，`checkpoints/` 保留最近 10 个检查点。

## 三层怎么分工

1. **规则（`kernel/`）说了算。** 什么是真的、什么做得到、做了会怎样，只由确定性的内核代码决定并写进事件日志；模型的任何输出都只是提议，经内核校验才生效。
2. **快系统（Jev）做选择题。** 人物此刻做什么、你这句话是什么意思、哪些选项要紧、叙述有没有越界——都是「在内核给的合法清单里选一个、打个分、答是或否」，又快又便宜。
3. **慢系统（Claude）写字和深思。** 你读到的叙述与台词、快系统没把握或事关重大时的抉择、重订打算、收场的后事，以及离线读原著。
4. **你不在场，世界照样往前走。** 每个时辰，各人按自己的打算行事；前提没了的事不再发生，受影响的人各自重新打算。原著只管开局，`baseline.yaml` 只在事后对照时读取，用来告诉你这一局在哪里与原著分了岔。
5. **任何一层失败都不拖垮游戏。** Jev 失败退到 Claude，Claude 失败退到规则与模板；`--mock` 下整局是确定性的，同一个种子、同样的输入，逐字相同。

## 目录

```text
pyproject.toml  config.toml  .env.example  DECISIONS.md  README.md
content/      运行时读取的世界数据：地点、人物卡、打算、情境、物件、事实、本事、规则表、原著参照点
prompts/      提示词与 Jev 的问题模板
src/tianlong/
  __main__.py   命令行
  game.py       回合主循环（方案 4.2），与界面无关
  kernel/       规则：状态、谓词、效果、动词、结算、言语与请求、信念、感知、成长、时间、不变量
  llm/          两个模型客户端、路由、记账、录制与重放、替身
  parser/       把你写的话读成一个合法动作加一句话
  minds/        人物的抉择、记忆、路人的作息、夜间反思
  world/        世界时钟、情境、离场结算、重新打算、与原著的对照
  storyteller/  牵挂、三选项、节奏与机会牌、年谱、后事
  narrator/     叙述、逐句检查、选项措辞、模板
  store/  ui/   存档与日志；终端界面与只读命令
  pipeline/     离线内容管线 P0 至 P7
  evalrun/  testing/   评测跑手；试探与分岔场景的驱动
evals/        评测集、结果与报告（evals/report.md）
scripts/      api_smoke.py、baseline_report.py、fuzz.py
tests/        按模块分目录；跨模块的在 tests/e2e、tests/acceptance
docs/         PLAN.md（规格）、ARCHITECTURE.md（模块契约）、CONTENT_FORMAT.md（content/ 的格式）、
              API_NOTES.md（接口索引）、content_notes.md（内容上待原文核对之处）、adr/
corpus/  build/  runs/  saves/     原著、管线中间产物、每局日志、存档；都不进版本库
```

依赖方向单向：`ui → game → parser / minds / world / storyteller / narrator → kernel`；这五个模块可以用 `llm`，彼此不互相 import；`kernel` 不 import 本包别的任何东西。`src/` 里不出现人名、地名、剧情，它们都在 `content/`、`prompts/`、`evals/`、`tests/` 里。

## 开发

```bash
.venv/bin/python -m pytest -q             # 全部测试：不联网，用替身模型
.venv/bin/ruff check src tests scripts
```

联网的测试标了 `network`，默认不跑。改 `content/` 之后先跑 `python -m tianlong pipeline p7 --mock`（校验）与 `python -m tianlong baseline --lucky --mock --seeds 1`（走运的基线）。

## 里程碑进度

过关标准见方案第 12 节。本仓库是在**没有 Claude 密钥、没有原著文本**的环境里做出来的（D-006）：凡要 Claude 写字或读原著的验收，代码与脚本都在、用替身跑通了，真跑要留给有密钥、有原文的机器；要人来试玩、过目的，留给人。

| 里程碑 | 在这里验过的 | 还差什么 |
| --- | --- | --- |
| M0 脚手架 | 两个客户端、记账、录制与重放、替身；Jev 真跑过冒烟，响应存成夹具（`evals/m0/`）；中英问法比较有结论（D-012、D-013） | Claude 的 8 项冒烟：`python scripts/api_smoke.py --skip-eval`（要 Claude 密钥，D-021） |
| M1 内核 | 11.1 的单元测试全过（`tests/kernel`）；不经模型、只用命令把 `bout_laugh` 玩通，学会一门本事（`tests/e2e/test_m1_commands.py`） | — |
| M2 一回合 | 真实 `content/` 里用替身完整地玩 30 回合，每屏视角检查通过（`tests/e2e/test_m2_gate.py`）；50 回合录制后逐字重放；Jev 一侧的首字时延实测 100 回合，中位数 580 毫秒、P90 855 毫秒（闸是 3 秒、6 秒） | C3 叙述、C4 措辞的真模型输出与首字时延（要 Claude 密钥） |
| M3 人物 | 11.6 的报告出齐了 Jev 一侧（`evals/report.md`）：J2 抉择过闸（门槛之上覆盖 52.5%、一致 83.3%），J5 核对按游戏里发给 Jev 的那份状态重跑后没过闸（门槛 0.80：检出 74.0%、误杀 4.0%；0.70：检出 82.0%、误杀 20.0%；没有一个门槛两项都满足。漏掉的全在玩家说了话的回合，见 `evals/results/findings.md`），J1 归类未过闸（门槛之上覆盖 87.7%、六项全对 81.4%，要求 95%）、路由定为先走 C1；防作弊集替身 40/40、真 J1 归类 40/40；拒绝集替身（归类照标注）20/20、真 J1 归类 20/20；知识边界与视角的字符串检查为零违规 | 报告里 Claude 的各栏（C1 归类、C2 抉择、台词一级的知识边界判读） |
| M4 世界 | 手写内容上带 `--lucky` 的基线 12 日 19/19 个参照点按时成立；真骰子 20 个种子全部跑完、无不变量失败；劝说路径（笑声之后劝人赔礼 → 耳光不落 → 记为分岔）经真正的回合循环走通（`tests/game/test_persuasion.py`）；「提前救人」「替人送信」两个分岔场景在真骰子下通过（`tests/acceptance/test_divergence.py`，见 `docs/wip/scenario-fix.md`） | 这两个场景写进验收记录（`docs/acceptance.md` 还没有） |
| M5 内容 | 管线 P0 至 P7 的代码与替身跑通；P7 的校验与走运的基线在手写内容上通过 | 用原著真跑管线生成 `content/`（要原著全文与 Claude 密钥）；差异报告的八条；人工过目 |
| M6 全域 | 收笔与后事、年谱与分岔清单、检查点都在并有测试 | 11.4 的全部分岔场景、11.2 的 2,000 回合试探：待填 |
| M7 打磨 | Jev 一侧每回合成本实测 0.064 美分（100 回合，每回合约 10 个 Jev 请求；闸是 2 美分） | 文风、Claude 一侧的时延与成本（要 Claude 密钥）；两局试玩与五个问题的记录（要人） |

「待填」的格子等验收测试与评测的最新结果出来后填；数字以 `evals/report.md` 与 `docs/wip/` 里各模块的交付记录为准。

## 文档索引

- [docs/PLAN.md](docs/PLAN.md)：实现方案（规格）。与别的文档冲突时以它为准。
- [DECISIONS.md](DECISIONS.md)：规格没写、实现时拿的主意，逐条编号。
- [docs/adr/](docs/adr/)：架构决定记录（内核为真、快慢两套模型；在场与离场、谁来推进打算）。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：模块之间的契约。
- [docs/CONTENT_FORMAT.md](docs/CONTENT_FORMAT.md)：`content/` 各文件的格式。
- [docs/API_NOTES.md](docs/API_NOTES.md)：已实现模块的接口索引。
- [evals/README.md](evals/README.md)、[evals/report.md](evals/report.md)：评测集的格式与最新报告。
