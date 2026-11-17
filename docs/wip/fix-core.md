# fix-core 进度（终审修补，core 包）

- [done] 1 被拦着追着时 go 要按逃跑判定：danger.blockers（意图冲着他来的捉拿/攻击，或 impeded 的来源；清醒、未被捆扣、看得见他）；resolve.r_go → _flee_to(e, blockers ∪ _pursuers)；r_flee 拆出 _flee_to；actions go 的 desc 加「夺路」。测试 tests/kernel/test_core_fixes.py（go_*）。
- [done] 2 图外的死没有人看见：effects.Run.implicit_about（主体不在图上且本列表没有 emit 就不记事件）；kill 图外不掉落物品；injure/add_status/remove_status/kill 的自带事件都走它。测试 offmap_*。
