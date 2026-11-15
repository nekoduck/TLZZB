"""检查点（方案 2.6、8.7）：``saves/<局名>/checkpoints/*.json``。

- 每个游戏日开始时存一个（``maybe_day_start``，一日只存一次）；
- ``danger`` 升到 3 之前存一个（``before_danger``，由调用方判断时机）；
- 只留最近 10 个。

文件名 ``<序号>_<标签>_t<刻>.json``，序号只增不减，按它排新旧；列表不用打开文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tianlong.kernel import timeflow
from tianlong.kernel.state import WorldState
from tianlong.store.save import CHECKPOINT_DIR, SaveError, load_state, save_state

KEEP = 10
DAY_PREFIX = "day"
DANGER_LABEL = "danger"
_NAME_RE = re.compile(r"^(\d+)_(.+)_t(\d+)\.json$")
_LABEL_BAD = re.compile(r"[^\w\-]+", re.UNICODE)


@dataclass(frozen=True)
class CheckpointInfo:
    name: str  # 文件名（含 .json）
    path: Path
    seq: int
    label: str
    tick: int

    @property
    def clock(self) -> str:
        return timeflow.clock_text(self.tick)

    @property
    def day(self) -> int:
        return timeflow.day_of(self.tick)


def _is_day_label(label: str) -> bool:
    return re.fullmatch(rf"{DAY_PREFIX}\d+", label) is not None


def _clean_label(label: str) -> str:
    s = _LABEL_BAD.sub("-", (label or "").strip()).strip("-_")
    return s[:32] or "cp"


class CheckpointManager:
    """一个存档目录下的检查点。"""

    def __init__(self, save_dir: str | Path, *, keep: int = KEEP) -> None:
        self.save_dir = Path(save_dir)
        self.dir = self.save_dir / CHECKPOINT_DIR
        self.keep = max(1, keep)
        self._days_done: set[int] = {i.day for i in self.list() if _is_day_label(i.label)}

    # ---- 列表 ----
    def list(self) -> list[CheckpointInfo]:
        """全部检查点，最新的在前。"""
        if not self.dir.is_dir():
            return []
        out: list[CheckpointInfo] = []
        for p in self.dir.glob("*.json"):
            m = _NAME_RE.match(p.name)
            if m is None:
                continue
            out.append(
                CheckpointInfo(
                    name=p.name, path=p, seq=int(m.group(1)), label=m.group(2), tick=int(m.group(3))
                )
            )
        out.sort(key=lambda i: i.seq, reverse=True)
        return out

    def latest(self) -> CheckpointInfo | None:
        items = self.list()
        return items[0] if items else None

    # ---- 存 ----
    def save(self, state: WorldState, label: str) -> Path:
        """存一个检查点，然后只留最近 ``keep`` 个。返回文件路径。"""
        items = self.list()
        seq = (items[0].seq + 1) if items else 1
        name = f"{seq:04d}_{_clean_label(label)}_t{state.tick}.json"
        path = save_state(state, self.dir / name, extra={"checkpoint": label, "seq": seq})
        self._rotate()
        return path

    def _rotate(self) -> None:
        for info in self.list()[self.keep :]:
            try:
                info.path.unlink()
            except FileNotFoundError:
                pass

    def maybe_day_start(self, state: WorldState) -> Path | None:
        """新的一个游戏日里第一次调用时存一个检查点；同一日再调用什么也不做。"""
        day = timeflow.day_of(state.tick)
        if day in self._days_done:
            return None
        path = self.save(state, f"{DAY_PREFIX}{day}")
        self._days_done.add(day)
        return path

    def before_danger(self, state: WorldState) -> Path | None:
        """``danger`` 将升到 3 之前存一个（调用方判断时机）。同一刻已经存过就不再存。"""
        last = self.latest()
        if last is not None and last.label == DANGER_LABEL and last.tick == state.tick:
            return None
        return self.save(state, DANGER_LABEL)

    # ---- 读 ----
    def find(self, name: str | None = None) -> CheckpointInfo | None:
        """按名字找：完整文件名、去掉 .json 的名字、序号、标签（取最新的那个）；空或 'latest' 取最新的。"""
        items = self.list()
        if not items:
            return None
        key = (name or "").strip()
        if not key or key in ("latest", "最近", "最新"):
            return items[0]
        for i in items:
            if key in (i.name, i.name.removesuffix(".json")):
                return i
        if key.isdigit():
            for i in items:
                if i.seq == int(key):
                    return i
        for i in items:
            if i.label == key:
                return i
        return None

    def restore(
        self, name: str | None = None, *, content_hash: str | None = None, allow_mismatch: bool = False
    ) -> WorldState:
        """读回一个检查点（默认最新的）。之后的「一日一个」从这个检查点的时刻重新算。"""
        info = self.find(name)
        if info is None:
            raise SaveError(f"找不到检查点「{name or '最新'}」")
        state = load_state(info.path, content_hash=content_hash, allow_mismatch=allow_mismatch)
        self._days_done = {i.day for i in self.list() if _is_day_label(i.label) and i.tick <= state.tick}
        return state
