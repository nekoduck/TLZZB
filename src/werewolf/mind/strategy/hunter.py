"""猎人：平时藏；将被放逐时可以亮身份威慑；有人冒领猎人时对跳；死时开枪带走头号怀疑，
怀疑度不够高时胆小的会不开。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Request, Role
from werewolf.mind.strategy.common import (
    Ctx,
    Score,
    accuse_acts,
    logit,
    mood_terms,
    pa,
    plan,
    rank_targets,
    score_vote_town,
    town_variants,
)
from werewolf.mind.types import PlannedAct, ReasonCode, SpeechPlan

SHOOT_SLOPE = 3.5
SHOOT_AT = 0.3


def score_vote(ctx: Ctx, request: Request) -> list[Score]:
    return score_vote_town(ctx, request)


def score_shoot(ctx: Ctx, request: Request) -> list[Score]:
    out: list[Score] = []
    for o in request.options:
        sc = Score(o.id, o.label, o.target)
        if o.target is None:
            sc.add(1.4 * (1 - ctx.traits.boldness) - 0.6, 'hold_fire')
            out.append(sc)
            continue
        t = o.target
        p = ctx.p(t)
        if ctx.hard(t):
            if p >= 0.5:
                sc.hard = True
                sc.add(SHOOT_SLOPE * logit(0.99) / 2, 'hard_wolf', t)
            else:
                sc.veto('known_good')
        else:
            sc.add(SHOOT_SLOPE * (p - SHOOT_AT), 'suspect' if p >= 0.4 else 'trusted', t)
            mood_terms(ctx, sc, t)
        out.append(sc)
    return out


def reveal_acts(ctx: Ctx) -> list[PlannedAct]:
    acts: list[PlannedAct] = [pa(ActKind.CLAIM_ROLE, None, 3, role=Role.HUNTER),
                              pa(ActKind.DEFEND, None, 2, ReasonCode.SELF_PRESERVE)]
    impostors = [s for s in ctx.ledger.claimants(Role.HUNTER) if s != ctx.seat and s in ctx.living]
    if impostors:
        s = impostors[0]
        eid = ctx.claim_event(s, Role.HUNTER)
        ev = [eid] if eid else []
        acts.append(pa(ActKind.DOUBT_CLAIM, s, 2, ReasonCode.COUNTER_CLAIM, ev, ReasonCode.COUNTER_CLAIM))
        acts.append(pa(ActKind.ACCUSE, s, 3, ReasonCode.COUNTER_CLAIM, ev, ReasonCode.COUNTER_CLAIM))
        acts.append(pa(ActKind.VOTE_INTENT, s, 2, ReasonCode.COUNTER_CLAIM, ev, ReasonCode.COUNTER_CLAIM))
    else:
        ranked = rank_targets(ctx)
        if ranked:
            acts += accuse_acts(ctx, ranked[0][0])
    return acts


def speech_candidates(ctx: Ctx, request: Request) -> list[SpeechPlan]:
    out = town_variants(ctx, request)
    impostor = any(s != ctx.seat and s in ctx.living for s in ctx.ledger.claimants(Role.HUNTER))
    if ctx.cover.role == Role.HUNTER:
        return [plan(ctx, request, 'reveal', 'reveal', reveal_acts(ctx), 5.0)]
    u = -1.8
    if ctx.danger(request):
        u = 2.6 + 1.0 * ctx.traits.boldness
    if impostor:
        u = max(u, 3.0)
    if request.speech_kind == 'last_words':
        u = max(u, 0.0)
    out.append(plan(ctx, request, 'reveal', 'counter_claim' if impostor else 'reveal', reveal_acts(ctx), u))
    return out
