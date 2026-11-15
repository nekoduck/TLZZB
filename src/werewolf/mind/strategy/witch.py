"""女巫：首夜通常救人，之后看被刀的是谁；毒药留给高度怀疑的人；被逼到悬崖边跳身份报银水自保；
有人冒领女巫时对跳。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Request, Role
from werewolf.mind.strategy.common import (
    Ctx,
    Score,
    accuse_acts,
    mood_terms,
    pa,
    plan,
    rank_targets,
    score_vote_town,
    town_variants,
)
from werewolf.mind.types import PlannedAct, ReasonCode, SpeechPlan

SAVE_FIRST = 2.6  # 首夜救人的效用
POISON_AT = 0.78  # 怀疑度过了它，毒药才值得用
POISON_SLOPE = 6.0


def score_vote(ctx: Ctx, request: Request) -> list[Score]:
    return score_vote_town(ctx, request)


def _gold_of_trusted(ctx: Ctx, t: int) -> bool:
    for c in ctx.ledger.checks_on(t):
        if c.result == 'good' and c.seat != ctx.seat and ctx.ledger.current_role(c.seat) == Role.SEER:
            if ctx.beliefs.claim_trust(c.seat, 'private', Role.SEER, ctx.ledger) >= 0.6:
                return True
    return False


def score_witch(ctx: Ctx, request: Request) -> list[Score]:
    out: list[Score] = []
    victim = request.context.get('victim')
    night = request.day
    for o in request.options:
        sc = Score(o.id, o.label, o.target)
        if o.id == 'save':
            if night <= 1:
                sc.add(SAVE_FIRST, 'save_first', victim)
            else:
                pv = ctx.p(victim) if victim is not None and victim != ctx.seat else 0.0
                sc.add(0.6, 'save', victim)
                sc.add(1.6 * (0.4 - pv), 'trusted' if pv < 0.4 else 'save_suspect', victim)
                if victim is not None and ctx.ledger.current_role(victim) in (Role.SEER, Role.HUNTER):
                    trust = ctx.beliefs.claim_trust(victim, 'private', ctx.ledger.current_role(victim), ctx.ledger)
                    sc.add(2.0 * trust, 'claimed', victim)
                if victim == ctx.seat:
                    sc.add(2.0, 'save', victim)
        elif o.id.startswith('poison:') and o.target is not None:
            t = o.target
            p = ctx.p(t)
            if ctx.hard(t) and p < 0.5:
                sc.veto('known_good')
            elif _gold_of_trusted(ctx, t):
                sc.veto('trusted_gold')
            sc.add(POISON_SLOPE * (p - POISON_AT), 'poison', t)
            mood_terms(ctx, sc, t)
        else:
            sc.add(0.0, 'keep_potion')
        out.append(sc)
    return out


def reveal_acts(ctx: Ctx) -> list[PlannedAct]:
    w = ctx.view.witch
    acts: list[PlannedAct] = [pa(ActKind.CLAIM_ROLE, None, 3, role=Role.WITCH)]
    if w is not None:
        for night, s in w.saved:
            if (night, s) not in ctx.cover.saves:
                acts.append(pa(ActKind.CLAIM_SAVE, s, 2, night=night, motive=ReasonCode.SAVED_BY_WITCH))
        for night, s in w.poisoned:
            if (night, s) not in ctx.cover.poisons:
                acts.append(pa(ActKind.CLAIM_POISON, s, 2, night=night))
    impostors = [s for s in ctx.ledger.claimants(Role.WITCH) if s != ctx.seat and s in ctx.living]
    if impostors:
        s = impostors[0]
        eid = ctx.claim_event(s, Role.WITCH)
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
    impostor = any(s != ctx.seat and s in ctx.living for s in ctx.ledger.claimants(Role.WITCH))
    if ctx.cover.role == Role.WITCH:
        return [plan(ctx, request, 'reveal', 'reveal', reveal_acts(ctx), 5.0)]
    u = -1.5
    if ctx.danger(request):
        u = 2.2 + 0.6 * (1 - ctx.traits.boldness)
    if impostor:
        u = max(u, 3.0)
    if request.speech_kind == 'last_words':
        u = max(u, 0.8)
    out.append(plan(ctx, request, 'reveal', 'counter_claim' if impostor else 'reveal', reveal_acts(ctx), u))
    return out
