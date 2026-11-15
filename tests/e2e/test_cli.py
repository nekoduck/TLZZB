"""命令行的冒烟测试（方案 10.4）：真的起子进程跑 ``python -m tianlong …``，全程 Mock、不联网。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import CONTENT, REPO

INPUTS = ["一", "我四下看看", "/状态", "我掏出手机报警", "二"]


def _config(tmp_path: Path) -> Path:
    """临时目录里的一份 config.toml：内容库与提示词指向仓库里的，日志与存档写进临时目录。"""
    text = (REPO / "config.toml").read_text(encoding="utf-8")
    for key, value in (
        ("content", CONTENT),
        ("prompts", REPO / "prompts"),
        ("runs", tmp_path / "runs"),
        ("saves", tmp_path / "saves"),
    ):
        text, n = re.subn(rf"(?m)^{key} = '[^']*'$", f"{key} = '{value}'", text)
        assert n == 1, key
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def _run(args: list[str], *, cwd: Path, stdin: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("JEV_API_KEY", "JEV_KEY", "ANTHROPIC_API_KEY")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tianlong", *args],
        cwd=cwd, env=env, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        check=False,
    )


def test_baseline_two_seeds_two_days(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    r = _run(["--config", str(cfg), "baseline", "--seeds", "2", "--days", "2", "--mock"], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "基线跑：2 个种子 × 2 日" in out
    assert len(re.findall(r"(?m)^种子 +\d+：按时 \d+/\d+", out)) == 2
    assert "参照点" in out and re.search(r"(?m)^\w+ +\d+ +\d+/2 ", out)  # 每个参照点一行
    assert "不变量失败 0；停摆 0；异常 0" in out and "结论：通过" in out


def test_piped_play_session_and_replay(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    base = ["--config", str(cfg), "play", "--mock", "--plain", "--seed", "7", "--name", "阿青"]
    r = _run([*base, "--save", "smoke"], cwd=tmp_path, stdin="\n".join([*INPUTS, "/退出"]) + "\n")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    # 开场一屏，加三个耗时的回合，一句不耗时的回话，一条命令
    assert out.count("〔第一日 辰时") >= 4
    assert out.count("一、") >= 5 and out.count("三、") >= 5
    for raw in INPUTS:
        assert f"> {raw}" in out
    assert "【状态】" in out and "阿青" not in out.split("【状态】")[0].split("\n")[0]
    assert "就此别过。" in out and "Traceback" not in out + r.stderr
    assert "--load smoke" in r.stderr
    runs = sorted((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    run = runs[0]
    for name in ("meta.json", "inputs.txt", "calls.jsonl", "events.jsonl", "transcript.md"):
        assert (run / name).is_file(), name
    assert (run / "inputs.txt").read_text(encoding="utf-8").splitlines() == INPUTS
    assert (tmp_path / "saves" / "smoke" / "state.json").is_file()

    # --replay：种子、名字、输入都从那一局取，输出逐字相同（/退出 由界面处理，不在录下的输入里）
    r2 = _run(["--config", str(cfg), "play", "--plain", "--replay", str(run)], cwd=tmp_path, stdin="")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert r2.stdout == out.replace("> /退出\n", "")
    runs2 = [p for p in sorted((tmp_path / "runs").iterdir()) if p != run]
    assert len(runs2) == 1
    assert (runs2[0] / "transcript.md").read_text(encoding="utf-8") == (run / "transcript.md").read_text(
        encoding="utf-8"
    )

    # --load：接着玩
    r3 = _run(
        ["--config", str(cfg), "play", "--mock", "--plain", "--load", "smoke"], cwd=tmp_path, stdin="一\n/退出\n"
    )
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert r3.stdout.count("一、") >= 2 and "就此别过。" in r3.stdout

    # 读不存在的档：说清楚，退出码非零
    r4 = _run(["--config", str(cfg), "play", "--mock", "--plain", "--load", "没有这个档"], cwd=tmp_path, stdin="")
    assert r4.returncode != 0 and "Traceback" not in r4.stderr


def test_eval_stub_lists_datasets() -> None:
    r = _run(["eval", "classify"], cwd=REPO)
    assert r.returncode == 2
    assert "not implemented yet" in r.stdout and "classify.yaml" in r.stdout and "anticheat" in r.stdout


def test_no_command_prints_help() -> None:
    r = _run([], cwd=REPO)
    assert r.returncode == 2 and "play" in r.stdout and "baseline" in r.stdout


@pytest.mark.parametrize("argv", [["pipeline", "--help"], ["play", "--help"], ["baseline", "--help"]])
def test_subcommand_help(argv: list[str]) -> None:
    r = _run(argv, cwd=REPO)
    assert r.returncode == 0 and "--mock" in r.stdout


def test_pipeline_p7_runs_the_validator(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    r = _run(["--config", str(cfg), "pipeline", "p7", "--mock"], cwd=tmp_path)
    assert r.returncode in (0, 1), r.stdout + r.stderr  # 0 通过，1 内容有问题；都说明接线是通的
    assert "P7 校验" in r.stdout and "Traceback" not in r.stderr
    r = _run(["--config", str(cfg), "pipeline", "p9", "--mock"], cwd=tmp_path)
    assert r.returncode == 2
