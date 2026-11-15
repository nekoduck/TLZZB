# content-tune 进度（wave 3 续作）

- [接手] WIP 6f0abd3 已有：C1–C8、C14 的内容补丁（agendas/situations/baseline），scripts/baseline_report.py；走运 12 日 19/19 按时（复核通过）。下一步：修 tests/world 两个失败用例 → C9–C13、C15、C16 → tests/content 基线测试 → 真骰子 0..19 → content_notes。
- [done] tests/world 两个用例随内容改：ping_popo 176 入场；dy_held 换成 dy_leave_camp。下一步：核对 C9–C13、C15、C16、K11 adverse、K3 sect_roles、K1 act:kill。
- [done] C12 全部 22 个情境有 title/chronicle_open/chronicle_close；C16 一次性的 lean 招式都加了 pre 守卫（必要处加了 .thanked/.urged/.bowed_a/.bowed_b/.asked/.threatened 旗标）。下一步：C9 labels+sect_roles、C10/C15 lexicon、C11、C13、K1 act:kill。
- [done] C9 world.yaml labels（role:<id>，平白文字）+ request.sect_roles；C10/C15 lexicon；C11 fact；C13 gate_hammer/tinder/waterskin 的 on_use，仆役 routine 认 admit。下一步：复跑走运基线 + 真骰子 0..19；写 tests/content/test_baseline.py；content_notes。
- [done] tests/content/test_content_wave3.py（C9–C13、C15、C16，18 例）+ test_baseline.py（走运 12 日 19/19、真骰子 3 种子干净、可重放，8 例）；tests/content 共 30 例通过（约 30 秒）。真骰子 0..19：20/20 跑完，无不变量失败/停摆/异常，14 个种子全中。下一步：content_notes.md；再核 check 的 adverse；全量测试 + ruff。
- [done] goals.yaml：加 survive_stand（对头在眼前且自己不弱就打退，弱的仍去躲——治两个互相敌视的人躲到同一处来回跑）；study_manual 的所求加 curiosity 并写明秘籍 silk_scroll（pick_item 的 where 认不出秘籍，原来按 id 挑到别的东西，这条目标实际从不出现）。正在复跑走运 + 真骰子 0..19。下一步：更新 content_notes 第六节的数字；全量测试 + ruff；交付。
- [decision] survive_stand 试过后撤掉：互相敌视的人（两宗掌门互相讥讽后好感跌破 -30）会一打就是几日，比来回躲更糟；来回躲是 world/goals.py 的 _safe_place 两边挑中同一处所致，留给 world 的负责人。study_manual 的修正保留。
- [done] 全量测试里两处由内容引起的失败已在自己的路径内化解：role:wuliang_east/west 的说法不另写（界面退回专名表，tests/ui 断言如此）；banned_game 暂留「经验」、去掉重复的 npc（tests/narrator/test_checks.py:88 按旧表断言，不是我的路径）。下一步：全量测试，交付。
- [done] 全量 851 例通过；ruff src tests scripts 干净；走运 19/19；真骰子 0..19 全部跑完，15 个种子全中，平均 18.4/19。交付。

## 给 DECISIONS.md 的条目（每条一行）

- 情境的 lean 招式出过一次就不再挂星：前提里写「这一拍还没演过」（它自己立的旗标），或它立的旗标本身让情境收场；tests/content 有通用检查。
- 每个情境都有 title / chronicle_open / chronicle_close，三句只说看得见的事，不预告结果。
- world.yaml 的 labels 里 role:<身份> 是平白的名词短语（界面原样打印，不写 ⟦id⟧）；与专名表同名的身份不另写，界面退回专名表。
- request.sect_roles 列出算作门派、人家的身份；来客、马帮、取药的差使、「同受灵鹫宫节制」不算同门。
- 谷口敲树用已有动词：细看大树得知规矩，搜出小锤，拿在手里「用」；人在谷口时得状态 knocked（两个时辰），盘问的人认它（challenge 的 admit，仆役的作息表排在赶人之前）。听见敲树声的人不会自己走出来，留待世界层。
- 火折子用了得状态 lit（半个时辰）并发出看得远的火光；水囊喝几口定神。两者都不是一次性的东西。
- 物件 on_use 里分情形，用带前提、delay 0 的 schedule（小锤只在谷口敲得出名堂）。
- 禁用词：游戏词只留带机制味的整词（拿掉「回合」「任务」）；现代词只收不会误伤寻常说法的（不收「小时」「老师」「香烟」「医生」「新闻」）。「经验」暂留，等 tests/narrator/test_checks.py 那一行改了再拿掉。
- goals.yaml 的 study_manual 写明秘籍是 silk_scroll、所求加 curiosity：pick_item 的 where 认不出秘籍，原写法从不出现；好奇的人手里有没读过的书会去读。
- 试过「对头不比自己强就当面打退」（survive_stand），撤掉了：互相敌视的人会一打几日。两人各自「躲开对方」却躲到同一处来回跑，根子在 world/goals.py 的 _safe_place，不在规则表里治。
- 基线报告脚本 scripts/baseline_report.py：跑的日子不够、by 还没到的参照点记「还没到期」，不算未达成。
- 真骰子基线不追求全中：人死了、判定没成，后面的步骤作废、相关的人重新打算，就是该有的结果；只要求跑完、无不变量失败、无停摆。
