"""模型调用的门面（ARCHITECTURE 4.1）。上层模块只经它调 Jev 与 Claude。

    llm = make_llm(settings, mock=False, run_dir=Path('runs/<id>'))
    answers = await llm.jev.ask('J2', state_text, questions, hints=..., timeout_s=None)
    obj, meta = await llm.claude.call_json('C2_soul', system_blocks, user_text, Decision, mock_default=...)
    async for piece in llm.claude.stream_text('C3', system_blocks, user_text, mock_text=...): ...

失败一律抛 `LLMError` 的子类，调用方按方案 4.3 的退让表兜底。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tianlong.config import Settings
from tianlong.llm.claude import BatchItem, BatchResult, BatchStatus, CallMeta, ClaudeClient
from tianlong.llm.cost import Ledger
from tianlong.llm.errors import (
    BudgetExceeded,
    ClaudeError,
    JevError,
    JevTimeout,
    LLMError,
    LLMRefusal,
    LLMUnavailable,
    ReplayMismatch,
    RetryableLLMError,
)
from tianlong.llm.jev import Answers, ChoiceAnswer, JevAnswer, JevClient, NoulAnswer, ScoreAnswer
from tianlong.llm.mock import STAR_PREFIX, MockClaude, MockJev
from tianlong.llm.record import CALLS_FILE, Recorder, Replayer
from tianlong.llm.replay import ReplayClaude, ReplayJev
from tianlong.llm.route import mode, sample_choice, should_escalate


@dataclass
class LLM:
    settings: Settings
    jev: JevClient
    claude: ClaudeClient
    recorder: Recorder
    ledger: Ledger
    mock: bool = False
    replay: bool = False
    run_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def mode(self, category: str) -> str:
        """路由取值（config.toml 的 [route]）。"""
        return mode(self.settings, category)

    @property
    def jev_available(self) -> bool:
        return self.mock or self.replay or self.settings.jev_api_key() is not None

    @property
    def claude_available(self) -> bool:
        return self.mock or self.replay or self.settings.claude_api_key() is not None

    async def aclose(self) -> None:
        await self.jev.aclose()
        await self.claude.aclose()

    async def __aenter__(self) -> LLM:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def make_llm(
    settings: Settings,
    *,
    mock: bool,
    run_dir: Path | None,
    replay_dir: Path | None = None,
    on_budget_warn: Callable[[str], None] | None = None,
    strict_replay: bool = True,
) -> LLM:
    """组装门面。`run_dir` 为 None 时只在内存里录制（测试用）。

    `replay_dir` 给出时按其中的 calls.jsonl 返回，不联网、不花钱；否则 `mock=True` 用替身。
    """
    warnings: list[str] = []

    def _warn(msg: str) -> None:
        warnings.append(msg)
        if on_budget_warn is not None:
            on_budget_warn(msg)

    calls_path = run_dir / CALLS_FILE if run_dir is not None else None
    recorder = Recorder(calls_path)
    ledger = Ledger.from_settings(settings, on_warn=_warn)
    jev: JevClient
    claude: ClaudeClient
    if replay_dir is not None:
        replayer = Replayer(replay_dir, strict=strict_replay)
        jev = ReplayJev(settings, replayer, recorder=recorder)
        claude = ReplayClaude(settings, replayer, recorder=recorder)
    elif mock:
        jev = MockJev(settings, recorder=recorder, ledger=ledger)
        claude = MockClaude(settings, recorder=recorder, ledger=ledger)
    else:
        jev = JevClient(settings, recorder=recorder, ledger=ledger)
        claude = ClaudeClient(settings, recorder=recorder, ledger=ledger)
    return LLM(
        settings=settings,
        jev=jev,
        claude=claude,
        recorder=recorder,
        ledger=ledger,
        mock=mock,
        replay=replay_dir is not None,
        run_dir=run_dir,
        warnings=warnings,
    )


__all__ = [
    'LLM',
    'STAR_PREFIX',
    'Answers',
    'BatchItem',
    'BatchResult',
    'BatchStatus',
    'BudgetExceeded',
    'CallMeta',
    'ChoiceAnswer',
    'ClaudeClient',
    'ClaudeError',
    'JevAnswer',
    'JevClient',
    'JevError',
    'JevTimeout',
    'LLMError',
    'LLMRefusal',
    'LLMUnavailable',
    'Ledger',
    'MockClaude',
    'MockJev',
    'NoulAnswer',
    'Recorder',
    'ReplayMismatch',
    'Replayer',
    'RetryableLLMError',
    'ScoreAnswer',
    'make_llm',
    'mode',
    'sample_choice',
    'should_escalate',
]
