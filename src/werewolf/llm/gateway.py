"""一局游戏共用的模型入口：tianlong 的 LLM 门面 + 路由 + 预算 + 可用性（ARCHITECTURE 6.2）。

调用方只关心两件事：`route(category)` 此刻实际走哪一档；`ask_jev` 带超时地问 Jev。
任何失败都以 `LLMError` 或 `TimeoutError` 抛出，由各适配器退到下一档。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tianlong.config import Settings
from tianlong.llm import LLM, Answers, make_llm
from tianlong.llm.errors import LLMError
from tianlong.llm.specs import claude_spec
from werewolf.llm.config import WerewolfSettings
from werewolf.protocol import LlmStatus

CATEGORIES = ('parse', 'decide', 'speech', 'check', 'reflect', 'monologue')
SPEECH_TIERS = ('claude', 'jev_select', 'template')


class Gateway:
    def __init__(
        self,
        settings: Settings,
        ww: WerewolfSettings,
        *,
        offline: bool = False,
        run_dir: Path | None = None,
        llm: LLM | None = None,
    ) -> None:
        self.settings = settings
        self.ww = ww
        self.offline = offline
        self.run_dir = run_dir
        self.llm: LLM = llm if llm is not None else make_llm(settings, mock=False, run_dir=run_dir)

    # ---- 可用性 ----
    def budget_left(self) -> bool:
        led = self.llm.ledger
        return not (led.budget_usd > 0 and led.spent >= led.budget_usd)

    def jev_usable(self) -> bool:
        return not self.offline and self.llm.jev_available and self.budget_left()

    def claude_usable(self, kind: str | None = None) -> bool:
        if self.offline or not self.llm.claude_available or not self.budget_left():
            return False
        if kind is not None:
            try:
                claude_spec(self.settings, kind)
            except KeyError:
                return False
        return True

    def route(self, category: str) -> str:
        """该类调用此刻实际走哪一档。最后一档总是规则、模板或关闭。"""
        want = str(getattr(self.ww.route, category))
        if category == 'speech':
            tiers = SPEECH_TIERS[SPEECH_TIERS.index(want):]
            for t in tiers:
                if t == 'claude' and self.claude_usable('W_speech'):
                    return t
                if t == 'jev_select' and self.jev_usable():
                    return t
            return 'template'
        if category == 'monologue':
            return 'claude' if want == 'claude' and self.claude_usable('W_monologue') else 'off'
        if category == 'parse':
            order = ['jev', 'claude', 'rules'][['jev', 'claude', 'rules'].index(want):]
            for t in order:
                if t == 'jev' and self.jev_usable():
                    return t
                if t == 'claude' and self.claude_usable('W_parse'):
                    return t
            return 'rules'
        # decide / check / reflect：jev 或 rules
        return 'jev' if want == 'jev' and self.jev_usable() else 'rules'

    def status(self) -> LlmStatus:
        speech = self.route('speech')
        return LlmStatus(
            jev=self.jev_usable(),
            claude=self.claude_usable(),
            speech=speech,  # type: ignore[arg-type]
            offline=self.offline,
        )

    def spent(self) -> float:
        return float(self.llm.ledger.spent)

    # ---- 调用 ----
    async def ask_jev(
        self,
        kind: str,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float | None = None,
    ) -> Answers:
        """问 Jev；失败抛 LLMError，超时抛 TimeoutError。"""
        if not self.jev_usable():
            raise LLMError('jev unavailable')
        coro = self.llm.jev.ask(kind, state, questions)
        if timeout_s is None:
            return await coro
        return await asyncio.wait_for(coro, timeout_s)

    async def aclose(self) -> None:
        await self.llm.aclose()
