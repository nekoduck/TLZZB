"""口径校验（第 4.2 节「口径」）：validate_plan 拒绝每一种不可能成立的声明；合法的方案通过；
发言方案的结构（1 到 4 个、id 唯一、标签、想法、语气）。"""

from __future__ import annotations

import pytest

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.types import ActKind, EventType, Phase, Role
from werewolf.mind.speechplan import validate_plan
from werewolf.mind.types import PRIVATE_REASONS, PlannedAct, ReasonCode, SpeechPlan
from werewolf.testing import duel_scenario

from .policy_helpers import mind_for, persona

SC = duel_scenario()  # 第 2 天白天；3 号自称预言家报过 7 号金水（第 1 夜）；第 1 夜 4 号死，第 2 夜 6 号死


def _plan(seat: int, *acts: PlannedAct) -> SpeechPlan:
    return SpeechPlan(id='t', seat=seat, day=2, kind='day', variant='push', acts=list(acts))


def _a(kind: str, target: int | None = None, **kw: object) -> PlannedAct:
    return PlannedAct(kind=ActKind(kind), target=target, **kw)  # type: ignore[arg-type]


def _errs(plan: SpeechPlan) -> list[str]:
    view = SC.view(plan.seat)
    return validate_plan(plan, view, ClaimLedger.from_events([e for e in view.events if e.vis == 'public']))


def _ev(type_: EventType, **kw: object) -> int:
    for e in SC.events:
        if e.type == type_ and all(getattr(e, k) == v for k, v in kw.items()):
            return e.id
    raise LookupError


SEER_CLAIM = _a('claim_role', role=Role.SEER)
WITCH_CLAIM = _a('claim_role', role=Role.WITCH)

CASES = [
    ('check_without_claim', _plan(9, _a('claim_check', 2, result='wolf', night=1))),
    ('role_change', _plan(5, SEER_CLAIM)),
    ('bad_night', _plan(3, _a('claim_check', 2, result='wolf', night=3))),
    ('check_self', _plan(3, _a('claim_check', 3, result='good', night=2))),
    ('check_dead', _plan(3, _a('claim_check', 4, result='good', night=2))),
    ('check_conflict', _plan(3, _a('claim_check', 7, result='wolf', night=2))),
    ('double_check', _plan(3, _a('claim_check', 9, result='good', night=1))),
    ('false_check', _plan(3, _a('claim_check', 2, result='good', night=2))),
    ('save_died', _plan(9, WITCH_CLAIM, _a('claim_save', 4, night=1))),
    ('poison_alive', _plan(9, WITCH_CLAIM, _a('claim_poison', 1, night=1))),
    ('two_potions', _plan(9, WITCH_CLAIM, _a('claim_save', 1, night=2), _a('claim_poison', 6, night=2))),
    ('double_save', _plan(9, WITCH_CLAIM, _a('claim_save', 1, night=1), _a('claim_save', 7, night=2))),
    ('two_roles', _plan(9, SEER_CLAIM, WITCH_CLAIM)),
    ('claim_werewolf', _plan(9, _a('claim_role', role=Role.WEREWOLF))),
    ('private_reason', _plan(9, _a('accuse', 2, weight=1, reason=ReasonCode.TEAM))),
    ('no_evidence', _plan(9, _a('accuse', 2, weight=2, reason=ReasonCode.QUIET))),
    ('gut_weight', _plan(9, _a('accuse', 2, weight=2, reason=ReasonCode.GUT,
                               evidence=[_ev(EventType.SPEECH, actor=2)]))),
    ('evidence_not_public', _plan(3, _a('accuse', 2, weight=1, reason=ReasonCode.GUT,
                                        evidence=[_ev(EventType.SEER_RESULT, target=2)]))),
    ('evidence_mismatch', _plan(7, _a('accuse', 2, weight=2, reason=ReasonCode.VOTED_ME,
                                      evidence=[_ev(EventType.SPEECH, actor=2)]))),
    ('seer_check_unbacked', _plan(9, _a('accuse', 2, weight=2, reason=ReasonCode.SEER_CHECK,
                                        evidence=[_ev(EventType.PHASE, day=2, phase=Phase.NIGHT_SEER)]))),
]


@pytest.mark.parametrize('code,plan', CASES, ids=[c for c, _ in CASES])
def test_rejects_each_infeasible_claim(code: str, plan: SpeechPlan) -> None:
    errs = _errs(plan)
    assert any(e.startswith(code) for e in errs), errs


def test_accepts_feasible_claims() -> None:
    ns = _ev(EventType.PHASE, day=2, phase=Phase.NIGHT_SEER)
    ok = [
        _plan(3, _a('claim_check', 2, result='wolf', night=2), _a('accuse', 2, weight=3,
                                                                    reason=ReasonCode.SEER_CHECK, evidence=[ns])),
        _plan(3, _a('claim_check', 7, result='good', night=1)),  # 重报旧的
        _plan(9, WITCH_CLAIM, _a('claim_save', 1, night=1)),
        _plan(9, WITCH_CLAIM, _a('claim_poison', 6, night=2)),
        _plan(9, _a('back_claim', 3), _a('vouch', 7, weight=2, reason=ReasonCode.SEER_CHECK,
                                          evidence=[_ev(EventType.SPEECH, actor=3)])),
        _plan(7, _a('accuse', 2, weight=2, reason=ReasonCode.VOTED_ME,
                    evidence=[_ev(EventType.VOTE, actor=2, target=7)])),
        _plan(9, _a('accuse', 2, weight=1, reason=ReasonCode.GUT)),
    ]
    for p in ok:
        assert _errs(p) == [], (p.acts, _errs(p))


@pytest.mark.parametrize('seat', [1, 2, 3, 5, 7, 9])
def test_variants_are_well_formed(seat: int) -> None:
    m = mind_for(SC, seat, persona(f'v{seat}'))
    view = SC.view(seat)
    variants = m.speech_variants(SC.request_speak(seat), view)
    assert 1 <= len(variants) <= 4
    assert len({v.id for v in variants}) == len(variants)
    for v in variants:
        assert v.label and v.thought and v.variant and v.request_id
        assert validate_plan(v, view, m.ledger) == []
        assert not any(a.reason in PRIVATE_REASONS for a in v.acts if a.reason is not None)
        assert v.public().acts == [a for a in v.public().acts]
    plan, d = m.choose_plan(variants)
    assert plan in variants and d.option_id == plan.id and d.kind.value == 'speak'
    assert abs(sum(d.probs.values()) - 1) < 1e-4


def test_hidden_seer_never_says_the_private_reason() -> None:
    m = mind_for(SC, 3, persona('hid', boldness=0.0))
    # 3 号已经自称预言家，只会亮着说；换一个没自称过的预言家视角
    for v in m.speech_variants(SC.request_speak(3), SC.view(3)):
        for a in v.acts:
            assert a.reason not in PRIVATE_REASONS
            if a.motive == ReasonCode.HIDDEN_CHECK:
                assert a.reason is not None and a.reason != ReasonCode.HIDDEN_CHECK
