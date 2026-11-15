"""minds 的结果类型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tianlong.kernel.actions import ActionInst, signature
from tianlong.kernel.state import Speech

Via = Literal["jev", "claude", "rule", "routine"]


class MindDecision(BaseModel):
    """一个人这一微轮的抉择：一个身体动作加至多一句话（方案 6.2 末段）。"""

    actor: str
    action: ActionInst
    speech: Speech | None = None
    inner: str = ""  # 此刻的心思，只进日志（C2 给出；其他路为空）
    display: str = ""  # 旁人看得见的神态，给叙述用（C2 给出；其他路为空）
    via: Via = "rule"
    escalated: bool = False  # 本来走 Jev，因没把握或要紧改交 Claude
    important: bool = False  # 内核把这一刻标为要紧（4.3）
    notes: list[str] = []  # 退让与校验的记录，供日志


def idle_action(actor: str) -> ActionInst:
    """动不了的人也得有个动作：等一刻（结算时内核会照常校验）。"""
    params = {"ticks": 1}
    return ActionInst(
        actor=actor, verb="wait", params=params, cost=1, desc="静静等一会儿", signature=signature("wait", params)
    )


__all__ = ["MindDecision", "Via", "idle_action"]
