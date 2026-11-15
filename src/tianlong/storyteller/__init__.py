"""讲述者（方案 8.4 至 8.7）：牵挂、三选项、节奏与机会牌、年谱、后事。

    storyteller.threads.update(state, defs, events)          # 牵挂
    options = await storyteller.pick_options(llm, state, defs)  # 三个 Option(action, tag, score, rule, thread_ids)
    storyteller.note_choice(state, defs, option)             # 玩家选了哪个（下回合不再给同一个）
    storyteller.pacing.record_turn(state, defs, events)      # 每回合末：张力与停滞的计数
    events = await storyteller.pace(llm, state, defs)        # 停滞时出一张机会牌（J6）
    storyteller.chronicle.record(state, defs, events)        # 年谱
    storyteller.chronicle.check_divergences(state, defs)     # 每个时辰对照一次参照点
    async for piece in storyteller.epilogue(llm, state, defs): ...   # 后事（C7）＋分岔清单

只 import kernel、llm、prompting；所有状态改动都经效果（thread / close_thread / teller / chronicle /
diverge，以及机会牌自己的效果）。
"""

from tianlong.storyteller import chronicle, opportunities, pacing, threads
from tianlong.storyteller.chronicle import render as render_chronicle
from tianlong.storyteller.epilogue import epilogue
from tianlong.storyteller.options import Option, note_action, note_choice, pick_options
from tianlong.storyteller.pacing import pace, record_turn
from tianlong.storyteller.threads import add_wish, remove_wish, render_threads

__all__ = [
    "Option",
    "add_wish",
    "chronicle",
    "epilogue",
    "note_action",
    "note_choice",
    "opportunities",
    "pace",
    "pacing",
    "pick_options",
    "record_turn",
    "remove_wish",
    "render_chronicle",
    "render_threads",
    "threads",
]
