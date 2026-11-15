"""`llm/` 的异常。调用方一律 `except LLMError` 后按方案 4.3 的退让表兜底。"""

from __future__ import annotations


class LLMError(Exception):
    """模型调用失败的总类。"""


class LLMUnavailable(LLMError):
    """没有密钥或客户端不可用。游戏应退回规则与模板。"""


class JevError(LLMError):
    """Jev 请求在重试之后仍然失败（超时、非 200、缺字段）。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JevTimeout(JevError):
    """这一次 `jev.ask` 超过了调用方给的时限（`timeout_s`），或被调用方取消。

    录制里记成一条错误行；重放时在同一处再抛出来，后面的调用不会错位。
    """

    def __init__(self, message: str, *, timeout_s: float | None = None) -> None:
        super().__init__(message, status=None)
        self.timeout_s = timeout_s


class ClaudeError(LLMError):
    """Claude 请求在 SDK 重试之后仍然失败（网络、4xx、5xx）。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RetryableLLMError(LLMError):
    """可以再试一次的失败：stop_reason 为 max_tokens、JSON 不合 schema。"""

    def __init__(self, reason: str, *, stop_reason: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stop_reason = stop_reason


class LLMRefusal(LLMError):
    """stop_reason 为 refusal：不重试，直接走兜底（方案 7.4 第 8 条）。"""

    def __init__(self, category: str | None = None, explanation: str | None = None) -> None:
        super().__init__(f'refusal ({category or "unknown"})')
        self.category = category
        self.explanation = explanation


class BudgetExceeded(LLMError):
    """本局花费到了 session_budget_usd：暂停并询问玩家。"""

    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(f'session budget exceeded: ${spent:.4f} of ${budget:.2f}')
        self.spent = spent
        self.budget = budget


class ReplayMismatch(LLMError):
    """重放时录制与本次请求对不上。"""
