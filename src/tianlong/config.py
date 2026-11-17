"""读 `config.toml` 与 `.env`，得到 `Settings`（方案 10.3）。

密钥从不存进 `Settings`：只记环境变量的名字，用到时才去环境里取（`jev_api_key()`、
`claude_api_key()`）。这样打印或记录 `Settings` 都不会带出密钥。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

RouteMode = Literal['jev_first', 'claude_first', 'claude_only', 'rules_only']
Effort = Literal['low', 'medium', 'high', 'xhigh', 'max']


class _Base(BaseModel):
    model_config = ConfigDict(extra='allow')


class JevThresholds(_Base):
    choice_confidence: float = 0.55
    choice_margin: float = 0.15
    needs_thought: float = 0.60
    noul_yes: float = 0.60
    sample_floor: float = 0.15
    narration_flag: float = 0.70


class JevSettings(_Base):
    base_url: str = 'https://api.typesafe.ai'
    model: str = 'jev-1.13.0'
    api_key_env: str = 'JEV_API_KEY'
    api_key_env_fallbacks: list[str] = Field(default_factory=list)
    timeout_s: float = 2.0
    max_parallel: int = 16
    lang: Literal['zh', 'en'] = 'zh'
    thresholds: JevThresholds = Field(default_factory=JevThresholds)


class RouteSettings(_Base):
    classify: RouteMode = 'jev_first'
    decide_soul: RouteMode = 'jev_first'
    decide_named: RouteMode = 'jev_first'
    replan: RouteMode = 'jev_first'
    options: RouteMode = 'jev_first'
    check: RouteMode = 'jev_first'
    cards: RouteMode = 'jev_first'
    # 只有 Claude 一条路的三类（没有 Jev 可退）：claude_first / claude_only 都是「调 Claude，失败退模板」，
    # rules_only 是「不调模型」（叙述与选项用模板，管线只跑不用模型的步骤）。
    narrate: RouteMode = 'claude_first'
    phrase: RouteMode = 'claude_first'
    pipeline: RouteMode = 'claude_only'


class ClaudeSettings(_Base):
    api_key_env: str = 'ANTHROPIC_API_KEY'
    api_key_env_fallbacks: list[str] = Field(default_factory=list)
    max_retries: int = 2
    # 服务端拒答兜底（beta server-side-fallback-2026-07-01，fallbacks='default'）。
    # 不支持的模型上服务端照原模型跑，所以对 Haiku 也无害。批处理不能用。
    server_fallback: bool = True


class ModelSpec(_Base):
    model: str
    effort: Effort = 'medium'
    max_tokens: int = 2000
    timeout_s: float = 30.0
    thinking: Literal['between_tools', 'adaptive'] | None = None
    batch: bool = False


class LimitsSettings(_Base):
    session_budget_usd: float = 5.0
    max_reactors: int = 8
    max_micro_rounds: int = 2
    stall_turns: int = 6
    cards_per_day: int = 2
    replan_per_hour: int = 1


class NarrationSettings(_Base):
    min_len: int = 80
    max_len: int = 220
    climax_max_len: int = 400


class CacheSettings(_Base):
    ttl: Literal['5m', '1h'] = '5m'


class Price(_Base):
    """美元每百万 token。"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float | None = None
    cache_write: float | None = None


class PathSettings(_Base):
    content: str = 'content'
    prompts: str = 'prompts'
    corpus: str = 'corpus/tianlong.txt'
    build: str = 'build'
    runs: str = 'runs'
    saves: str = 'saves'


def _default_prices() -> dict[str, Price]:
    return {
        'jev': Price(input=0.042, output=0.0),
        'haiku': Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
        'sonnet': Price(input=2.0, output=10.0, cache_read=0.10, cache_write=2.50),
        'opus': Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0),
    }


class Settings(_Base):
    jev: JevSettings = Field(default_factory=JevSettings)
    route: RouteSettings = Field(default_factory=RouteSettings)
    claude: ClaudeSettings = Field(default_factory=ClaudeSettings)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    narration: NarrationSettings = Field(default_factory=NarrationSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    prices: dict[str, Price] = Field(default_factory=_default_prices)
    paths: PathSettings = Field(default_factory=PathSettings)
    root: Path = Field(default_factory=Path.cwd, exclude=True)

    # ---- 路径 ----
    def path(self, name: str) -> Path:
        """`paths` 里某一项的绝对路径（相对 config.toml 所在目录）。"""
        p = Path(getattr(self.paths, name))
        return p if p.is_absolute() else self.root / p

    # ---- 密钥：只在调用时读环境，不进模型字段 ----
    def jev_api_key(self) -> str | None:
        for name in [self.jev.api_key_env, *self.jev.api_key_env_fallbacks]:
            value = os.environ.get(name, '').strip()
            if value:
                return value
        return None

    def jev_key_source(self) -> str | None:
        """取到 Jev 密钥的环境变量名（不含值），没有就是 None。"""
        for name in [self.jev.api_key_env, *self.jev.api_key_env_fallbacks]:
            if os.environ.get(name, '').strip():
                return name
        return None

    def claude_api_key(self) -> str | None:
        for name in [self.claude.api_key_env, *self.claude.api_key_env_fallbacks]:
            value = os.environ.get(name, '').strip()
            if value:
                return value
        return None


def load_settings(path: str | Path = 'config.toml', *, env_file: str | Path | None = None) -> Settings:
    """读配置。`.env` 默认取 config.toml 同目录下的；已有的环境变量不被覆盖。"""
    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    root = cfg_path.parent
    dotenv_path = Path(env_file) if env_file is not None else root / '.env'
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)
    data: dict[str, object] = {}
    if cfg_path.is_file():
        with cfg_path.open('rb') as f:
            data = tomllib.load(f)
    settings = Settings.model_validate(data)
    settings.root = root
    return settings
