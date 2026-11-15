"""心智状态测试共用的小工具：人格、按座位喂事件。"""

from __future__ import annotations

from typing import Any

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import Event, Role, Seat, strip_text
from werewolf.mind.beliefs import Beliefs
from werewolf.mind.types import Traits
from werewolf.testing import Scenario


def traits(**kw: float) -> Traits:
    base: dict[str, Any] = dict(boldness=0.5, logic=0.5, talkative=0.5, stubborn=0.5, vengeful=0.5,
                                conformity=0.5, deception=0.5)
    base.update(kw)
    return Traits(**base)


def visible(sc: Scenario, seat: Seat, public_only: bool = False) -> list[Event]:
    """该座位看得见的（去掉文字的）事件。"""
    return [strip_text(e) for e in sc.events if sc.visible(e, seat) and (not public_only or e.vis == 'public')]


def beliefs_for(sc: Scenario, seat: Seat, role: Role | None = None, tr: Traits | None = None,
                public_only: bool = False, ledger: ClaimLedger | None = None) -> Beliefs:
    b = Beliefs(seat, role or sc.roles[seat], tr or traits())
    lg = ledger if ledger is not None else ClaimLedger()
    for ev in visible(sc, seat, public_only):
        b.observe(ev, None, lg)
    return b
