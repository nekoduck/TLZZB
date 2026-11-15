"""平民：说自己的判断、踩头号怀疑并给理由、保信任的人、在对跳里站边、被踩时辩解；没信息时可以划水。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, Request, Role
from werewolf.mind.strategy.common import Ctx, Score, pa, plan, score_vote_town, town_variants
from werewolf.mind.types import ReasonCode, SpeechPlan


def score_vote(ctx: Ctx, request: Request) -> list[Score]:
    return score_vote_town(ctx, request)


def speech_candidates(ctx: Ctx, request: Request) -> list[SpeechPlan]:
    out = town_variants(ctx, request)
    # 被逼到台上或被查杀：报一句「我是平民」再辩解
    if ctx.danger(request) and ctx.cover.role in (None, Role.VILLAGER):
        acc = ctx.accusers()
        acts = [pa(ActKind.CLAIM_ROLE, None, 2, role=Role.VILLAGER),
                pa(ActKind.DEFEND, None, 3, ReasonCode.SELF_PRESERVE)]
        out.append(plan(ctx, request, 'claim_villager', 'defend', acts, 1.2 + 0.4 * (1 - ctx.traits.boldness),
                        [acc[0][0]] if acc else []))
    return out
