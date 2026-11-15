"""提示词模板（方案 10.1）：Claude 的放在 prompts/*.md，Jev 的状态与问题模板放在 prompts/jev/*.md。

模板用 jinja2 渲染。调用方只传模板名（相对 prompts/ 的路径，不带 .md），例如
``render('c3_user', events=...)`` 或 ``render('jev/j2_state', ...)``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import jinja2

_ROOT = Path(__file__).resolve().parents[2] / "prompts"


def set_root(path: str | Path) -> None:
    """测试或打包后改用别处的 prompts/。"""
    global _ROOT
    _ROOT = Path(path)
    _env.cache_clear()


def root() -> Path:
    return _ROOT


@lru_cache(maxsize=1)
def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_ROOT)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=False,
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )


def load(name: str) -> str:
    """原样读出一个模板（system 块常用，不含变量）。"""
    return (_ROOT / f"{name}.md").read_text(encoding="utf-8").strip()


def render(name: str, **ctx: Any) -> str:
    """渲染一个模板；缺变量直接报错，免得把空白送给模型。"""
    return _env().get_template(f"{name}.md").render(**ctx).strip()


def exists(name: str) -> bool:
    return (_ROOT / f"{name}.md").is_file()
