"""记忆与遗忘（第 10 节）：第 1 天被某人投票，第 2 天的发言计划里，记仇的人格会提起并带那张票作证据；
与当前无关的旧怨（已经和好的人、已经出局的人）不出现；weight ≥ 2 的指认与担保都有证据事件。"""

from __future__ import annotations

from werewolf.kernel.types import ActKind, EventType
from werewolf.mind.speechplan import validate_plan
from werewolf.mind.types import ReasonCode
from werewolf.testing import Scenario, act

from .policy_helpers import mind_for, persona

ME, B, FRIEND, GONE = 4, 6, 9, 5


def _scenario() -> Scenario:
    sc = Scenario()
    sc.night(1, kill=None)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.vote({1: 2, 2: 1, 3: 1, 4: 1, 5: ME, 6: ME, 7: 1, 8: 1, 9: ME}, exiled=1)
    sc.night(2, kill=GONE)
    sc.dawn([GONE])
    sc.start_speeches([6, 7, 8, 9, 2, 3, 4])
    sc.speech(FRIEND, 'x', [act('vouch', ME, weight=3), act('defend', ME, weight=3)])
    sc.speech(3, 'x', [act('vouch', ME, weight=3)])
    return sc


def _vote_id(sc: Scenario, voter: int) -> int:
    return next(e.id for e in sc.events if e.type == EventType.VOTE and e.actor == voter and e.day == 1)


def test_vengeful_brings_up_the_vote_with_evidence() -> None:
    sc = _scenario()
    m = mind_for(sc, ME, persona('grudge', vengeful=0.9))
    plans = m.speech_variants(sc.request_speak(ME), sc.view(ME))
    mentions = [a for p in plans for a in p.acts if a.target == B and a.reason == ReasonCode.VOTED_ME]
    assert mentions, [p.acts for p in plans]
    assert all(_vote_id(sc, B) in a.evidence for a in mentions)
    assert m.memory.has(_vote_id(sc, B))


def test_unrelated_old_grudges_do_not_appear() -> None:
    sc = _scenario()
    m = mind_for(sc, ME, persona('grudge', vengeful=0.9))
    plans = m.speech_variants(sc.request_speak(ME), sc.view(ME))
    for p in plans:
        for a in p.acts:
            if a.reason in (ReasonCode.VOTED_ME, ReasonCode.ACCUSED_ME):
                assert a.target not in (FRIEND, GONE), (p.id, a)


def test_mild_persona_does_not_dwell_on_it() -> None:
    sc = _scenario()
    m = mind_for(sc, ME, persona('mild', vengeful=0.1))
    plans = m.speech_variants(sc.request_speak(ME), sc.view(ME))
    assert not any(a.reason == ReasonCode.VOTED_ME for p in plans for a in p.acts)


def test_heavy_accusations_and_vouches_have_evidence() -> None:
    sc = _scenario()
    for seat in (2, 3, 4, 6, 7, 8, 9):
        m = mind_for(sc, seat, persona(f's{seat}', vengeful=0.9))
        view = sc.view(seat)
        for p in m.speech_variants(sc.request_speak(seat), view):
            assert validate_plan(p, view, m.ledger) == []
            for a in p.acts:
                if a.kind in (ActKind.ACCUSE, ActKind.VOUCH) and a.weight >= 2:
                    assert a.evidence, (seat, p.id, a)
                    assert all(sc.events[e - 1].vis == 'public' for e in a.evidence)


def test_forgotten_events_cannot_be_cited() -> None:
    sc = _scenario()
    m = mind_for(sc, ME, persona('grudge', vengeful=0.9))
    for p in m.speech_variants(sc.request_speak(ME), sc.view(ME)):
        for a in p.acts:
            if a.reason in (ReasonCode.VOTED_ME, ReasonCode.ACCUSED_ME):
                assert all(m.memory.has(e) for e in a.evidence)
