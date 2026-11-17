# AI 狼人杀：使用说明

9 人标准局（3 狼 3 民 预言家 女巫 猎人）：你一个真人，加 8 个 AI。契约见 [ARCHITECTURE.md](ARCHITECTURE.md)，决策见 [ADR-001](ADR-001-ai-werewolf.md)。

## 运行

```
.venv/bin/python -m werewolf                      # 等同 serve，默认 http://127.0.0.1:8765/
.venv/bin/python -m werewolf serve --offline      # 不调任何模型：规则 + 模板，零花费
.venv/bin/python -m werewolf serve --speed fast --no-browser --port 8770
.venv/bin/python -m werewolf serve --dev          # 开发模式：新局对话框可填种子（页面加 ?dev=1）
```

参数：`--port --host --offline --no-browser --speed slow|normal|fast|instant --dev --config werewolf.toml --allow-origin ORIGIN`。
服务只接受本机页面的握手（Origin 为 `127.0.0.1:{port}` 或 `localhost:{port}`；经反向代理访问时用 `--allow-origin`
逐个放行，可重复，精确匹配），同一时刻一个连接、一局：新开的页面会顶掉旧页面。

## 怎么玩

1. 打开页面，在「欢迎入座」里填名字（1 到 8 个汉字或字母）、身份偏好、座位、演出速度，点「开始游戏」。
2. 发牌后按顶栏提示走：夜里点座位选目标，再点确认键（狼人首夜可选打法；女巫点药瓶；平民等天亮）；
   白天轮到你时底栏出现输入框：直接打字，或点快捷短语（「我是预言家」「查杀 N 号」「今天出 N 号」……）；
   「预览」会先显示系统理解到的言语行为，读错的点 × 去掉，再「确认发言」。「过」是跳过。
3. 投票：点座位再确认，也可以弃票。天亮公告、票型公示、猎人开枪都有专门的画面。
4. 你出局后，等不再有需要你做的决定，顶栏出现「上帝视角」，可看全部身份与此前不可见的事件。
5. 终局后看胜方与全员身份，点「查看复盘」看每天的怀疑度曲线与每个 AI 的内心想法。
6. 刷新页面不会丢局：浏览器重连后发 `resume`，服务端重放历史并停在同一个请求上。

座位上的「狼 / 好 / 神 / ?」是你自己的记号，只存在你的浏览器里。
演示模式：`/?demo=<名字>` 播放 `src/werewolf/web/demo/<名字>.json`，不连服务。

## 自对弈与统计

```
.venv/bin/python -m werewolf sim --games 300 --mode rules --report runs/sim.md
.venv/bin/python -m werewolf sim --games 20 --mode full --compare --budget 1.0   # 真模型，会花钱
```

`--mode rules|jev|full`（默认 rules，默认 20 局），`--human-seat N` 让一个座位由托管真人占着，`--seed` 是第一局的种子，
`--report` 写 markdown 报告。另有：`--engine session|arena`（rules 模式走会话层还是纯规则循环）、`--parallel N`
（默认 rules 1 局、模型模式 4 局并行）、`--budget`（模型模式总花费上限，默认 1 美元）、`--jev-weight`（覆盖
`MindTuning.jev_weight`）、`--check`（每步跑心智不变量，慢）、`--config`。

## 配置模型与密钥

- 配置在仓库根的 `werewolf.toml`；密钥只从环境变量或 `.env` 取，**不写进配置，也不要提交**：
  - Jev：`JEV_API_KEY`（备选 `JEV_KEY`）；Claude：`ANTHROPIC_API_KEY`（备选 `CLAUDE_KEY`、`CLAUDE_API_KEY`）。
  - 没有密钥或加了 `--offline`：自动退到规则与模板，游戏照常能玩。
- `[werewolf.route]`：每类调用走哪一档（`speech = 'claude' | 'jev_select' | 'template'` 等），失败自动降档。
- `[models.W_speech]`、`[models.W_monologue]`、`[models.W_parse]`：模型、思考强度、`max_tokens`、超时。
- `[limits] session_budget_usd`：一次启动的总预算；到八成提醒，到顶后全走规则与模板。顶栏设置里能看到本局花费。
- 速度档也可以在对话框或设置里切；`instant` 不等动画、不打字。
- 真模型冒烟：`.venv/bin/python -m werewolf smoke [decide speech parse check] [-v]`。开跑前先说缺哪把密钥（环境变量名）、
  因此哪些项目跳过：缺 Jev 跳过 Jev 决定、解析（Jev）、核对（Jev），只跑规则部分；缺 Claude 时台词只测 Jev 挑选档或模板档。

## 日志在哪

每局一个目录 `runs/<game_id>/`（`runs/` 已被 gitignore）：

| 文件 | 内容 |
| --- | --- |
| `meta.json` | 种子、真人座位、速度 |
| `events.jsonl` | 全部内核事件（裁判视角，含身份与原始编号） |
| `decisions.jsonl` | 每个 AI 的每个决定 |
| `briefs.jsonl` | 台词简报、成稿、档位、被拦下的稿子 |
| `wire.jsonl` | 与浏览器之间的全部消息 `{ts_ms, dir, msg}` |
| `calls.jsonl` | 模型调用（花费、时延） |

把一局变成前端演示：`python -m werewolf demo <game_id> <名字> [--runs DIR] [--out DIR]`（默认输出到 `web/demo/`）。

## 测试

```
.venv/bin/python -m pytest tests -q                 # 快的那套（默认跳过 slow 与 network）
.venv/bin/python -m pytest tests -q -m slow         # 慢的：300 局自对弈统计、浏览器整局等
.venv/bin/python -m pytest tests -q -m network      # 要真密钥、会花钱
```

`pyproject.toml` 的 `addopts` 是 `-m 'not network and not slow'`；命令行再给 `-m` 就覆盖它。

## 浏览器端到端测试

```
.venv/bin/python -m pytest tests/ww/e2e -q -m slow  # 五种身份各一局（instant）+ 手机尺寸一局（fast）；需要 node 与 playwright（缺了跳过）
node tests/ww/e2e/driver.mjs --url http://127.0.0.1:8765 --role seer --seed 22 --speed fast \
     --out /tmp/e2e --shots deal,night,speech,composer,reveal,dawn,over [--mobile] [--no-reload]
.venv/bin/python tests/ww/e2e/wirehelper.py runs/<game_id> /tmp/e2e/result.json   # wire_check + 界面对账
```

驱动只用界面控件打完整局，检查：零控制台错误与未捕获异常、每个请求都能作答、中途刷新能恢复同一屏与同一请求、
出局后的上帝视角、布局（无横向溢出、座位不压中央、关键控件在视口内、文字不被裁）。`wirehelper` 对 `wire.jsonl`
跑 `wire_check`，并核对界面最后的 seq、出局名单、胜方与 wire 一致，统计每段 AI 发言从 thinking 到发言的耗时。

## 验收现状

第 10 节每一行对应的测试（完整索引与函数名见 `tests/ww/acceptance/test_index.py`，有测试核对索引里的文件与函数都在）。
表里的数是测试断言的阈值；标「slow」的默认不跑，要 `-m slow`。

| 第 10 节 | 测试文件 |
| --- | --- |
| 人格一致 | `mind/test_policy_persona.py` |
| 记忆与遗忘 | `mind/test_policy_memory.py` |
| 动机与主动性 | `sim/test_arena.py`、`sim/test_selfplay.py`（300 局的两项是 slow） |
| 情绪 | `mind/test_policy_emotion.py` |
| 知识边界 | `sim/test_arena.py`、`acceptance/test_full_games.py`、`mind/test_policy_reactions.py`、`voice/test_brief.py`、`acceptance/test_role_independence.py` |
| 不完美 | `sim/test_arena.py`、`sim/test_selfplay.py`（slow） |
| 社会性 | `mind/test_mood.py`、`mind/test_policy_persona.py`、`mind/test_policy_refusal.py`、`sim/test_selfplay.py` |
| 内核不变量 | `kernel/test_properties.py`、`server/test_session_games.py`（slow）、`acceptance/test_full_games.py` |
| 口径一致 | `sim/test_arena.py`、`sim/test_selfplay.py` |
| 抗骗用例 | `acceptance/test_anticheat.py`（数据 `data/anticheat.yaml`） |
| 防漏 | `acceptance/test_leaks.py`、`llm/test_speak.py`、`server/test_session_wire.py`、`llm/test_structure.py`、`voice/test_brief.py` |
| 平衡与分布 | `sim/test_selfplay.py::test_section10_targets_300_plus_300`（slow） |
| 基线对照 | `sim/test_selfplay.py`（替身模型）；真模型用 `sim --compare` |
| 真模型冒烟 | `llm/test_smoke.py`（替身版）；真模型用 `python -m werewolf smoke`，不在测试里 |
| 浏览器整局 | `web/test_demos.py`（演示文件逐条校验）、`e2e/test_browser_games.py`（Playwright 实机，slow） |

没有自动覆盖的：真模型下的各项指标（解析精确率、核对放行、Claude 档占比、花费与时延）只在联网跑 `smoke` 与 `sim --mode full` 时得出。
