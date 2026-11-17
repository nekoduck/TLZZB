"""拒绝与从众（第 10 节）：别人号召投 X 只抬高、不决定。Jev 被替身成永远选被号召的那一项时，
从众 ≤ 0.3 的人格跟票率 ≤ 35%，全体 ≤ 60%；高、低从众相差 ≥ 20 个百分点；X 是自己验出的金水时跟票率为 0。"""

from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.testing import Scenario, act
from werewolf.voice.personas import load_personas

from .policy_helpers import mind_for, option_counts, persona

X = 8
JEV = {f'vote:{X}': 1.0}


def _scenario(check: tuple[int, int, str] | None = None) -> Scenario:
    sc = Scenario()
    sc.night(1, kill=None, check=check)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.speech(1, 'x', [act('accuse', 6)])
    sc.speech(2, 'x', [act('vote_intent', X)])  # 号召
    sc.speech(4, 'x', [act('accuse', 6, weight=1)])
    return sc


def _follow(conf: float, seat: int = 7, n_ids: int = 10, n: int = 50) -> float:
    """十个人设 id（第一感觉各不相同）的平均跟票率。"""
    sc = _scenario()
    req = sc.request_vote(seat)
    view = sc.view(seat)
    tot = 0.0
    for i in range(n_ids):
        m = mind_for(sc, seat, persona(f'id{i}', conformity=conf))
        tot += option_counts(m, req, view, n, JEV)[f'vote:{X}'] / n
    return tot / n_ids


def test_call_raises_but_does_not_determine() -> None:
    low, low3, high = _follow(0.1), _follow(0.3), _follow(0.9)
    assert low <= 0.35 and low3 <= 0.35, (low, low3)
    assert high - low >= 0.2, (low, high)
    # 没有 Jev 时更低：号召只是一点效用
    sc = _scenario()
    req, view = sc.request_vote(7), sc.view(7)
    m = mind_for(sc, 7, persona('id0', conformity=0.9))
    assert option_counts(m, req, view, 200)[f'vote:{X}'] < option_counts(m, req, view, 200, JEV)[f'vote:{X}']


def test_overall_follow_rate_with_real_personas() -> None:
    sc = _scenario()
    req, view = sc.request_vote(7), sc.view(7)
    rates = [option_counts(mind_for(sc, 7, p), req, view, 60, JEV)[f'vote:{X}'] / 60 for p in load_personas()]
    assert sum(rates) / len(rates) <= 0.6, rates


def test_never_follows_a_call_on_own_gold() -> None:
    sc = _scenario(check=(3, X, 'good'))
    req, view = sc.request_vote(3), sc.view(3)
    m = mind_for(sc, 3, persona('seer', conformity=1.0))
    assert m.role == Role.SEER
    assert option_counts(m, req, view, 200, JEV)[f'vote:{X}'] == 0
    assert all(o.option_id != f'vote:{X}' for o in m.options(req, view))


def test_call_on_self_is_not_an_option() -> None:
    sc = Scenario()
    sc.night(1, kill=None)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.speech(2, 'x', [act('vote_intent', 7)])
    req, view = sc.request_vote(7), sc.view(7)
    m = mind_for(sc, 7, persona('self', conformity=1.0))
    counts = option_counts(m, req, view, 100, {'vote:7': 1.0})
    assert counts['vote:7'] == 0
