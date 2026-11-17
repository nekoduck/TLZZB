"""终端里的主循环（方案 10.1；ARCHITECTURE 8）。

只认 ``GameLike`` 协议（``game.Game`` 满足它）和 ``ui/events.py`` 的界面事件：

- 终端下用 prompt_toolkit 读中文输入，历史记在 ``runs/`` 下；rich 渲染并流式打印叙述；
- 标准输入不是终端时（管道、脚本测试）退回逐行读取，并把输入回显成「> 输入」；
- 「一 / 二 / 三 / 1 / 2 / 3」与斜杠命令原样交给 ``game.turn``；``/退出`` 与 Ctrl-D 在这里干净地退出。
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, TextIO, runtime_checkable

from tianlong.ui.commands import parse_command
from tianlong.ui.events import PROMPT, Ended, Notice, UIEvent
from tianlong.ui.render import Renderer

HISTORY_FILE = "input_history"
FAREWELL = "就此别过。"
INTERRUPT_HINT = "要离开请输入 /退出，或按 Ctrl-D。"
_FULLWIDTH_DIGITS = str.maketrans("１２３４５６７８９０", "1234567890")


@runtime_checkable
class GameLike(Protocol):
    """界面眼里的游戏（ARCHITECTURE 8）。"""

    def start(self) -> AsyncIterator[UIEvent]: ...

    def turn(self, raw: str) -> AsyncIterator[UIEvent]: ...

    async def aclose(self) -> None: ...


GameFactory = Callable[[], "GameLike | Awaitable[GameLike]"]


# ---------------------------------------------------------------- 读输入


class LineReader:
    """非终端：逐行读文本流；读到头返回 None。"""

    def __init__(self, stream: TextIO, renderer: Renderer, *, echo: bool = True) -> None:
        self.stream = stream
        self.renderer = renderer
        self.echo = echo

    async def read(self) -> str | None:
        line = await asyncio.to_thread(self.stream.readline)
        if line == "":
            return None
        line = line.rstrip("\r\n")
        if self.echo:
            self.renderer.echo_input(line)
        return line


class PromptReader:
    """终端：prompt_toolkit，支持中文输入法与上下键翻历史。Ctrl-D 返回 None，Ctrl-C 清掉这一行。"""

    def __init__(self, history_path: Path | None, *, input: Any = None, output: Any = None) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory, History, InMemoryHistory

        history: History
        if history_path is not None:
            try:
                history_path.parent.mkdir(parents=True, exist_ok=True)
                history = FileHistory(str(history_path))
            except OSError:
                history = InMemoryHistory()
        else:
            history = InMemoryHistory()
        self.session: Any = PromptSession(history=history, input=input, output=output)
        self.interrupted = False

    async def read(self) -> str | None:
        try:
            return await self.session.prompt_async(PROMPT)
        except EOFError:
            return None
        except KeyboardInterrupt:
            self.interrupted = True
            return ""


def history_path(settings: Any) -> Path | None:
    """输入历史放在 ``runs/`` 下（``settings.path('runs')``）。"""
    path_fn = getattr(settings, "path", None)
    if path_fn is None:
        return None
    try:
        return Path(path_fn("runs")) / HISTORY_FILE
    except Exception:
        return None


def normalize_input(raw: str) -> str:
    """去掉首尾空白；全角数字的选项（１２３）换成半角。其余原样交给游戏。"""
    s = raw.strip()
    if len(s) == 1:
        s = s.translate(_FULLWIDTH_DIGITS)
    return s


# ---------------------------------------------------------------- 主循环


async def pump(agen: AsyncIterator[UIEvent], renderer: Renderer, *, debug: bool = False) -> Ended | None:
    """把一串界面事件画出来，返回其中的 ``Ended``（如果有）。

    游戏这边抛了异常也不让界面崩掉：打一句提示，这一回合到此为止。
    """
    ended: Ended | None = None
    renderer.begin_wait()
    try:
        async for ev in agen:
            renderer.end_wait()
            renderer.render(ev)
            if isinstance(ev, Ended):
                ended = ev
    except Exception as e:  # 游戏本身的毛病：界面不跟着倒
        renderer.end_wait()
        renderer.render(Notice(f"出了点岔子（{type(e).__name__}），这一回合没能走完。", "warn"))
        if debug:
            renderer.render(Notice(traceback.format_exc(), "warn"))
    finally:
        renderer.end_wait()
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass
        renderer.finish()
    return ended


async def _make_game(game_factory: GameFactory) -> GameLike:
    g = game_factory()
    if inspect.isawaitable(g):
        g = await g
    return g  # type: ignore[return-value]


def _interactive(stdin: TextIO | None, args: Any) -> bool:
    if getattr(args, "plain", False) or stdin is not None:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


async def run(
    game_factory: GameFactory,
    settings: Any = None,
    args: Any = None,
    *,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    renderer: Renderer | None = None,
    reader: LineReader | PromptReader | None = None,
) -> int:
    """开一局，读输入、画输出，直到 ``/退出``、Ctrl-D 或输入读完。返回退出码。

    ``game_factory()`` 返回一个 ``GameLike``（或它的 awaitable）。``args`` 里认 ``plain``、``debug``、``width``。
    ``stdin`` / ``out`` / ``renderer`` / ``reader`` 给测试用：传了 ``stdin`` 就走非终端的逐行读取。
    """
    debug = bool(getattr(args, "debug", False))
    interactive = _interactive(stdin, args)
    if renderer is None:
        if interactive:
            renderer = Renderer()
        else:
            renderer = Renderer(
                plain=True, out=out if out is not None else sys.stdout, width=getattr(args, "width", None)
            )
    if reader is None:
        if interactive:
            reader = PromptReader(history_path(settings))
        else:
            reader = LineReader(stdin if stdin is not None else sys.stdin, renderer)

    game = await _make_game(game_factory)
    try:
        await pump(game.start(), renderer, debug=debug)
        while True:
            raw = await reader.read()
            if raw is None:  # Ctrl-D 或输入读完
                break
            text = normalize_input(raw)
            if not text:
                if isinstance(reader, PromptReader) and reader.interrupted:
                    reader.interrupted = False
                    renderer.render(Notice(INTERRUPT_HINT))
                continue
            cmd = parse_command(text)
            if cmd is not None and cmd[0] == "退出":
                break
            await pump(game.turn(text), renderer, debug=debug)
    finally:
        try:
            await game.aclose()
        except Exception:
            if debug:
                renderer.render(Notice(traceback.format_exc(), "warn"))
        renderer.render(Notice(FAREWELL))
        renderer.finish()
    return 0


def run_sync(game_factory: GameFactory, settings: Any = None, args: Any = None) -> int:
    """给命令行用的同步入口。"""
    try:
        return asyncio.run(run(game_factory, settings, args))
    except KeyboardInterrupt:
        return 130
