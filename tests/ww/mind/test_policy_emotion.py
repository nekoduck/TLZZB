"""情绪改变决定（第 10 节）：A 是证据上的头号嫌疑，B 连着两天踩我（还投过我）。
记仇 ≥ 0.7 的人格在 tilted 时投 B 的概率比 steady 高 ≥ 20 个百分点；mood_cause 指向一条由真实事件产生的念头。"""

from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.testing import Scenario, act

from .policy_helpers import mind_for, option_counts, persona

A, B, ME = 8, 6, 4


def _scenario() -> tuple[Scenario, int]:
    sc = Scenario()
    sc.night(1, kill=None)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.speech(B, 'x', [act('accuse', ME), act('vote_intent', ME)])
    sc.vote({1: 2, 2: 1, 3: 1, 4: 6, 5: 1, 6: 4, 7: 1, 8: 1, 9: 1}, exiled=1)
    sc.night(2, kill=7, check=(3, A, 'wolf'))
    sc.dawn([7])
    sc.start_speeches([8, 9, 2, 3, 4, 5, 6])
    sc.speech(3, 'x', [act('claim_role', role=Role.SEER), act('claim_check', A, result='wolf', night=2),
                       act('accuse', A, weight=3), act('vote_intent', A)])
    ev = sc.speech(B, 'x', [act('accuse', ME), act('vote_intent', ME)])
    return sc, ev.id


def test_tilted_vengeful_votes_the_grudge_more() -> None:
    sc, eid = _scenario()
    view = sc.view(ME)
    req = sc.request_vote(ME)
    per = persona('vengeful', vengeful=0.8)
    steady = mind_for(sc, ME, per)
    assert steady.mood.state() == 'steady'
    assert steady.beliefs.p(A) > steady.beliefs.p(B)  # 证据上的头号嫌疑是 A
    assert steady.mood.trust(B) <= -0.5
    n = 300
    s_counts = option_counts(steady, req, view, n)
    tilted = mind_for(sc, ME, per)
    tilted.mood.strain(120, f'grudge:{B}', f'{B} 号连着两天踩我', about=B, event_id=eid)
    assert tilted.mood.state() == 'tilted'
    t_counts = option_counts(tilted, req, view, n)
    assert t_counts[f'vote:{B}'] / n - s_counts[f'vote:{B}'] / n >= 0.2, (s_counts, t_counts)
    for _ in range(50):
        d = tilted.decide(req, view, tilted.options(req, view))
        if d.option_id == f'vote:{B}':
            break
    assert d.option_id == f'vote:{B}' and d.state == 'tilted'
    cause = next(t for t in tilted.mood.thoughts() if t.key == d.mood_cause)
    ids = {e.id for e in view.events}
    assert cause.event_id in ids and cause.about == B
    assert sc.events[cause.event_id - 1].actor == B  # 那条事件是 B 自己的言行


def test_low_vengeance_tilt_matters_less() -> None:
    sc, eid = _scenario()
    view = sc.view(ME)
    req = sc.request_vote(ME)
    per = persona('calm', vengeful=0.1)
    m = mind_for(sc, ME, per)
    m.mood.strain(120, f'grudge:{B}', 'x', about=B, event_id=eid)
    hot = mind_for(sc, ME, persona('calm', vengeful=0.9))
    hot.mood.strain(120, f'grudge:{B}', 'x', about=B, event_id=eid)
    n = 200
    assert option_counts(hot, req, view, n)[f'vote:{B}'] > option_counts(m, req, view, n)[f'vote:{B}']


def test_decision_thought_mentions_mood_when_tilted() -> None:
    sc, eid = _scenario()
    m = mind_for(sc, ME, persona('v', vengeful=0.8))
    m.mood.strain(120, f'grudge:{B}', 'x', about=B, event_id=eid)
    d = m.decide(sc.request_vote(ME), sc.view(ME), m.options(sc.request_vote(ME), sc.view(ME)))
    assert '心态崩了' in d.thought
