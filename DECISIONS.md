# 实现中自己拿的主意

方案（`docs/PLAN.md`）没写、或必须取舍的地方，逐条记在这里。编号不复用。

## M0 之前

- **D-001 仓库根就是项目根。** 方案 10.2 的 `tianlong/` 目录对应本仓库的根目录，包放在 `src/tianlong/`。
- **D-002 模块接口另写成文。** 各模块之间的函数签名、`Defs`、`ActionInst`、实体引用写法见 `docs/ARCHITECTURE.md`；`content/` 各文件的格式见 `docs/CONTENT_FORMAT.md`。两份文件只补方案没写的部分，与方案冲突时以方案为准。
- **D-003 内核文件比方案 10.2 多几个。** 除方案列出的 `state predicates effects actions resolve speech beliefs perception conflict growth timeflow invariants rng` 外，加了 `defs`（内容定义的类型）、`refs`（实体引用的渲染）、`events`（唯一造事件的地方）、`checks`（6.4）、`memory`（5.6 的写入）、`mood`（5.5）、`danger`（6.10）。它们都属于方案 6 节的内核规则，只是拆开放。
- **D-004 Jev 密钥的环境变量。** 方案约定 `JEV_API_KEY`。本开发环境里给的是 `JEV_KEY`，所以配置里加了 `api_key_env_fallbacks = ['JEV_KEY']`：先读 `JEV_API_KEY`，没有再读后备的名字。
- **D-005 Jev 的版本。** 2026 年 10 月 14 日 TypeSafe 发布了 `jev-1.14.0`，更新说明里写着「更好的多语言支持」。方案锁定 `jev-1.13.0`，配置照方案；M0 的冒烟脚本对两个版本都跑一遍中文样例，结论记在 D 条目里，再决定是否换。
- **D-006 开发环境里没有 Claude 的密钥，也没有原著文本。** 所以：凡要调 Claude 的验收（M0 的 Claude 冒烟、M3 里 Claude 一方的评测、M5 的管线运行、M7 的时延与试玩）只能写好代码与脚本、用 Mock 跑通，真跑留给有密钥、有原文的机器。`content/` 按方案 9.3 至 9.6 的种子手写（方案 12 节允许 M4 之前手写），不含原文；`style/samples.yaml` 与人物卡的台词样例留空，因为它们必须是原文的子串。
- **D-007 时辰的起点。** `tick = 0` 是第一日子时初，一日十二个时辰从子时数起，辰时是第五个，所以开局 `tick = 32`。
- **D-008 实体引用写成 `⟦id⟧`。** 事实句、动作描述、事实文字里提到实体都这样写，渲染时按看的人决定写真名、叫法还是「你」（`docs/ARCHITECTURE.md` 第 5 节）。这是方案 4.4「称呼表」与 6.8「专名要听到才知道」的落地办法。
- **D-009 情境局部旗标。** 旗标名以 `.` 开头时只属于当前这一场情境，让通用情境可以反复开场。
- **D-010 Mock 的提示通道。** `MockJev` 要能取 ★ 项、对是非题给出合理的值，所以 `jev.ask` 多一个 `hints` 参数，只有 Mock 读它，真请求里没有它。
- **D-011 `goals.yaml`、`routines.yaml`、`opportunities.yaml` 的格式由读它们的模块定。** 方案只给了种子表。
