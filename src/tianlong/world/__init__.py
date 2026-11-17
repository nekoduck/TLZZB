"""world/：世界时钟、情境、离场结算、图外来人、重新打算、活性、与原著的分岔（方案 8.1 至 8.3、8.6）。

game 接线：

    events += await world.tick(llm, state, defs, decide=minds.decide_one)   # 每过一个时辰
    world.end_of_turn(state, defs)                                          # 每回合一次
    report = await world.run_days(llm, state, defs, 12, lucky=True)          # 基线跑（11.3）
    rows = world.divergences_report(state, defs)                             # 收场时的分岔清单
"""

from tianlong.world.baseline import divergences_report
from tianlong.world.clock import tick
from tianlong.world.offscreen import advance
from tianlong.world.onscene import onscene_elapsed  # noqa: F401  （I6：在场者的「等」，game 在 pass_time 里调用）
from tianlong.world.replan import process_queue
from tianlong.world.run import RunReport, run_days
from tianlong.world.situations import end_of_turn

__all__ = ["tick", "end_of_turn", "advance", "process_queue", "run_days", "RunReport", "divergences_report"]
