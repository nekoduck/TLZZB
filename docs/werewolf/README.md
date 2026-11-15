# AI 狼人杀：使用说明

9 人标准局（3 狼 3 民 预言家 女巫 猎人）：你一个真人，加 8 个 AI。契约见 [ARCHITECTURE.md](ARCHITECTURE.md)，决策见 [ADR-001](ADR-001-ai-werewolf.md)。

## 运行

```
.venv/bin/python -m werewolf                      # 等同 serve，默认 http://127.0.0.1:8765/
.venv/bin/python -m werewolf serve --offline      # 不调任何模型：规则 + 模板，零花费
.venv/bin/python -m werewolf serve --speed fast --no-browser --port 8770
.venv/bin/python -m werewolf serve --dev          # 开发模式：新局对话框可填种子（页面加 ?dev=1）
```

参数：`--port --host --offline --no-browser --speed slow|normal|fast|instant --dev --config werewolf.toml`。
服务只接受本机页面的握手（Origin 为 `127.0.0.1:{port}` 或 `localhost:{port}`），同一时刻一个连接、一局。

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

`--mode rules|jev|full`，`--human-seat N` 让一个座位由托管真人占着，`--seed` 是第一局的种子。

## 配置模型与密钥

- 配置在仓库根的 `werewolf.toml`；密钥只从环境变量或 `.env` 取，**不写进配置，也不要提交**：
  - Jev：`JEV_API_KEY`（备选 `JEV_KEY`）；Claude：`ANTHROPIC_API_KEY`（备选 `CLAUDE_KEY`、`CLAUDE_API_KEY`）。
  - 没有密钥或加了 `--offline`：自动退到规则与模板，游戏照常能玩。
- `[werewolf.route]`：每类调用走哪一档（`speech = 'claude' | 'jev_select' | 'template'` 等），失败自动降档。
- `[models.W_speech]`、`[models.W_monologue]`、`[models.W_parse]`：模型、思考强度、`max_tokens`、超时。
- `[limits] session_budget_usd`：一次启动的总预算；到八成提醒，到顶后全走规则与模板。顶栏设置里能看到本局花费。
- 速度档也可以在对话框或设置里切；`instant` 不等动画、不打字。
- 真模型冒烟：`.venv/bin/python -m werewolf smoke [-v]`。

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

把一局变成前端演示：`python -m werewolf demo <game_id> <名字>`（输出到 `web/demo/`）。

## 浏览器端到端测试

```
.venv/bin/python -m pytest tests/ww/e2e -q          # 慢；需要 node 与 playwright（缺了自动跳过）
node tests/ww/e2e/driver.mjs --url http://127.0.0.1:8765 --role seer --seed 22 --speed fast \
     --out /tmp/e2e --shots deal,night,speech,composer,reveal,dawn,over [--mobile] [--no-reload]
.venv/bin/python tests/ww/e2e/wirehelper.py runs/<game_id> /tmp/e2e/result.json   # wire_check + 界面对账
```

驱动只用界面控件打完整局，检查：零控制台错误与未捕获异常、每个请求都能作答、中途刷新能恢复同一屏与同一请求、
出局后的上帝视角、布局（无横向溢出、座位不压中央、关键控件在视口内、文字不被裁）。`wirehelper` 对 `wire.jsonl`
跑 `wire_check`，并核对界面最后的 seq、出局名单、胜方与 wire 一致，统计每段 AI 发言从 thinking 到发言的耗时。
