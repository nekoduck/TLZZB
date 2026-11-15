"""预言家：首日大概率起跳报查验（胆量调概率，验到狼更要跳）；有人悍跳必须对跳；之后每天报新查验。
夜里优先验「影响大而身份不明」的人。藏着身份时踩自己验出的狼，动机记 HIDDEN_CHECK，嘴上另找公开理由。"""

from __future__ import annotations

from werewolf.kernel.claims import speech_acts
from werewolf.kernel.types import GODS, ActKind, EventType, Phase, Request, Role, Seat
from werewolf.mind.strategy.common import (
    Ctx,
    Score,
    accuse_acts,
    pa,
    plan,
    rank_targets,
    score_vote_town,
    town_variants,
)
from werewolf.mind.types import PlannedAct, ReasonCode, SpeechPlan

REVEAL_BASE = -0.2  # 首日起跳的基础效用（再加胆量、验到狼、有人悍跳）
FOUND_WOLF = 3.2  # 验到狼时起跳的额外效用（九人局里拿着查杀不跳的预言家几乎没有）


def score_vote(ctx: Ctx, request: Request) -> list[Score]:
    return score_vote_town(ctx, request)


def _activity(ctx: Ctx, s: Seat) -> int:
    n = 0
    for e in ctx.view.events:
        if e.type == EventType.SPEECH and e.actor == s:
            n += sum(1 for a in speech_acts(e) if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.VOUCH))
    return n


def score_check(ctx: Ctx, request: Request) -> list[Score]:
    out: list[Score] = []
    for o in request.options:
        sc = Score(o.id, o.label, o.target)
        t = o.target
        if t is None:
            out.append(sc)
            continue
        p = ctx.p(t)
        sc.add(1.4 * (1 - abs(2 * p - 1)), 'unknown', t)
        sc.add(0.6 * (p - 0.3), 'suspect', t)
        role = ctx.ledger.current_role(t)
        if role == Role.SEER:
            sc.add(-0.5)
        elif role in GODS:
            sc.add(0.5, 'claimed', t)
        sc.add(min(0.6, 0.15 * _activity(ctx, t)), 'active', t)
        out.append(sc)
    return out


def check_evidence(ctx: Ctx, night: int, target: Seat) -> list[int]:
    """说出口的查验理由的证据：以前报过就引那次发言，否则引那一夜的预言家环节。"""
    for c in ctx.ledger.checks_by(ctx.seat):
        if c.target == target:
            return [c.event_id]
    eid = ctx.phase_event(Phase.NIGHT_SEER, night)
    return [eid] if eid is not None else []


def reveal_acts(ctx: Ctx, restate: bool = False) -> tuple[list[PlannedAct], Seat | None]:
    """`restate`：遗言与 PK 台上把报过的查验再报一遍（真人预言家留遗言都这么做）。"""
    claimed = set() if restate else {(c.night, c.target) for c in ctx.cover.checks}
    acts: list[PlannedAct] = [pa(ActKind.CLAIM_ROLE, None, 3, role=Role.SEER)]
    checks = sorted(ctx.view.seer_checks, key=lambda c: c.night)
    for chk in checks:
        if (chk.night, chk.target) not in claimed:
            acts.append(pa(ActKind.CLAIM_CHECK, chk.target, 3, result=chk.result, night=chk.night,
                           motive=ReasonCode.SEER_CHECK))
    wolves = [c for c in checks if c.result == 'wolf' and c.target in ctx.living]
    for c in reversed(wolves):
        acts.append(pa(ActKind.ACCUSE, c.target, 3, ReasonCode.SEER_CHECK, check_evidence(ctx, c.night, c.target),
                       ReasonCode.SEER_CHECK))
    impostors = [s for s in ctx.ledger.claimants(Role.SEER) if s != ctx.seat and s in ctx.living]
    for s in impostors:
        eid = ctx.claim_event(s, Role.SEER)
        ev = [eid] if eid else []
        acts.append(pa(ActKind.DOUBT_CLAIM, s, 2, ReasonCode.COUNTER_CLAIM, ev, ReasonCode.COUNTER_CLAIM))
        acts.append(pa(ActKind.ACCUSE, s, 3, ReasonCode.COUNTER_CLAIM, ev, ReasonCode.COUNTER_CLAIM))
    target: Seat | None = None
    if wolves:
        target = wolves[-1].target
        acts.append(pa(ActKind.VOTE_INTENT, target, 2, ReasonCode.SEER_CHECK,
                       check_evidence(ctx, wolves[-1].night, target), ReasonCode.SEER_CHECK))
    elif impostors:
        target = impostors[0]
        eid = ctx.claim_event(target, Role.SEER)
        acts.append(pa(ActKind.VOTE_INTENT, target, 2, ReasonCode.COUNTER_CLAIM, [eid] if eid else [],
                       ReasonCode.COUNTER_CLAIM))
    else:
        golds = {c.target for c in checks if c.result == 'good'}
        ranked = rank_targets(ctx, 'private', exclude=golds)
        if ranked:
            target = ranked[0][0]
            acts += accuse_acts(ctx, target)
    fresh_goods = [c for c in checks if c.result == 'good' and (c.night, c.target) not in claimed
                   and c.target in ctx.living]
    for c in fresh_goods[-2:]:
        acts.append(pa(ActKind.VOUCH, c.target, 2, ReasonCode.SEER_CHECK, check_evidence(ctx, c.night, c.target),
                       ReasonCode.SEER_CHECK))
    return acts, target


def speech_candidates(ctx: Ctx, request: Request) -> list[SpeechPlan]:
    tr = ctx.traits
    acts, _ = reveal_acts(ctx, restate=request.speech_kind in ('last_words', 'pk'))
    impostor = any(s != ctx.seat for s in ctx.ledger.claimants(Role.SEER))
    found_wolf = any(c.result == 'wolf' for c in ctx.view.seer_checks)
    if ctx.cover.role == Role.SEER:
        return [plan(ctx, request, 'reveal', 'reveal', acts, 6.0)]
    u = REVEAL_BASE + 2.2 * tr.boldness + (FOUND_WOLF if found_wolf else 0.0) + 1.5 * max(0, ctx.day - 1)
    if impostor:
        u += 4.0
    if request.speech_kind in ('last_words', 'pk'):
        u += 3.0
    if ctx.danger(request):
        u += 3.0
    variant = 'counter_claim' if impostor else 'reveal'
    out = [plan(ctx, request, 'reveal', variant, acts, u)]
    hidden = {c.target: c.result for c in ctx.view.seer_checks}
    for p in town_variants(ctx, request, hidden=hidden):
        out.append(p.model_copy(update={'id': f'hide_{p.id}', 'variant': 'hide' if p.variant != 'pass' else 'pass'}))
    return out
