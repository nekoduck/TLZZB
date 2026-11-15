"""存档（方案 10.5）：``saves/<局名>/state.json``，每回合覆盖。

存档文件是一个信封：

```json
{"format": "tianlong-save", "version": 1, "content_hash": "…", "saved_at": "…",
 "tick": 32, "turn": 0, "clock": "第一日 辰时", "player": "某某", "state": {…WorldState…}}
```

- 带版本号与 ``content_hash``。读档时版本低于 ``SAVE_VERSION`` 的，按 ``MIGRATIONS`` 逐级迁移，
  不直接把旧档喂给新的数据模型；版本高于本程序的拒读。
- ``content_hash`` 与当前 ``content/`` 对不上时抛 ``ContentMismatch``，除非 ``allow_mismatch=True``。
- 写入先写临时文件再改名，中途断电也不会留下半个存档。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tianlong.kernel import timeflow
from tianlong.kernel.state import WorldState

SAVE_FORMAT = "tianlong-save"
SAVE_VERSION = 1  # 与 WorldState.version 同步；数据模型有不兼容的改动时加一，并在 MIGRATIONS 里补一级
STATE_FILE = "state.json"
CHECKPOINT_DIR = "checkpoints"

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# MIGRATIONS[n]：把第 n 版的 state 字典改写成第 n+1 版。按版本号从低到高依次套用。
MIGRATIONS: dict[int, Migration] = {}


class SaveError(Exception):
    """存档读写的毛病。"""


class ContentMismatch(SaveError):
    """存档是用另一份 content/ 生成的。"""

    def __init__(self, path: Path, saved: str, current: str) -> None:
        self.path = path
        self.saved = saved
        self.current = current
        super().__init__(
            f"存档 {path} 的内容指纹是 {saved[:12] or '（空）'}，当前 content/ 是 {current[:12]}：内容已经改过。"
            "可以用 allow_mismatch=True 强行读入。"
        )


class SaveVersionError(SaveError):
    """存档版本比本程序新，或缺少迁移函数。"""


def register_migration(from_version: int) -> Callable[[Migration], Migration]:
    """装饰器：登记从 ``from_version`` 升到 ``from_version + 1`` 的迁移函数。"""

    def deco(fn: Migration) -> Migration:
        MIGRATIONS[from_version] = fn
        return fn

    return deco


# ---------------------------------------------------------------- 路径与名字

_BAD_NAME_CHARS = set('/\\:*?"<>|\0')


def check_name(name: str) -> str:
    """存档名：不空、不含路径分隔符和文件系统不认的字符。中文可以。返回去掉首尾空白的名字。"""
    n = (name or "").strip()
    if (
        not n
        or n in (".", "..")
        or any(ch in _BAD_NAME_CHARS or ord(ch) < 32 for ch in n)
        or n.startswith(".")
    ):
        raise SaveError(f'存档名「{name}」不能用：不能为空，不能含 / \\ : * ? " < > | 这些字符。')
    if len(n) > 64:
        raise SaveError("存档名太长了（至多 64 个字）。")
    return n


def save_dir(root: str | Path, name: str) -> Path:
    """``saves/<名字>/``。"""
    return Path(root) / check_name(name)


def state_path(path: str | Path) -> Path:
    """``path`` 是 .json 文件就用它；否则当作存档目录，取其中的 state.json。"""
    p = Path(path)
    return p if p.suffix == ".json" else p / STATE_FILE


# ---------------------------------------------------------------- 写


def _player_name(state: WorldState) -> str:
    c = state.chars.get(state.player)
    return c.name if c is not None else ""


def envelope(state: WorldState, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """存档的信封（不写盘）。"""
    env: dict[str, Any] = {
        "format": SAVE_FORMAT,
        "version": SAVE_VERSION,
        "content_hash": state.content_hash,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tick": state.tick,
        "turn": state.turn,
        "clock": timeflow.clock_text(state.tick),
        "player": _player_name(state),
        "ended": state.ended,
    }
    for k, v in (extra or {}).items():
        if k not in env and k != "state":
            env[k] = v
    data = state.model_dump(mode="json")
    data["version"] = SAVE_VERSION
    env["state"] = data
    return env


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_state(state: WorldState, path: str | Path, *, extra: dict[str, Any] | None = None) -> Path:
    """把状态写进 ``path``（存档目录，或一个 .json 文件），覆盖旧的。返回写入的文件。"""
    target = state_path(path)
    write_json_atomic(target, envelope(state, extra=extra))
    return target


# ---------------------------------------------------------------- 读


@dataclass
class LoadedSave:
    state: WorldState
    meta: dict[str, Any] = field(default_factory=dict)  # 信封里除 state 之外的字段
    migrated_from: int | None = None  # 经过迁移时，原来的版本
    mismatch: bool = False  # content_hash 对不上而被强行读入


def read_envelope(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """读存档文件，返回 (信封字段, state 字典)。兼容没有信封、直接是 WorldState 的 JSON。"""
    p = state_path(path)
    try:
        with p.open(encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError as e:
        raise SaveError(f"找不到存档 {p}") from e
    except (OSError, json.JSONDecodeError) as e:
        raise SaveError(f"存档 {p} 读不出来：{e}") from e
    if not isinstance(raw, dict):
        raise SaveError(f"存档 {p} 的格式不对")
    if isinstance(raw.get("state"), dict):
        meta = {k: v for k, v in raw.items() if k != "state"}
        data = raw["state"]
    elif "chars" in raw and "tick" in raw:
        data = raw
        meta = {"version": raw.get("version", 1), "content_hash": raw.get("content_hash", "")}
    else:
        raise SaveError(f"存档 {p} 的格式不对")
    return meta, data


def migrate(
    data: dict[str, Any],
    version: int,
    *,
    target: int | None = None,
    migrations: dict[int, Migration] | None = None,
) -> dict[str, Any]:
    """把第 ``version`` 版的 state 字典逐级迁移到 ``target``（默认 ``SAVE_VERSION``）。"""
    target = SAVE_VERSION if target is None else target
    migrations = MIGRATIONS if migrations is None else migrations
    if version > target:
        raise SaveVersionError(f"存档是第 {version} 版，比本程序（第 {target} 版）新，读不了。")
    v = version
    while v < target:
        fn = migrations.get(v)
        if fn is None:
            raise SaveVersionError(f"没有从第 {v} 版升到第 {v + 1} 版的迁移函数。")
        data = fn(data)
        v += 1
    data["version"] = target
    return data


def load_save(
    path: str | Path,
    *,
    content_hash: str | None,
    allow_mismatch: bool = False,
) -> LoadedSave:
    """读存档：版本检查与迁移、内容指纹核对，返回 ``LoadedSave``。

    ``content_hash`` 为 None 时不核对指纹。``allow_mismatch=True`` 时指纹对不上也读入，并把状态里的
    ``content_hash`` 换成当前的（这一档从此算作当前内容的存档）。
    """
    p = state_path(path)
    meta, data = read_envelope(p)
    try:
        version = int(meta.get("version", data.get("version", 1)))
    except (TypeError, ValueError) as e:
        raise SaveError(f"存档 {p} 的版本号不对") from e
    migrated_from = version if version != SAVE_VERSION else None
    data = migrate(dict(data), version)
    saved_hash = str(data.get("content_hash") or meta.get("content_hash") or "")
    mismatch = False
    if content_hash is not None and saved_hash != content_hash:
        if not allow_mismatch:
            raise ContentMismatch(p, saved_hash, content_hash)
        mismatch = True
        data["content_hash"] = content_hash
    try:
        state = WorldState.model_validate(data)
    except Exception as e:  # pydantic.ValidationError 等
        raise SaveError(f"存档 {p} 的内容与数据模型对不上：{e}") from e
    return LoadedSave(state=state, meta=meta, migrated_from=migrated_from, mismatch=mismatch)


def load_state(
    path: str | Path,
    *,
    content_hash: str | None,
    allow_mismatch: bool = False,
) -> WorldState:
    """读存档，返回 ``WorldState``。见 ``load_save``。"""
    return load_save(path, content_hash=content_hash, allow_mismatch=allow_mismatch).state


# ---------------------------------------------------------------- 复制与列表


def copy_save(name_from: str, name_to: str, *, root: str | Path, with_checkpoints: bool = True) -> Path:
    """``/存档 名字``：把 ``saves/<name_from>/`` 的当前快照（连同检查点）复制到 ``saves/<name_to>/``。

    目标已有的存档被覆盖；目标原有的检查点先清掉，免得两条时间线混在一起。返回目标目录。
    """
    src = save_dir(root, name_from)
    dst = save_dir(root, name_to)
    if src.resolve() == dst.resolve():
        return dst
    src_state = src / STATE_FILE
    if not src_state.is_file():
        raise SaveError(f"找不到存档「{name_from}」")
    dst.mkdir(parents=True, exist_ok=True)
    tmp = dst / f".tmp-{STATE_FILE}"
    shutil.copyfile(src_state, tmp)
    os.replace(tmp, dst / STATE_FILE)
    if with_checkpoints:
        dst_cp = dst / CHECKPOINT_DIR
        if dst_cp.exists():
            shutil.rmtree(dst_cp)
        src_cp = src / CHECKPOINT_DIR
        if src_cp.is_dir():
            shutil.copytree(src_cp, dst_cp)
    return dst


@dataclass
class SaveInfo:
    name: str
    path: Path
    ok: bool = True
    error: str = ""
    version: int | None = None
    content_hash: str = ""
    saved_at: str = ""
    tick: int | None = None
    turn: int | None = None
    clock: str = ""
    player: str = ""
    ended: str | None = None
    checkpoints: int = 0
    mtime: float = 0.0


def list_saves(root: str | Path) -> list[SaveInfo]:
    """``saves/`` 下的全部存档，最近写过的在前。读不出来的也列出（``ok=False``）。"""
    r = Path(root)
    if not r.is_dir():
        return []
    out: list[SaveInfo] = []
    for d in r.iterdir():
        f = d / STATE_FILE
        if not d.is_dir() or d.name.startswith(".") or not f.is_file():
            continue
        cp = d / CHECKPOINT_DIR
        n_cp = len(list(cp.glob("*.json"))) if cp.is_dir() else 0
        info = SaveInfo(name=d.name, path=d, checkpoints=n_cp, mtime=f.stat().st_mtime)
        try:
            meta, data = read_envelope(f)
            info.version = int(meta.get("version", 1))
            info.content_hash = str(meta.get("content_hash") or data.get("content_hash") or "")
            info.saved_at = str(meta.get("saved_at") or "")
            info.tick = int(meta.get("tick", data.get("tick", 0)))
            info.turn = int(meta.get("turn", data.get("turn", 0)))
            info.clock = str(meta.get("clock") or timeflow.clock_text(info.tick))
            info.player = str(meta.get("player") or "")
            info.ended = meta.get("ended", data.get("ended"))
        except (SaveError, TypeError, ValueError) as e:
            info.ok = False
            info.error = str(e)
        out.append(info)
    out.sort(key=lambda i: i.mtime, reverse=True)
    return out
