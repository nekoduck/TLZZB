"""心智不变量（ARCHITECTURE 4.2）：自对弈里每一步都跑，返回违例的描述，空表示没有。

- 快照里记忆、念头的事件 id 都在视图里；
- 计划的证据都是公开事件，且与理由相符（`VOTED_ME` 对应一条该目标投我的票，等等）；
- 说出口的理由不在 `PRIVATE_REASONS` 里；
- 自己的口径无矛盾（账本里没有自己的矛盾；快照的口径身份与账本一致）；计划本身过口径校验。
"""

from __future__ import annotations

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import View
from werewolf.mind.speechplan import validate_plan
from werewolf.mind.strategy.common import evidence_ok
from werewolf.mind.types import PRIVATE_REASONS, MindSnapshot, SpeechPlan


def check(snapshot: MindSnapshot, plan: SpeechPlan | None, view: View) -> list[str]:
    bad: list[str] = []
    ids = {e.id for e in view.events}
    by_id = {e.id: e for e in view.events}
    for m in snapshot.memories:
        if m.event_id not in ids:
            bad.append(f'memory_event_unseen:{m.event_id}')
    for t in snapshot.thoughts:
        if t.event_id is not None and t.event_id not in ids:
            bad.append(f'thought_event_unseen:{t.key}:{t.event_id}')
    ledger = ClaimLedger.from_events([e for e in view.events if e.vis == 'public'])
    for c in ledger.contradictions(snapshot.seat):
        bad.append(f'own_contradiction:{c.kind}:{c.event_id}')
    if snapshot.cover.role != ledger.current_role(snapshot.seat):
        bad.append(f'cover_mismatch:{snapshot.cover.role}!={ledger.current_role(snapshot.seat)}')
    if plan is not None:
        if plan.seat != snapshot.seat:
            bad.append('plan_seat')
        for a in plan.acts:
            if a.reason is not None and a.reason in PRIVATE_REASONS:
                bad.append(f'private_reason:{a.kind.value}:{a.reason.value}')
            for eid in a.evidence:
                ev = by_id.get(eid)
                if ev is None or ev.vis != 'public':
                    bad.append(f'evidence_not_public:{eid}')
                elif a.reason is not None and not evidence_ok(a.reason, ev, a.target, plan.seat):
                    bad.append(f'evidence_mismatch:{a.kind.value}:{a.reason.value}:{eid}')
        bad += [f'plan:{p}' for p in validate_plan(plan, view, ledger)]
    return bad
