"""AI 驱动的 9 人狼人杀（单机）。设计见 `docs/werewolf/ADR-001-ai-werewolf.md` 与 `docs/werewolf/ARCHITECTURE.md`。

分层（自下而上）：
- `kernel/`：规则内核。事实只有这一个来源；纯同步、可重放、不做任何 I/O。
- `mind/`：每个 AI 玩家的心智。信念、情绪、记忆、效用与发言计划，全是规则，输入模型而不由模型产生。
- `voice/`：人设卡、口吻、模板发言。
- `llm/`：Jev 与 Claude 的适配。Jev 只在合法选项里选；Claude 只拿「对外口径」写台词。
- `director/`：法官与导演。只管节奏、旁白和反应的投放，不替角色做决定。
- `server/`：本地服务与一局游戏的会话编排。
- `web/`：浏览器前端（静态文件）。
- `sim/`：无人值守自对弈与行为统计。
"""

__version__ = '0.1.0'
