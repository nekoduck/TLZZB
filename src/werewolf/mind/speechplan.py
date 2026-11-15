"""发言计划：把各身份给出的候选方案补全（语气、长短、神态、口径、回应谁、标签、想法），按口径规则校验，
丢掉不过关的，留 1 到 4 个（ARCHITECTURE 4.2「发言计划」「口径」）。

- 语气、长短、神态只由公开心智、人设与方案的公开言语行为决定，所以同一份公开输入下与真实身份无关。
- 「回应谁」：被 QUESTION / ACCUSE 点到的，下一次发言大概率回应（`addressed` 含对方），但不是必然；
  是否回应用 `rng.fork(请求 id)` 的派生流决定，不动原流，所以 `speech_variants` 仍是纯的。
- `validate_plan` 在取样之前对每个方案检查口径；不过关的方案丢弃。
"""

from __future__ import annotations

from collections.abc import Sequence

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import ActKind, Request, Role, Tone, View
from werewolf.mind import explain
from werewolf.mind.strategy.common import Ctx, evidence_ok, pa, plan
from werewolf.mind.types import PRIVATE_REASONS, Length, MindState, PlannedAct, ReasonCode, SpeechPlan, Traits

MAX_VARIANTS = 4
ADDRESS_RATE = 0.7  # 非辩解类方案里顺带回应点我的人的概率
PASS_WHEN_ACCUSED = 1.2  # 被点名时「过」的效用减多少


# --------------------------------------------------------------------------- 语气、长短、神态


def style(state: MindState, traits: Traits, acts: Sequence[PlannedAct]) -> tuple[Tone, str | None]:
    """语气与神态：只看公开心智的状态、人设与公开言语行为。"""
    kinds = {a.kind for a in acts}
    if kinds <= {ActKind.PASS}:
        return (Tone.COLD if traits.talkative < 0.4 else Tone.CALM), ('shrug' if traits.boldness >= 0.5 else 'silent')
    if state == 'tilted':
        return Tone.ANGRY, 'angry'
    if state == 'tense':
        return (Tone.FIRM, 'stare') if traits.boldness >= 0.5 else (Tone.NERVOUS, 'sweat')
    strong = any(a.kind == ActKind.ACCUSE and a.weight >= 3 for a in acts)
    if ActKind.CLAIM_ROLE in kinds or ActKind.CLAIM_CHECK in kinds:
        return (Tone.FIRM, 'stare') if traits.boldness >= 0.5 else (Tone.EARNEST, None)
    if ActKind.DEFEND in kinds and not strong:
        return Tone.EARNEST, 'sigh'
    if state == 'confident':
        return Tone.FIRM, 'nod'
    if strong:
        return Tone.FIRM, 'frown'
    if traits.talkative >= 0.75 and traits.boldness >= 0.5:
        return Tone.JOKING, 'laugh'
    if ActKind.VOUCH in kinds or ActKind.BACK_CLAIM in kinds:
        return Tone.CALM, 'nod'
    return Tone.CALM, 'think'


def length(traits: Traits, acts: Sequence[PlannedAct]) -> Length:
    n = sum(1 for a in acts if a.kind != ActKind.PASS)
    if n == 0:
        return 'short'
    level = 0 if traits.talkative < 0.35 else (2 if traits.talkative >= 0.7 else 1)
    if n >= 5:
        level += 1
    elif n <= 1:
        level -= 1
    return ('short', 'medium', 'long')[max(0, min(2, level))]  # type: ignore[return-value]


# --------------------------------------------------------------------------- 补全


def _cover_role(ctx: Ctx, acts: Sequence[PlannedAct]) -> Role | None:
    for a in acts:
        if a.kind == ActKind.CLAIM_ROLE and a.role is not None:
            return a.role
    return ctx.ledger.current_role(ctx.seat)


def finish(ctx: Ctx, request: Request, plans: Sequence[SpeechPlan]) -> list[SpeechPlan]:
    """补全、校验、去重、截到至多 4 个。全被丢掉时给一个「过」。"""
    pm_state = ctx.mood.state('public')
    accusers = ctx.accusers()
    acc = accusers[0][0] if accusers else None
    r = ctx.rng.fork(f'address:{request.id}')
    roll = r.random()
    out: list[SpeechPlan] = []
    seen: set[str] = set()
    for p in plans:
        if p.id in seen:
            continue
        acts = list(p.acts)
        addressed = list(p.addressed)
        u = p.utility
        if acc is not None and p.variant != 'pass' and acc not in addressed and roll < ADDRESS_RATE:
            addressed.append(acc)
            if not any(a.kind == ActKind.DEFEND and a.target is None for a in acts):
                acts.insert(0, pa(ActKind.DEFEND, None, 1, ReasonCode.SELF_PRESERVE, motive=ReasonCode.SELF_PRESERVE))
        if acc is not None and p.variant == 'pass':
            u -= PASS_WHEN_ACCUSED
        acts = acts[:12]
        tone, emote = style(pm_state, ctx.traits, acts)
        cover = _cover_role(ctx, acts)
        whys = [explain.reason(a.motive or a.reason, a.target) for a in acts if (a.motive or a.reason) is not None]
        reasons = list(dict.fromkeys([*p.reasons, *whys]))[:4]
        q = p.model_copy(update={
            'acts': acts, 'addressed': addressed, 'utility': round(u, 4), 'tone': tone, 'emote': emote,
            'length': length(ctx.traits, acts), 'cover_role': cover, 'reasons': reasons,
            'label': explain.plan_label(p.variant, acts),
            'thought': explain.plan_thought(p.variant, acts, cover),
            'request_id': request.id, 'day': request.day, 'kind': request.speech_kind or 'day',
        })
        if validate_plan(q, ctx.view, ctx.ledger):
            continue
        seen.add(q.id)
        out.append(q)
    out.sort(key=lambda x: (-x.utility, x.id))
    out = out[:MAX_VARIANTS]
    if not out:
        fallback = plan(ctx, request, 'pass', 'pass', [pa(ActKind.PASS, None, 1)], -1.0)
        tone, emote = style(pm_state, ctx.traits, fallback.acts)
        out = [fallback.model_copy(update={'tone': tone, 'emote': emote, 'length': 'short',
                                           'cover_role': ctx.ledger.current_role(ctx.seat),
                                           'label': explain.plan_label('pass', fallback.acts),
                                           'thought': explain.plan_thought('pass', fallback.acts)})]
    return out


# --------------------------------------------------------------------------- 口径校验


def validate_plan(plan: SpeechPlan, view: View, ledger: ClaimLedger) -> list[str]:
    """一个方案的口径问题；空表 = 通过。见 ARCHITECTURE 4.2「口径」。"""
    errs: list[str] = []
    me = plan.seat
    by_id = {e.id: e for e in view.events}
    cur = ledger.current_role(me)
    claimed = [a.role for a in plan.acts if a.kind == ActKind.CLAIM_ROLE and a.role is not None]
    if len(set(claimed)) > 1:
        errs.append('two_roles')
    if claimed and cur is not None and claimed[0] != cur:
        errs.append(f'role_change:{cur.value}->{claimed[0].value}')
    if Role.WEREWOLF in claimed:
        errs.append('claim_werewolf')
    role = claimed[0] if claimed else cur
    backed = {a.target for a in plan.acts if a.kind == ActKind.BACK_CLAIM and a.target is not None}
    plan_checks = [a for a in plan.acts if a.kind == ActKind.CLAIM_CHECK]
    mine = ledger.checks_by(me)
    for a in plan.acts:
        tag = f'{a.kind.value}:{a.target}'
        if a.reason is not None and a.reason in PRIVATE_REASONS:
            errs.append(f'private_reason:{tag}')
        for eid in a.evidence:
            ev = by_id.get(eid)
            if ev is None or ev.vis != 'public':
                errs.append(f'evidence_not_public:{tag}:{eid}')
            elif a.reason is not None and not evidence_ok(a.reason, ev, a.target, me):
                errs.append(f'evidence_mismatch:{tag}:{eid}')
        if a.kind in (ActKind.ACCUSE, ActKind.VOUCH) and a.weight >= 2 and not a.evidence:
            errs.append(f'no_evidence:{tag}')
        if a.reason == ReasonCode.GUT and a.weight > 1:
            errs.append(f'gut_weight:{tag}')
        if a.reason == ReasonCode.SEER_CHECK and a.target is not None:
            t = a.target
            ok = any(c.target == t for c in plan_checks) or any(c.target == t for c in mine)
            ok = ok or any(c.target == t for s in backed for c in ledger.checks_by(s))
            if not ok:
                errs.append(f'seer_check_unbacked:{tag}')
        if a.kind == ActKind.CLAIM_CHECK:
            errs += _check_problems(a, plan, view, ledger, role)
        elif a.kind in (ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON):
            errs += _potion_problems(a, plan, view, ledger)
    return errs


def _night_ok(night: int | None, view: View) -> bool:
    return night is not None and 1 <= night <= view.day


def _check_problems(a: PlannedAct, plan: SpeechPlan, view: View, ledger: ClaimLedger, role: Role | None) -> list[str]:
    me = plan.seat
    tag = f'claim_check:{a.target}'
    out: list[str] = []
    if role != Role.SEER:
        out.append(f'check_without_claim:{tag}')
    if not _night_ok(a.night, view) or a.target is None or a.result is None:
        return [*out, f'bad_night:{tag}']
    t, n = a.target, a.night
    assert n is not None
    if t == me:
        out.append(f'check_self:{tag}')
    died = ledger.death_of(t)
    if died is not None and died[0] < n:
        out.append(f'check_dead:{tag}')
    prior = [(c.night, c.target, c.result) for c in ledger.checks_by(me)]
    prior += [(c.night, c.target, c.result) for c in plan.acts if c.kind == ActKind.CLAIM_CHECK and c is not a]
    if any(pt == t and pr != a.result for _, pt, pr in prior):
        out.append(f'check_conflict:{tag}')
    if any(pn == n and pt != t for pn, pt, _ in prior):
        out.append(f'double_check:{tag}')
    if view.role == Role.SEER and not any(c.night == n and c.target == t and c.result == a.result
                                          for c in view.seer_checks):
        out.append(f'false_check:{tag}')
    return out


def _potion_problems(a: PlannedAct, plan: SpeechPlan, view: View, ledger: ClaimLedger) -> list[str]:
    tag = f'{a.kind.value}:{a.target}'
    if not _night_ok(a.night, view) or a.target is None:
        return [f'bad_night:{tag}']
    n, t = a.night, a.target
    assert n is not None
    out: list[str] = []
    deaths = ledger.dawn_deaths(n)
    kind = 'save' if a.kind == ActKind.CLAIM_SAVE else 'poison'
    if kind == 'save' and deaths is not None and t in deaths:
        out.append(f'save_died:{tag}')
    if kind == 'poison' and (deaths is None or t not in deaths):
        out.append(f'poison_alive:{tag}')
    prior = [(p.kind, p.night, p.target) for p in ledger.potions_by(plan.seat)]
    prior += [('save' if c.kind == ActKind.CLAIM_SAVE else 'poison', c.night, c.target) for c in plan.acts
              if c.kind in (ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON) and c is not a]
    if any(k == kind and pt != t for k, _, pt in prior):
        out.append(f'double_{kind}:{tag}')
    if any(k != kind and pn == n for k, pn, _ in prior):
        out.append(f'two_potions:{tag}')
    return out
