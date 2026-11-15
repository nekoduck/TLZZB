# speech-diverge 进度（I1 开口的选项、I2 分岔只认定一处）

- [接手] 上一位在 6f0abd3 留下：storyteller/speaking.py（开口候选）、options.py 接线（Option.speech）、chronicle 只读 state.divergences、world.baseline 唯一认定、narrator 的 options_text/templates 改动。未写日志。下一步：核对并补测试（开局劝赔不是端到端、未知专名、phrase_options、分岔单条记录）。
- [核对] I2 已完成（baseline.check 唯一认定，chronicle 只读）；I1 实现已在（speaking.py/options.py/narrator），缺测试。实测：开局未笑之前没有「劝赔不是」（apologize 的前提是 guest_laughed，提前给会剧透）；笑过之后出现「低声劝…赔个不是」（介入 87.5）。下一步：写 I1 端到端测试、未知专名/事实测试、narrator 开口选项测试、baseline 单条记录测试。
- [完成] tests/storyteller/test_speech_options.py 前 9 条通过（含劝赔不是端到端：选项→resolve.apply→minds.decide_one 答应→赔礼成 ★→赔礼，耳光不落）。下一步：其余开口测试、narrator 测试、baseline 测试。
- [完成] tests/storyteller/test_speech_options.py 17 条全过（招呼/打听/告知/示警/求情/相求/安慰/答复、to_pending 往返、note_choice/note_action、不露未知专名与不信的事）。下一步：narrator 开口选项测试（tests/narrator/test_options.py）与模板细修，再 baseline 测试。
- [完成] 分岔显示不露未知专名：common.linked 把参照点白话里的真名换成 ⟦id⟧，chronicle.mark_text；年谱条目与 /年谱 的分岔行游戏中按玩家叫法、收场用真名（test_chronicle 相应更新）。下一步：narrator 开口选项测试、baseline 单条记录测试。
- [完成] narrator：开口选项的模板/C4 事实句测试 4 条（tests/narrator/test_options.py）；模板里描述冒号后的事实全文不照抄；旧测试里的禁词「任务」被 content-tune 从 banned_game 拿掉了，测试改用「主线」。下一步：baseline 单条记录+起因测试，全量测试，ruff。
- [完成] tests/world/test_world_baseline.py 加 2 条（一参照点一条记录、起因追到玩家那句劝、只经 diverge 效果；年谱与 baseline 报告指向同一条）。下一步：全量测试 + ruff，然后复查 speaking.py 细节、收尾报告。
- [完成] 全量：816 过、2 败（tests/narrator/test_checks.py 的禁词「经验」、tests/ui/test_ui_commands.py 的身份标签——都来自 content-tune 正在改的 lexicon/world.yaml，不在我名下）；我名下的路径 ruff 干净。实机（mock）走了四回合：笑声之后第一条选项就是「低声劝段公子拱手赔个不是」。发现：同一微轮里出手的人先结算（initiative 5>3），耳光先落，答应在后，req_R1 这一步卡在客人打算最前面（情境已散）。属 minds/kernel，写进报告。下一步：复查 __init__ 导出与文档字符串，收尾。
- [收尾] storyteller/__init__ 导出 speaking、文档字符串写明 Option.speech 与 note_action(action, speech)。I1、I2 列出的事项都已完成；剩余问题见下。
- 决定（给 DECISIONS.md）：
  - 开口的选项 = 内核 Speech + 1 刻 wait（与解析器处理只说话相同），候选在 storyteller/speaking.py，同一张计分表，J4 一并权衡；Option 带 speech/id/signature/desc，to_pending 原样带出。
  - 「劝」只在那一招此刻做得出时才给（赔礼的前提是已经笑过）：提前给等于剧透。劝 = request，所求是对方在情境里那一招（setpiece）；对方答应后内核把它插到打算最前面，成了 ★。
  - 说出口的要旨只带玩家叫得出名字的实体（common.safe_text）；告知只取玩家相信的事，并筛掉对方已经知道的事（查对方信念，只露出「这是新闻」）。
  - 分岔只由 world.baseline.check 认定（world.tick 每个时辰一次，经 diverge 效果）；storyteller.chronicle 只读 state.divergences。开口相求、相劝、示警、喝令、威胁算「改变了别人处境的事件」，优先当起因。
  - 参照点那句白话里的真名先换成 ⟦id⟧（common.linked）：游戏中的年谱与分岔行按玩家的叫法，收场后才用真名。
- 剩余（不在我名下）：minds.order 让出手的人先于被劝的人结算，耳光先落；内核 accept 对已散情境的 setpiece 仍插一步，卡住在场者的打算至多一日；答应的事实句写成「答应情境」；game 的 note_action 没传 speech。
