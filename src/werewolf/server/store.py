"""一局的落盘（ARCHITECTURE 第 8 节）：`runs/<game_id>/` 下几份 JSONL。

- `events.jsonl`：全部内核事件（裁判视角，带文字、带原始编号）
- `decisions.jsonl`：每个 AI 的每个决定（含发言方案与台词档）
- `briefs.jsonl`：`{request_id, brief, text, tier, rejected}`
- `wire.jsonl`：`{ts_ms, dir: 'out'|'in', msg}`，与浏览器之间的全部消息
- `calls.jsonl`：模型调用，由 Gateway（tianlong.llm）写，不归这里管
- `meta.json`：种子、真人座位等

这里只写会话层交给它的东西，不碰配置与环境变量，所以不会落下密钥。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel

FILES = ('events', 'decisions', 'briefs', 'wire')


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    return obj


class RunStore:
    """追加写；每行一个 JSON 对象，写完即 flush，进程被杀也只丢最后一行。`run_dir` 为 None 时什么也不写。"""

    def __init__(self, run_dir: Path | None) -> None:
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self._fh: dict[str, IO[str]] = {}
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.run_dir is not None

    def _write(self, name: str, row: dict[str, Any]) -> None:
        if self.run_dir is None:
            return
        fh = self._fh.get(name)
        if fh is None:
            fh = (self.run_dir / f'{name}.jsonl').open('a', encoding='utf-8')
            self._fh[name] = fh
        fh.write(json.dumps(row, ensure_ascii=False, default=_jsonable) + '\n')
        fh.flush()

    def meta(self, data: dict[str, Any]) -> None:
        if self.run_dir is not None:
            (self.run_dir / 'meta.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    def event(self, ev: BaseModel) -> None:
        self._write('events', ev.model_dump(mode='json'))

    def decision(self, row: dict[str, Any]) -> None:
        self._write('decisions', row)

    def brief(self, row: dict[str, Any]) -> None:
        self._write('briefs', row)

    def wire(self, direction: str, msg: BaseModel | dict[str, Any]) -> None:
        self._write('wire', {'ts_ms': int(time.time() * 1000), 'dir': direction, 'msg': _jsonable(msg)})

    def close(self) -> None:
        for fh in self._fh.values():
            try:
                fh.close()
            except OSError:
                pass
        self._fh.clear()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
