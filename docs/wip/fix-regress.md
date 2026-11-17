# fix-regress 进度

复查 D-347 到 D-366 与 seams 各项后报出的七条回归。每条先写一个因所报原因而失败的测试，再改。

## 已完成

- 1（走开被追上又在同一回合被杀）：`kernel/resolve.py` 的 `_flee_to`，被追上时追者那一步记作做完。测试 `tests/kernel/test_regress_fixes.py`（前三条）、`tests/game/test_regress_fixes.py::test_walking_away_from_a_killer_never_dies_in_one_input`。
- 2（闯出口没闯成仍耗整条路）：`kernel/resolve.py` 加 `held_back`；`game.py` 的 `_play` 没走成耗时记 0；`world/offscreen.py` 的 `_walk` 没走成不清这段路已花的工夫。测试 `tests/game/test_regress_fixes.py::test_failed_contested_go_costs_no_travel_time`、`..._that_gets_through_still_costs_the_edge`、`tests/world/test_world_regress_fixes.py::test_offscene_walker_stopped_at_the_door_keeps_his_leg`。
- 3（侥幸不死的机会在下一次输入前过期）：`game.py` 的 `_keep_reprieve`（时间走完后重记两刻）；`storyteller/options.py` 在下手的人还在跟前时必给「自保」（逃）。测试 `tests/game/test_regress_fixes.py::test_reprieve_*`。
- 4（闯出口把不算拦着的人也拉进来）：`r_go` 只用 `danger.blockers`；`danger.pursuers`、`danger.can_stop` 给 flee 与 struggle 的追者加同样的条件。测试 `tests/kernel/test_regress_fixes.py`（后三条）。
- 5（玩家只在大厅里等，钟灵的人质一步提前作废）：`content/agendas/zhong_ling.yaml` 的 `zl_wait_rescue` `takes` 16 改 64；`docs/content_notes.md` 改正「作废后接着做回家」。测试 `tests/world/test_world_regress_fixes.py::test_waiting_in_the_hall_leaves_the_hostage_line_intact`。
- 6（途中每个时辰重查前提）：`world/offscreen.py` 上了路不再查（`Agenda.en_route`）。测试 `test_premise_is_not_rechecked_on_the_road`、`test_premise_false_on_arrival_still_voids`。
- 7（眼前刚开场的情境还没生效就作废）：`world/offscreen.py` 动身前头一次不成立只等一个时辰（`Agenda.doubt`），下个时辰仍不成立才作废。测试 `test_false_premise_waits_one_hour_before_voiding`、`test_premise_that_comes_true_within_the_hour_is_kept`、`test_siege_opened_in_front_of_the_player_does_not_void_the_defenders`；`tests/world/test_world_offscreen.py::test_false_premise_is_voided_before_setting_out` 随之改为两个时辰。
- 6、7 共用的记账：`kernel/state.py` 的 `Agenda.en_route`、`Agenda.doubt`；`kernel/effects.py` 的 `mark_char` 认这两项，换一步、插一步时清掉。测试 `tests/kernel/test_bookkeeping_effects.py::test_mark_char_setout_marks_live_on_the_current_step`。文档 `docs/API_NOTES.md`。

## 决定（交给主控编号写进 DECISIONS.md）

- （补 D-347）走开或逃跑没成、被追上时，追者眼下这一步若就是冲他来的攻击或捉拿，这一下就算他这一步的出手，记作做完；他要再下手得另起打算。这样同一来源的两次致命判定不会落在玩家的同一次输入里（6.10）。不另加致命警示：被人追上取命属于「别人要杀玩家」，走的是降级那一条，不是「玩家自己的致命动作」。
- （补 D-347、D-358）`go` 按逃跑判定结算而没走成的，不耗路上的时间：玩家这一回合耗时记 0（与 `flee` 相同）；离场的人这段路已经花的工夫留着，下个时辰接着闯。D-358「赶路一律走完边上的耗时」只管走成了的。
- （修订 D-352）侥幸不死的两刻，对玩家从这一回合的时间走完（下一次能动手）时起算；这一回合里得了又自己逃过一次的不补。两刻之内下手的人还在跟前时，三个选项里必有一个「自保」（拔腿逃）。角色不变：照旧从挨那一下起算两刻。
- （修订 D-347）闯出口的较量只算拦着的人（`danger.blockers`），不再并入在场的其他仇家；`flee` 与 `struggle` 的追者、看守也只算清醒、没被捆扣、看得见他的人，被捆着的仇家仍让 `flee` 可用，但不压成功率、不追人。
- （修订 D-363）`zl_wait_rescue` 的 `takes` 由 16 改为 64：这一步从「陪着」做完算起，离场结算到点才查前提，玩家只在大厅里等时 16 刻在她被扣之前就到点了。到点还没被扣仍作废；作废之后是重新打算，不是接着做「回家」（`docs/content_notes.md` 原先写错了）。
- （修订 D-362）动身前的前提检查只在动身前做，上了路不再查，到了动手时再查；动身前头一次不成立只原地等一个时辰，下个时辰仍不成立才作废。账记在 `Agenda.en_route` 与 `Agenda.doubt` 上，经 `mark_char` 写，换了一步就清掉。D-362 原文的「各查一次」实现成了每个时辰、每段路都查，「当场作废」又赶在眼前刚开场的情境生效之前，这两处是 D-362 的实现与措辞之误。

## 没做完的
