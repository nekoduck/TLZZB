"""一局的日志（方案 10.5）：``runs/<id>/`` 下的

- ``events.jsonl``：全量事件，一行一条，含离场事件（``Event`` 的完整 JSON，外加 ``turn``，给了 defs 时再加全知视角的 ``text``）；
- ``transcript.md``：玩家看到的全文，抬头、叙述、选项、提示和玩家的输入，照屏幕上的样子；
- ``calls.jsonl``：由 ``llm`` 的 Recorder 写，这里不碰。

另在内存里留最近几回合玩家看到的文字，供 ``/回顾``。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Event, WorldState

EVENTS_FILE = "events.jsonl"
TRANSCRIPT_FILE = "transcript.md"
CALLS_FILE = "calls.jsonl"  # llm 写
REVIEW_KEEP = 20
INPUT_PREFIX = "> "


def _event_dict(ev: Event | dict[str, Any]) -> dict[str, Any]:
    if isinstance(ev, Event):
        return ev.model_dump(mode="json")
    if isinstance(ev, dict):
        return dict(ev)
    dump = getattr(ev, "model_dump", None)
    if dump is not None:
        return dump(mode="json")
    raise TypeError(f"不认得的事件：{type(ev).__name__}")


class RunLog:
    """一局的日志目录。所有写入都是追加；文件不存在时自动建。"""

    def __init__(self, run_dir: str | Path, *, review_keep: int = REVIEW_KEEP) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / EVENTS_FILE
        self.transcript_path = self.run_dir / TRANSCRIPT_FILE
        self.calls_path = self.run_dir / CALLS_FILE
        self._turns: deque[str] = deque(maxlen=max(1, review_keep))
        self._seen: set[int] = set()

    # ---- events.jsonl ----
    def log_events(
        self,
        events: Iterable[Event | dict[str, Any]],
        *,
        turn: int | None = None,
        state: WorldState | None = None,
        defs: Defs | None = None,
    ) -> int:
        """把事件追加进 events.jsonl，返回写了几条。

        同一个 id 在这个 RunLog 里只写一次（调用方把同一条事件传了两遍也不重复）；读档、回到检查点之后
        事件 id 会从小的重新数，先调 ``reset_seen()``。给了 ``state`` 与 ``defs`` 时附一句全知视角的 ``text``。
        """
        lines: list[str] = []
        for ev in events:
            d = _event_dict(ev)
            eid = d.get("id")
            if isinstance(eid, int):
                if eid in self._seen:
                    continue
                self._seen.add(eid)
            if turn is not None:
                d["turn"] = turn
            if state is not None and defs is not None and d.get("fact_text"):
                try:
                    d["text"] = refs.render(str(d["fact_text"]), state, defs)
                except Exception:  # 渲染只是方便人看，出错不影响记录
                    pass
            lines.append(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        if lines:
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return len(lines)

    def reset_seen(self) -> None:
        """读档或回到检查点之后调用：事件 id 会重新数，去重的记录清空。"""
        self._seen.clear()

    # ---- transcript.md ----
    def log_transcript(self, text: str) -> None:
        """把玩家看到的文字原样追加进 transcript.md（含抬头与选项）。"""
        if not text:
            return
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(text)

    def log_input(self, raw: str) -> str:
        """玩家的输入，照屏幕上「> 输入」的样子记下（后面跟一个空行，与下一段之间的空行一致）。返回写进去的文字。"""
        line = f"{INPUT_PREFIX}{raw.strip()}\n\n"
        self.log_transcript(line)
        return line

    # ---- 一回合 ----
    def log_turn(
        self,
        state: WorldState,
        events: Iterable[Event | dict[str, Any]],
        shown_text: str,
        *,
        player_input: str | None = None,
        defs: Defs | None = None,
    ) -> None:
        """一回合收尾时调用（方案 4.2 第 6 步）：事件进 events.jsonl，玩家看到的文字进 transcript.md 与回顾缓冲。

        ``shown_text`` 一般是 ``tianlong.ui.text.plain_text(本回合的界面事件)``。
        """
        self.log_events(events, turn=state.turn, state=state, defs=defs)
        shown = shown_text or ""
        if shown and not shown.endswith("\n"):
            shown += "\n"
        record = ""
        if player_input is not None and player_input.strip():
            record += self.log_input(player_input)
        if shown:
            self.log_transcript(shown)
            record += shown
        if record.strip():
            self._turns.append(record.rstrip("\n"))

    def remember(self, text: str) -> None:
        """只进回顾缓冲、不写文件（例如开场叙述已经另外记过时）。"""
        if text and text.strip():
            self._turns.append(text.rstrip("\n"))

    def recent(self, n: int = 3) -> list[str]:
        """最近 n 回合玩家看到的文字（旧的在前），给 ``/回顾``。"""
        if n <= 0:
            return []
        return list(self._turns)[-n:]

    @property
    def turns(self) -> list[str]:
        return list(self._turns)


def read_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """读回 events.jsonl（评测与测试用）。"""
    p = Path(run_dir) / EVENTS_FILE
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
