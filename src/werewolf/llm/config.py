"""werewolf.toml 的 [werewolf.*] 各节（ARCHITECTURE 6.2）。

公共几节由 `tianlong.config.load_settings` 读；它把不认识的 `[werewolf]` 留在 `model_extra` 里，
这里取出来校验成 `WerewolfSettings`。心智调参 `MindTuning` 的默认值在 `mind/types.py`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tianlong.config import Settings, load_settings
from werewolf.mind.types import MindTuning

Speed = Literal['slow', 'normal', 'fast', 'instant']


class _Section(BaseModel):
    model_config = ConfigDict(extra='ignore')


class RouteWW(_Section):
    """每一类调用走哪条路；失败时自动退到下一档（见 Gateway.route）。"""

    parse: Literal['jev', 'claude', 'rules'] = 'jev'
    decide: Literal['jev', 'rules'] = 'jev'
    speech: Literal['claude', 'jev_select', 'template'] = 'claude'
    check: Literal['jev', 'rules'] = 'jev'
    reflect: Literal['jev', 'rules'] = 'jev'
    monologue: Literal['claude', 'off'] = 'claude'


class CheckWW(_Section):
    block: float = 0.5  # 台词核对：Jev 任一道是非题的概率到这个数就算不过
    injection: float = 0.8  # 解析真人发言：冒充题的概率到这个数才记 injection


class LimitsWW(_Section):
    decide_timeout_s: float = 4.0
    speech_timeout_s: float = 20.0
    max_speech_chars: int = 180
    human_speech_chars: int = 500
    ack_timeout_s: float = 45.0
    confirm_parse: bool = True
    quote_chars: int = 80
    # 以下是本模块自加的超时；werewolf.toml 没写就用默认
    parse_timeout_s: float = 6.0
    check_timeout_s: float = 4.0
    reflect_timeout_s: float = 6.0
    monologue_timeout_s: float = 20.0


class WerewolfSettings(_Section):
    host: str = '127.0.0.1'
    port: int = 8765
    speed: Speed = 'normal'
    open_browser: bool = True
    route: RouteWW = Field(default_factory=RouteWW)
    check: CheckWW = Field(default_factory=CheckWW)
    mind: MindTuning = Field(default_factory=MindTuning)
    limits: LimitsWW = Field(default_factory=LimitsWW)


def from_settings(settings: Settings) -> WerewolfSettings:
    """从 tianlong 的 Settings 里取出 [werewolf.*]；没有这一节就全用默认。"""
    raw: Any = (settings.model_extra or {}).get('werewolf') or {}
    return WerewolfSettings.model_validate(raw)


def load(path: str | Path = 'werewolf.toml') -> tuple[Settings, WerewolfSettings]:
    settings = load_settings(path)
    return settings, from_settings(settings)
