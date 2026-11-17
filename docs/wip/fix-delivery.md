# fix-delivery 进度

- [done] 1 钟灵去营地是陪着段誉：`content/agendas/zhong_ling.yaml` 的 `zl_guard_duan` 去掉 `at`、改成 `follow duan_yu`、前提加 `alive duan_yu`；`zl_wait_rescue` 去掉 `at`。测试 tests/content/test_content_delivery_fix.py（zhong_ling_*，3 条）。
- [done] 2 私逃被撞见只在后山小径、断崖开场：`content/situations/caught_eloping.yaml` 的 `when` 与文件头注释。测试同上文件（bystander_at_the_ferry_*、caught_eloping_still_opens_*、nobody_dies_at_the_cliff_*）。
- [done] 4 撞见的人不取躲着的、被拿住的、被缚的：`caught_eloping.yaml` 的 `roles.witness`。测试同上文件（hidden_or_held_*、hidden_player_does_not_take_the_scene_*）。
- [done] 3 knows 里不得有事实表明写「开局时无人知道」的事：`pipeline/common.py` 加 `unknown_at_opening`，`filter_knows` 用它（P3、P4 随之生效）；`pipeline/p7_validate.py` 从 facts.yaml 读 known_by，`pipeline_checks` 多一个参数 `nobody`。`content/facts.yaml` 没改。测试 tests/pipeline/test_common.py（filter_knows_drops_*、filter_knows_on_shipped_facts）、tests/pipeline/test_p7.py（p7_flags_*，2 条）。文档：docs/CONTENT_FORMAT.md、docs/content_notes.md。
- [done] 收尾：全量 pytest 1064 passed（含 tests/content/test_baseline.py、tests/acceptance）；ruff check src tests scripts 通过；`python -m tianlong pipeline p7 --mock` 通过。二十个种子的基线报告没有重跑。

## 待记入 DECISIONS.md 的决定（DECISIONS.md 不在 delivery 包路径内，由汇总方编号写入）

- （1）钟灵去神农帮营地的两步挂在人身上而不是挂在地点上：`zl_guard_duan` 是 `follow duan_yu`（前提含段誉活着），`zl_wait_rescue` 不写 `at`（被扣的人原地等，没被扣就作废）。段誉死了或不去，她不独自进营，擒人、放貂随之不发生。
- （2，原则 5；收窄方案 9.5 的「私逃途中」）`caught_eloping` 只在后山小径与断崖开场：它的结算就是「逼到断崖、失足坠落」，断崖离后山小径一段路；两人在渡口、大道上被人看见不开情境（效果语言没有条件分支，不另造一套「不在崖边」的结局）。
- （4，D-335 旁）`caught_eloping` 的「撞见的人」不取躲着的、被拿住的、被缚的人；只剩这样的人在场时不开场。
- （3，补 D-211/D-215）knows 另外不得含 facts.yaml 里明写 known_by 为空的事实（开局无人知道）：P3/P4 的 `filter_knows` 见了就删，P7 报成问题；没有 known_by 这个键的（`exit:*`、知情人只记在卡里的）不算。`F_zhong_ling_held` 属此类，不打 after_opening 标（P5 见标即丢）。
