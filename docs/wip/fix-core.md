# fix-core 进度（终审修补，core 包）

- [done] 1 被拦着追着时 go 要按逃跑判定：danger.blockers（意图冲着他来的捉拿/攻击，或 impeded 的来源；清醒、未被捆扣、看得见他）；resolve.r_go → _flee_to(e, blockers ∪ _pursuers)；r_flee 拆出 _flee_to；actions go 的 desc 加「夺路」。测试 tests/kernel/test_core_fixes.py（go_*）。
- [done] 2 图外的死没有人看见：effects.Run.implicit_about（主体不在图上且本列表没有 emit 就不记事件）；kill 图外不掉落物品；injure/add_status/remove_status/kill 的自带事件都走它。测试 offmap_*。
- [done] 3 动了人就藏不住：resolve._reveals（attack、restrain/block/give/show/help/release/teach、推人、对别人用东西）在结算前解除 hidden；events.emit 里躲着的人自己出了大动静即解除；r_steal 失手先解除再记事件。测试 hidden_*。
- [done] 4 没有救人就没有恩：resolve.R._others_doing（来源不是自己/上司/手下才算别人的）；r_release、r_use 的 rescue/helped 只在解的是别人加的状态时给；actions help 对已 tended 且醒着的人不合法。测试 captor_/subordinate_/third_party_/help_cannot_/use_cure_*。
- [done] 5 没有 do 的许诺到期静默了结：timeflow._promises 里 status 仍记 broken，但不扣信任、不记「没有做到」、不排重新打算。测试 promise_*。
- [done] 6 侥幸不死可再逃一次：conflict saved 加 reprieve（2 刻）；actions flee 在 reprieve 下不受 3 级之限、flee_available 认它；resolve.r_flee 一逃即用掉；go/follow 照旧不行。测试 saved_*。parser/binding.py:147 的 TOO_HURT（不在本包路径内）留给上报。
- [done] 7 读 SSE 正文时的传输层异常落成 ClaudeError：llm/claude.py _stream（及 _create 的同款加固）末尾加 except LLMError: raise / except Exception → ClaudeError(类型名)。测试 tests/llm/test_llm_claude_sdk.py（stream_transport_error_mid_body_*、stream_early_close_*）。
- [done/partly] 8 重放对不上要报出来（llm 这一半）：Replayer.mismatches + Replayer.mismatch()（next 的两处、replay.py 的两处都走它，仍抛 ReplayMismatch）；LLM.replayer 字段、make_llm 填上。测试 tests/llm/test_llm_record.py（replay_mismatches_are_kept_*、replay_recorded_output_unfit_*）。game.py 的 Notice、__main__ 的退出码、ARCHITECTURE、tests/e2e 不在本包路径内，留给上报。

## 待记入 DECISIONS.md 的决定（DECISIONS.md 不在 core 包路径内，由汇总方编号写入）

- （1）被拦着、追着时 `go` 仍合法，但按逃跑判定（6.4）结算，不成即被追上。「拦着、追着」只认：同处、清醒、未被捆扣、看得见他，且眼下的打算就是冲他来的攻击/捉拿，或是他身上 `impeded` 的来源；仅仅好感低、门派对头不算，否则从仇家身边走过也成了逃命。
- （2，补 D-056）死在图外的人（已离图或未上场）：账照记（打算作废、请求过期、许诺了结、相关的人重新打算），但不单独记事件、没有见证人，身上的东西仍在他身上、不落回地图；同一效果列表里写了 `emit` 的照旧并入性质。受伤、中毒、被捆、脱困的自带事件同此。
- （3）躲着的人何时现身：自己弄出大动静（任何 loud 事件）、冲着别人去的动作（attack、restrain、block、give、show、help、release、teach、推人、对别人用东西）、偷东西失手；自己悄悄做的事（看、听、查、搜、拿、放、推机关、自己用东西、等）与偷东西得手不现身。
- （4，细化 D-029）「被救了命」「被帮了忙」要真有一救：放开或解掉的状态里至少有一个是别人加的才给（来源是自己、自己的上司或手下的不算；来源不明算别人的）；已有人照料着且醒着的人不能再 `help`（一个照料期 96 刻只记一次恩）。
- （5）没有动作模式（`do`）的许诺照旧记一条带期限的承诺，到期静默了结：不扣信任、不记「没有做到」、不触发重新打算；只有写明 `do` 的许诺才判守诺/失约。
- （6）侥幸不死：重伤 3 级 + `reprieve`（2 刻），其间可 `flee` 一次（一逃即用掉，重伤的减值照算），不受 3 级不能走动之限；`go`/`follow` 仍不行。
- （7，D-171 旁）读 Claude 流式正文时抛出的任何非 `LLMError` 异常（传输层超时、断线、坏事件）在传输层一律落成 `ClaudeError`（消息里带原异常类型名），调用方照 4.3 兜底；`_create` 同样处理。调用方中途停下与任务取消不算错误。
- （8，补 D-018）重放对不上仍按 4.3 兜底、游戏不中断，但每一处都记在 `Replayer.mismatches`（经 `LLM.replayer` 露出），由主循环提示玩家一次、命令行以退出码 1 报告。
- [done] 收尾：全量 pytest 1036 passed（含 tests/content/test_baseline.py、tests/acceptance）；ruff check src tests scripts 通过。
