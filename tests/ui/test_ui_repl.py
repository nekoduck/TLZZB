"""终端主循环：用一个假的 GameLike 和管道输入（非终端路径）驱动。"""

from __future__ import annotations

import io
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

from tianlong.ui.events import FREE_INPUT_HINT, Break, Ended, Header, Notice, Options, Text, UIEvent
from tianlong.ui.render import Renderer
from tianlong.ui.repl import FAREWELL, GameLike, PromptReader, history_path, normalize_input, run

ROOT = Path(__file__).resolve().parents[2]


class FakeGame:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.inputs: list[str] = []
        self.closed = False
        self.fail_on = fail_on

    async def start(self) -> AsyncIterator[UIEvent]:
        yield Header("第一日 辰时", "练武厅")
        for piece in ("厅上两人", "斗剑正酣。"):
            yield Text(piece)
        yield Break()
        yield Options(["劝一句", "抬头看看", "往门口挪"])

    async def turn(self, raw: str) -> AsyncIterator[UIEvent]:
        self.inputs.append(raw)
        if raw == self.fail_on:
            yield Text("说到一半")
            raise RuntimeError("boom")
        if raw.startswith("/"):
            yield Notice(f"命令：{raw}")
            return
        if raw == "死":
            yield Ended("dead")
            yield Text("后事如此。")
            return
        yield Header("第一日 辰时", "练武厅")
        yield Text(f"你选了「{raw}」。")
        yield Break()
        yield Options(["甲", "乙", "丙"])

    async def aclose(self) -> None:
        self.closed = True


async def _drive(script: str, game: FakeGame | None = None, **kw) -> tuple[FakeGame, str, int]:
    g = game or FakeGame()
    out = io.StringIO()
    code = await run(
        lambda: g, None, SimpleNamespace(plain=True, width=60), stdin=io.StringIO(script), out=out, **kw
    )
    return g, out.getvalue(), code


def test_fake_game_satisfies_protocol() -> None:
    assert isinstance(FakeGame(), GameLike)


async def test_piped_input_passes_through_and_quit() -> None:
    g, out, code = await _drive("一\n2\n\n/状态\n/退出\n不该读到\n")
    assert code == 0
    assert g.inputs == ["一", "2", "/状态"]  # 选项与命令原样交给游戏；空行跳过；/退出 之后不再读
    assert g.closed
    assert out.startswith("〔第一日 辰时 · 练武厅〕\n\n厅上两人斗剑正酣。\n\n一、劝一句\n")
    assert f"{FREE_INPUT_HINT}\n> 一\n\n〔第一日 辰时 · 练武厅〕\n\n你选了「一」。" in out
    assert "> 2\n" in out and "你选了「2」。" in out
    assert "命令：/状态" in out
    assert "> /退出\n" in out
    assert "不该读到" not in out
    assert out.rstrip().endswith(FAREWELL)


async def test_eof_exits_cleanly_and_fullwidth_digits() -> None:
    g, out, code = await _drive("１\n  三  \n")
    assert code == 0 and g.closed
    assert g.inputs == ["1", "三"]


async def test_ime_quit_and_alias_quit() -> None:
    g, _, _ = await _drive("、退出\n一\n")
    assert g.inputs == []
    g, _, _ = await _drive("/quit\n一\n")
    assert g.inputs == []
    g, _, _ = await _drive("／退出\n")
    assert g.inputs == [] and g.closed


async def test_game_error_does_not_crash_ui() -> None:
    g, out, code = await _drive("坏\n一\n", FakeGame(fail_on="坏"))
    assert code == 0
    assert g.inputs == ["坏", "一"]
    assert "说到一半" in out
    assert "【注意】出了点岔子（RuntimeError）" in out
    assert "你选了「一」。" in out


async def test_ended_then_continue() -> None:
    g, out, _ = await _drive("死\n/读档 甲\n")
    assert "—— 收场 ——\n你死了。" in out and "后事如此。" in out
    assert g.inputs == ["死", "/读档 甲"]


async def test_async_factory() -> None:
    g = FakeGame()

    async def factory() -> FakeGame:
        return g

    out = io.StringIO()
    code = await run(factory, None, SimpleNamespace(plain=True), stdin=io.StringIO("一\n"), out=out)
    assert code == 0 and g.inputs == ["一"] and g.closed


def test_history_path_under_runs(tmp_path: Path) -> None:
    from tianlong.config import load_settings

    settings = load_settings(ROOT / "config.toml")
    p = history_path(settings)
    assert p is not None and p.parent == settings.path("runs")
    assert history_path(None) is None
    assert normalize_input(" ２ ") == "2" and normalize_input("我说２句") == "我说２句"


def test_real_stdin_pipe_subprocess(tmp_path: Path) -> None:
    """真的管道：sys.stdin 不是终端时退回逐行读取。"""
    script = tmp_path / "drive.py"
    script.write_text(
        textwrap.dedent(
            """
            from tianlong.ui.events import Header, Options, Text, Break
            from tianlong.ui.repl import run_sync

            class G:
                def __init__(self):
                    self.n = 0
                async def start(self):
                    yield Header("第一日 辰时", "厅")
                    yield Text("开场。")
                    yield Break()
                    yield Options(["一个", "两个", "三个"])
                async def turn(self, raw):
                    self.n += 1
                    yield Text(f"第{self.n}回：{raw}")
                    yield Break()
                async def aclose(self):
                    print("CLOSED")

            raise SystemExit(run_sync(G, None, None))
            """
        ),
        encoding="utf-8",
    )
    import os

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        input="二\n你好\n/退出\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "〔第一日 辰时 · 厅〕" in out
    assert "> 二\n" in out and "第1回：二" in out and "第2回：你好" in out
    assert "CLOSED" in out and FAREWELL in out


async def test_prompt_toolkit_reader_with_pipe_input(tmp_path: Path) -> None:
    """终端路径：prompt_toolkit 读中文输入、记历史；Ctrl-D 退出。"""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    g = FakeGame()
    out = io.StringIO()
    hist = tmp_path / "runs" / "input_history"
    with create_pipe_input() as pipe:
        pipe.send_text("二\r你好，段公子\r\x04")
        reader = PromptReader(hist, input=pipe, output=DummyOutput())
        code = await run(
            lambda: g, None, None, renderer=Renderer(plain=True, out=out, width=60), reader=reader
        )
    assert code == 0 and g.closed
    assert g.inputs == ["二", "你好，段公子"]
    assert "你选了「你好，段公子」。" in out.getvalue()
    assert "你好，段公子" in hist.read_text(encoding="utf-8")
