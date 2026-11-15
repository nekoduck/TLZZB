"""狼人：首夜报打法意愿，此后用纯函数 `pack_plan(view)` 从对狼可见的事件推出全队分工。

- 悍跳（jump_seer）：自称预言家，维护一份假查验账（从自己的对外口径读，所以前后一致）。
- 冲锋（charge）：力挺悍跳的队友，攻击真预言家与假查杀的对象。
- 深水（deep）：装平民（用公开层挑目标）；队友保不住时倒钩（动机记 TEAM）。
- 被查杀时辩解或反跳；将被放逐时，演技高的可以冒领猎人或女巫。
- 夜里优先刀跳出来的预言家与女巫、找狼找得准的好人；自家有人悍跳而对跳的另一位还活着时，
  刀他的效用减 seer_kill_bias。

决定用私有层；公开可见的东西（语气、神态、反应）由 speechplan / mind 只从公开层算。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import get_args

from werewolf.kernel.claims import ClaimLedger, speech_acts
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    ActKind,
    EventType,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    Traits,
    View,
    WolfStance,
) if False else None  # noqa: E501  （占位，真正的导入在下面）
from werewolf.kernel.types import ActKind, EventType, Phase, Request, RequestKind, Role, Seat, View, WolfStance
from werewolf.mind.strategy.common import (
    Ctx,
    Score,
    accuse_acts,
    commit_term,
    logit,
    mood_terms,
    pa,
    plan,
    town_variants,
)
from werewolf.mind.types import PlannedAct, ReasonCode, SpeechPlan, Traits

STANCES: tuple[str, ...] = get_args(WolfStance)

# 旋钮
JUMP_BASE = 0.05  # 首夜想悍跳的权重：基础 + 演技 + 胆量（charge、deep 的权重合计约 1）
JUMP_DECEPTION = 0.45
JUMP_BOLD = 0.15
GOLD_MATE_RATE = 0.3  # 悍跳首验给队友发金水的概率（否则查杀一个好人）
KILL_SEER = 2.5
KILL_WITCH = 1.8
KILL_HUNTER = -2.0


def stance_wish(traits: Traits, rng: Rng) -> WolfStance:
    """首夜的打法意愿：按演技与胆量，加自己的随机数。"""
    weights = {
        'jump_seer': JUMP_BASE + JUMP_DECEPTION * traits.deception + JUMP_BOLD * traits.boldness,
        'charge': 0.3 + 0.4 * traits.boldness,
        'deep': 0.3 + 0.4 * (1 - traits.boldness),
    }
    return rng.weighted(weights)  # type: ignore[return-value]


def pack_plan(view: View) -> dict[Seat, WolfStance]:
    """全队分工。只读对狼可见的事件，三只狼各自算出同一份结果（与 view.seat 无关）。

    真人头狼报的 stance 优先；想悍跳的 AI 狼里留座位最小的一只（活着的），其余降为冲锋；
    已有狼公开自称预言家时，就是他悍跳，别的狼不再跳。"""
    if view.wolf is None:
        return {}
    wolves = sorted({view.seat, *view.wolf.teammates})
    wishes: dict[Seat, str] = {}
    leader_stance: str | None = None
    leader_kill_seen = False
    for e in view.events:
        if e.day != 1:
            continue
        if e.type == EventType.WOLF_PROPOSE and e.actor is not None and e.data.get('stance') in STANCES:
            wishes[e.actor] = str(e.data['stance'])
        elif e.type == EventType.WOLF_KILL and e.data.get('by') == 'leader':
            leader_kill_seen = True
            if e.data.get('stance') in STANCES:
                leader_stance = str(e.data['stance'])
    leader: Seat | None = None
    if leader_kill_seen:
        proposers = {e.actor for e in view.events if e.type == EventType.WOLF_PROPOSE and e.day == 1}
        rest = [w for w in wolves if w not in proposers]
        leader = rest[0] if len(rest) == 1 else None
    plan_: dict[Seat, WolfStance] = {w: wishes.get(w, 'deep') for w in wolves}  # type: ignore[misc]
    if leader is not None:
        plan_[leader] = (leader_stance or 'deep')  # type: ignore[assignment]
    ledger = ClaimLedger.from_events([e for e in view.events if e.vis == 'public'])
    claimed = [w for w in ledger.claimants(Role.SEER) if w in wolves]
    alive = set(view.living())
    jumper: Seat | None
    if claimed:
        jumper = claimed[0]
    elif leader is not None and leader_stance == 'jump_seer':
        jumper = leader if leader in alive else None
    else:
        wanting = [w for w in wolves if plan_[w] == 'jump_seer' and w in alive and w != leader]
        jumper = min(wanting) if wanting else None
    for w in wolves:
        if plan_[w] == 'jump_seer' and w != jumper:
            plan_[w] = 'charge'
    if jumper is not None:
        plan_[jumper] = 'jump_seer'
    return plan_


def jumper_of(ctx: Ctx) -> Seat | None:
    for s, st in ctx.pack.items():
        if st == 'jump_seer':
            return s
    return None


def _mates(ctx: Ctx) -> set[Seat]:
    return set(ctx.teammates()) | {ctx.seat}


def real_seer_claimants(ctx: Ctx) -> list[Seat]:
    return [s for s in ctx.ledger.claimants(Role.SEER) if s not in _mates(ctx) and s in ctx.living]


def _threat_logic(ctx: Ctx, t: Seat) -> int:
    mates = _mates(ctx)
    n = 0
    for e in ctx.view.events:
        if e.type == EventType.SPEECH and e.actor == t:
            n += sum(1 for a in speech_acts(e) if a.kind in (ActKind.ACCUSE, ActKind.VOTE_INTENT) and a.target in mates)
        elif e.type == EventType.VOTE and e.actor == t and e.target in mates:
            n += 1
    return n


# --------------------------------------------------------------------------- 夜里刀人


def score_kill(ctx: Ctx, request: Request) -> list[Score]:
    mates = _mates(ctx)
    jumper = jumper_of(ctx)
    jumper_claimed = jumper is not None and ctx.ledger.current_role(jumper) == Role.SEER and jumper in ctx.living
    proposals = request.context.get('proposals') or []
    out: list[Score] = []
    for o in request.options:
        sc = Score(o.id, o.label, o.target, motive=ReasonCode.TEAM)
        t = o.target
        if t is None:
            sc.add(-2.5, 'empty_kill')
            out.append(sc)
            continue
        if t in mates:
            sc.veto('teammate')
            out.append(sc)
            continue
        role = ctx.ledger.current_role(t)
        revealed = ctx.view.known_roles.get(t)
        if revealed == Role.HUNTER or role == Role.HUNTER:
            sc.add(KILL_HUNTER, 'hunter', t)
        elif role == Role.SEER:
            sc.add(KILL_SEER, 'threat_seer', t)
            if jumper_claimed:
                sc.add(-ctx.tuning.seer_kill_bias, 'bias', t)
        elif role == Role.WITCH or any(pc.seat == t for pc in ctx.ledger.potion_claims()):
            sc.add(KILL_WITCH, 'threat_witch', t)
        sc.add(min(1.4, 0.35 * _threat_logic(ctx, t)), 'threat_logic', t)
        for c in ctx.ledger.checks_on(t):
            if c.result == 'good' and c.seat not in mates and ctx.ledger.current_role(c.seat) == Role.SEER:
                sc.add(0.6, 'trusted', t)
                break
        sc.add(-1.0 * (ctx.p(t, 'public') - 0.33), 'unknown', t)
        if ctx.trust(t) < -0.3:
            sc.add(0.3, 'grudge', t)
        n = sum(1 for p in proposals if p.get('target') == t)
        if n:
            sc.add(0.8 * n, 'mate_pick', t)
        out.append(sc)
    return out


# --------------------------------------------------------------------------- 白天投票


def doomed_mate(ctx: Ctx) -> Seat | None:
    """保不住的队友：今天大半号召都冲着他，或被可信的预言家声明查杀。"""
    intents = ctx.intents_today()
    for m in ctx.teammates():
        if m not in ctx.living:
            continue
        calls = [s for s, t in intents.items() if t == m]
        if len(calls) >= 3 and len(calls) * 2 >= max(1, len(intents)):
            return m
        for c in ctx.ledger.checks_on(m):
            if c.result == 'wolf' and c.seat not in _mates(ctx) and c.seat in ctx.living:
                if ctx.beliefs.claim_trust(c.seat, 'public', Role.SEER, ctx.ledger) >= 0.6:
                    return m
    return None


def score_vote(ctx: Ctx, request: Request) -> list[Score]:
    mates = set(ctx.teammates())
    intents = ctx.intents_today()
    n_int = max(1, len(intents))
    bus = doomed_mate(ctx) if ctx.pack.get(ctx.seat) == 'deep' else None
    seers = real_seer_claimants(ctx)
    checkers = {c.seat for m in mates for c in ctx.ledger.checks_on(m) if c.result == 'wolf'}
    out: list[Score] = []
    for o in request.options:
        sc = Score(o.id, o.label, o.target, motive=ReasonCode.TEAM)
        t = o.target
        if t is None:
            sc.add(-2.0, 'abstain')
            out.append(sc)
            continue
        if t in mates:
            if t == bus:
                sc.add(0.4, 'bus', t)
                commit_term(ctx, sc, t, request)
            else:
                sc.veto('teammate')
            out.append(sc)
            continue
        sc.add(0.9 * logit(ctx.p(t, 'public')), 'suspect', t)
        calls = sum(1 for v in intents.values() if v == t)
        sc.add(1.6 * calls / n_int, 'crowd', t)
        if t in seers:
            sc.add(1.0, 'threat_seer', t)
        if t in checkers:
            sc.add(1.2, 'cover', t)
        commit_term(ctx, sc, t, request)
        mood_terms(ctx, sc, t)
        out.append(sc)
    return out


# --------------------------------------------------------------------------- 发言


def _check_evidence(ctx: Ctx, night: int, target: Seat) -> list[int]:
    for c in ctx.ledger.checks_by(ctx.seat):
        if c.target == target:
            return [c.event_id]
    eid = ctx.phase_event(Phase.NIGHT_SEER, night)
    return [eid] if eid is not None else []


def fake_check(ctx: Ctx, request: Request) -> tuple[Seat, str] | None:
    """今晚（第 day 夜）的假查验；口径里这一夜已经报过就不再造。确定性：用 rng.fork(request.id)。"""
    night = ctx.day
    if night < 1 or any(c.night == night for c in ctx.cover.checks):
        return None
    done = {c.target for c in ctx.cover.checks}
    pool = [s for s in ctx.living if s != ctx.seat and s not in done and ctx.alive_at_night(s, night)]
    if not pool:
        return None
    mates = set(ctx.teammates())
    real = [s for s in real_seer_claimants(ctx) if s in pool]
    if real:
        return real[0], 'wolf'
    goods = [s for s in pool if s not in mates]
    golds = [s for s in pool if s in mates]
    r = ctx.rng.fork(f'fake:{request.id}')
    if golds and (not goods or r.random() < GOLD_MATE_RATE):
        return r.choice(sorted(golds)), 'good'
    ranked = sorted(goods, key=lambda s: (-(ctx.p(s, 'public') + 0.3 * _threat_logic(ctx, s)), s))
    return r.choice(ranked[:2]), 'wolf'


def jump_acts(ctx: Ctx, request: Request) -> list[PlannedAct]:
    acts: list[PlannedAct] = [pa(ActKind.CLAIM_ROLE, None, 3, motive=ReasonCode.TEAM, truthful=False, role=Role.SEER)]
    checks = [(c.night, c.target, c.result) for c in ctx.cover.checks]
    new = fake_check(ctx, request)
    if new is not None:
        acts.append(pa(ActKind.CLAIM_CHECK, new[0], 3, motive=ReasonCode.TEAM, truthful=False, result=new[1],
                       night=ctx.day))
        checks.append((ctx.day, new[0], new[1]))
    wolves = [(n, t) for n, t, r in checks if r == 'wolf' and t in ctx.living]
    for n, t in reversed(wolves):
        acts.append(pa(ActKind.ACCUSE, t, 3, ReasonCode.SEER_CHECK, _check_evidence(ctx, n, t), ReasonCode.TEAM,
                       False))
    for s in real_seer_claimants(ctx):
        eid = ctx.claim_event(s, Role.SEER)
        acts.append(pa(ActKind.DOUBT_CLAIM, s, 2, ReasonCode.COUNTER_CLAIM, [eid] if eid else [], ReasonCode.TEAM,
                       False))
    if wolves:
        n, t = wolves[-1]
        acts.append(pa(ActKind.VOTE_INTENT, t, 2, ReasonCode.SEER_CHECK, _check_evidence(ctx, n, t),
                       ReasonCode.TEAM, False))
    else:
        tgt = _push_target(ctx)
        if tgt is not None:
            acts += accuse_acts(ctx, tgt, motive=ReasonCode.TEAM, truthful=False)
    if new is not None and new[1] == 'good' and new[0] in ctx.living:
        acts.append(pa(ActKind.VOUCH, new[0], 2, ReasonCode.SEER_CHECK, _check_evidence(ctx, ctx.day, new[0]),
                       ReasonCode.TEAM, False))
    return acts


def _push_target(ctx: Ctx) -> Seat | None:
    """狼要踩的好人：公开层看着最可疑（说得通），再加威胁。"""
    mates = _mates(ctx)
    goods = [s for s in ctx.living if s not in mates]
    if not goods:
        return None
    seers = set(real_seer_claimants(ctx))
    checkers = {c.seat for m in mates for c in ctx.ledger.checks_on(m) if c.result == 'wolf'}

    def key(s: Seat) -> tuple[float, int]:
        v = ctx.p(s, 'public') + (0.5 if s in seers else 0.0) + (0.5 if s in checkers else 0.0)
        v += 0.1 * _threat_logic(ctx, s)
        return (-v, s)

    return min(goods, key=key)


def charge_acts(ctx: Ctx) -> list[PlannedAct]:
    jumper = jumper_of(ctx)
    acts: list[PlannedAct] = []
    target: Seat | None = None
    if jumper is not None and ctx.ledger.current_role(jumper) == Role.SEER:
        acts.append(pa(ActKind.BACK_CLAIM, jumper, 2, motive=ReasonCode.TEAM, truthful=False))
        hits = [c.target for c in ctx.ledger.checks_by(jumper) if c.result == 'wolf' and c.target in ctx.living]
        if hits:
            target = hits[-1]
    seers = real_seer_claimants(ctx)
    for s in seers:
        eid = ctx.claim_event(s, Role.SEER)
        if len(ctx.ledger.claimants(Role.SEER)) >= 2:
            acts.append(pa(ActKind.DOUBT_CLAIM, s, 2, ReasonCode.COUNTER_CLAIM, [eid] if eid else [],
                           ReasonCode.TEAM, False))
    if target is None:
        target = seers[0] if seers else _push_target(ctx)
    if target is not None:
        acts += accuse_acts(ctx, target, motive=ReasonCode.TEAM, truthful=False, strong=True)
    return acts


def speech_candidates(ctx: Ctx, request: Request) -> list[SpeechPlan]:
    tr = ctx.traits
    stance = ctx.pack.get(ctx.seat, 'deep')
    mates = set(ctx.teammates())
    mate_claimed_seer = any(ctx.ledger.current_role(m) == Role.SEER for m in mates)
    out: list[SpeechPlan] = []
    accusers = ctx.accusers()
    addressed = [accusers[0][0]] if accusers else []
    checkers = [c for c in ctx.checked_wolf_by() if c in ctx.living]
    if ctx.cover.role == Role.SEER:
        return [plan(ctx, request, 'jump', 'jump', jump_acts(ctx, request), 6.0, addressed)]
    can_claim_seer = not mate_claimed_seer and ctx.cover.role is None
    if stance == 'jump_seer' and can_claim_seer:
        u = 2.4 + 0.8 * tr.deception
        out.append(plan(ctx, request, 'jump', 'jump', jump_acts(ctx, request), u, addressed))
    if stance == 'charge':
        out.append(plan(ctx, request, 'charge', 'charge', charge_acts(ctx), 1.7 + 0.4 * tr.boldness, addressed))
    # 深水（也给另两种打法当备选）：装平民，用公开层
    for p in town_variants(ctx, request, layer='public', exclude=mates, motive=ReasonCode.TEAM, truthful=False):
        if stance != 'deep' and p.variant != 'pass':
            p = p.model_copy(update={'utility': round(p.utility - 0.6, 4)})
        out.append(p.model_copy(update={'id': f'deep_{p.id}', 'variant': p.variant}))
    # 倒钩
    bus = doomed_mate(ctx)
    if bus is not None and stance == 'deep':
        acts = accuse_acts(ctx, bus, motive=ReasonCode.TEAM, truthful=True)
        out.append(plan(ctx, request, f'bus:{bus}', 'bus', acts, 1.2 + 1.0 * tr.deception))
    # 被查杀：辩解 / 反跳
    if checkers:
        c = checkers[0]
        eid = next((x.event_id for x in ctx.ledger.checks_on(ctx.seat) if x.seat == c and x.result == 'wolf'), None)
        ev = [eid] if eid else []
        acts = [pa(ActKind.DEFEND, None, 3, ReasonCode.SELF_PRESERVE, motive=ReasonCode.TEAM, truthful=False),
                pa(ActKind.ACCUSE, c, 2, ReasonCode.CONTRADICTION, ev, ReasonCode.TEAM, False),
                pa(ActKind.DOUBT_CLAIM, c, 2, ReasonCode.CONTRADICTION, ev, ReasonCode.TEAM, False),
                pa(ActKind.VOTE_INTENT, c, 2, ReasonCode.CONTRADICTION, ev, ReasonCode.TEAM, False)]
        out.append(plan(ctx, request, 'defend_check', 'defend', acts, 2.0, [c]))
        if can_claim_seer and ctx.day >= 1 and not any(x.night == ctx.day for x in ctx.cover.checks) \
                and ctx.alive_at_night(c, ctx.day):
            ph = ctx.phase_event(Phase.NIGHT_SEER, ctx.day)
            acts2 = [pa(ActKind.CLAIM_ROLE, None, 3, motive=ReasonCode.TEAM, truthful=False, role=Role.SEER),
                     pa(ActKind.CLAIM_CHECK, c, 3, motive=ReasonCode.TEAM, truthful=False, result='wolf',
                        night=ctx.day),
                     pa(ActKind.ACCUSE, c, 3, ReasonCode.SEER_CHECK, [ph] if ph else [], ReasonCode.TEAM, False),
                     pa(ActKind.DOUBT_CLAIM, c, 2, ReasonCode.CONTRADICTION, ev, ReasonCode.TEAM, False),
                     pa(ActKind.VOTE_INTENT, c, 2, ReasonCode.SEER_CHECK, [ph] if ph else [], ReasonCode.TEAM, False)]
            u2 = -0.6 + 2.0 * tr.deception + 1.0 * tr.boldness
            out.append(plan(ctx, request, 'counter_jump', 'counter_claim', acts2, u2, [c]))
    # 将被放逐：冒领猎人或女巫
    if ctx.danger(request) and ctx.cover.role is None and tr.deception >= 0.5:
        fake = _fake_role(ctx)
        if fake is not None:
            acts3 = [pa(ActKind.CLAIM_ROLE, None, 3, motive=ReasonCode.TEAM, truthful=False, role=fake),
                     pa(ActKind.DEFEND, None, 3, ReasonCode.SELF_PRESERVE, motive=ReasonCode.TEAM, truthful=False)]
            tgt = _push_target(ctx)
            if tgt is not None:
                acts3 += accuse_acts(ctx, tgt, motive=ReasonCode.TEAM, truthful=False)
            u3 = 0.4 + 3.0 * (tr.deception - 0.5) + 0.6 * tr.boldness
            out.append(plan(ctx, request, f'fake_{fake.value}', 'fake_claim', acts3, u3, addressed))
    return out


def _fake_role(ctx: Ctx) -> Role | None:
    mates = set(ctx.teammates())
    for role in (Role.HUNTER, Role.WITCH):
        claim = ctx.ledger.claimants(role)
        if not claim and not any(ctx.ledger.current_role(m) == role for m in mates):
            if role == Role.HUNTER and any(r == Role.HUNTER for r in ctx.view.known_roles.values()):
                continue
            return role
    return None


def wolf_requests() -> Mapping[RequestKind, str]:
    return {RequestKind.WOLF_PROPOSE: 'kill', RequestKind.WOLF_KILL: 'kill'}
