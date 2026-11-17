"""效用、候选、取样：纯函数、硬知识领先、否决、Jev 混合、首夜照顾、is_human 对调不变。"""

from __future__ import annotations

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import RequestKind
from werewolf.mind import utility
from werewolf.mind.types import MindTuning, ScoredOption
from werewolf.testing import Scenario, duel_scenario

from .policy_helpers import mind_for, persona


def _seer_day2() -> tuple[Scenario, int]:
    sc = duel_scenario()  # 预言家 3 号第 2 夜验出 2 号是狼
    return sc, 3


def test_options_and_variants_are_pure() -> None:
    sc, seer = _seer_day2()
    m = mind_for(sc, seer)
    view = sc.view(seer)
    req = sc.request_vote(seer)
    before = (m.snapshot().model_dump(), m._decide_rng.state(), m._rng.state())
    a = m.options(req, view)
    b = m.options(req, view)
    sreq = sc.request_speak(seer)
    va = m.speech_variants(sreq, view)
    vb = m.speech_variants(sreq, view)
    assert a == b and va == vb
    assert (m.snapshot().model_dump(), m._decide_rng.state(), m._rng.state()) == before
    assert 1 <= len(va) <= 4 and len({p.id for p in va}) == len(va)


def test_hard_knowledge_leads_by_decisive_gap_and_is_chosen() -> None:
    sc, seer = _seer_day2()
    m = mind_for(sc, seer)
    view = sc.view(seer)
    req = sc.request_vote(seer)
    allopts = m.all_options(req, view)
    hard = [o for o in allopts if o.hard]
    assert [o.option_id for o in hard] == ['vote:2']
    rest = [o.utility for o in allopts if not o.hard and not o.vetoed]
    assert hard[0].utility - max(rest) >= m.tuning.decisive_gap
    # 验过的金水被否决
    assert next(o for o in allopts if o.option_id == 'vote:7').vetoed
    for _ in range(10):
        d = m.decide(req, view, m.options(req, view))
        assert d.option_id == 'vote:2' and d.best_option_id == 'vote:2'


def test_wolf_never_kills_teammate_and_candidates_are_cut() -> None:
    sc = duel_scenario()
    m = mind_for(sc, 5)
    view = sc.view(5)
    req = sc.request_kill(5)
    allopts = m.all_options(req, view)
    assert next(o for o in allopts if o.option_id == 'kill:2').vetoed
    cands = m.options(req, view)
    tun = m.tuning
    assert 1 <= len(cands) <= tun.top_k
    assert all(o.option_id != 'kill:2' and not o.vetoed for o in cands)
    probs = utility.softmax({o.option_id: o.utility for o in allopts if not o.vetoed},
                            utility.temperature(tun, m.mood.value('private')))
    assert all(probs[o.option_id] >= tun.min_rule_prob for o in cands[1:])
    d = m.decide(req, view, cands)
    assert d.note and d.option_id in {o.option_id for o in cands}


def test_jev_mixing_cannot_lift_excluded_option() -> None:
    tun = MindTuning()
    cands = [ScoredOption(option_id='a', label='a', utility=1.0), ScoredOption(option_id='b', label='b', utility=0.5)]
    probs, best, decisive = utility.distribution(cands, tun, 50.0, {'a': 0.0, 'b': 0.0, 'z': 1.0})
    assert set(probs) == {'a', 'b'} and best == 'a' and not decisive
    assert abs(sum(probs.values()) - 1) < 1e-9
    p2, _, _ = utility.distribution(cands, tun, 50.0, {'b': 1.0})
    assert p2['b'] > probs['b']
    # 一锤定音：领先 ≥ decisive_gap 时，Jev 也挪不动
    c3 = [ScoredOption(option_id='a', label='a', utility=5.0), ScoredOption(option_id='b', label='b', utility=1.0)]
    p3, _, dec = utility.distribution(c3, tun, 50.0, {'b': 1.0})
    assert dec and p3 == {'a': 1.0, 'b': 0.0}


def test_temperature_rises_when_mood_is_low() -> None:
    tun = MindTuning()
    assert utility.temperature(tun, 50) == tun.temperature
    assert utility.temperature(tun, 0) == tun.temperature * (1 + tun.mood_temperature)


def _night1(human: int) -> Scenario:
    sc = Scenario(human=human)
    sc.day = 1
    return sc


def test_mercy_only_rescales_the_human_kill_probability() -> None:
    sc = _night1(human=1)
    req = sc.request_kill(5)
    view = sc.view(5)
    m0 = mind_for(sc, 5, tuning=MindTuning(mercy_first_night=0.0))
    m1 = mind_for(sc, 5, tuning=MindTuning(mercy_first_night=0.5))
    c0, c1 = m0.options(req, view), m1.options(req, view)
    assert c0 == c1  # 效用与候选不变
    d0, d1 = m0.decide(req, view, c0), m1.decide(req, view, c1)
    if 'kill:1' in d0.probs and d0.probs['kill:1'] > 0:
        odds0 = d0.probs['kill:1'] / (1 - d0.probs['kill:1'])
        odds1 = d1.probs['kill:1'] / (1 - d1.probs['kill:1'])
        assert abs(odds1 / odds0 - 0.5) < 1e-3
    for k in d0.probs:
        if k != 'kill:1' and d0.probs[k] > 0:
            assert d1.probs[k] >= d0.probs[k] - 1e-9


def test_mercy_applies_only_on_night_one_kill() -> None:
    sc = duel_scenario()  # 白天第 2 天
    sc.night(3, kill=None)
    req = sc.request_kill(5)
    view = sc.view(5)
    m0 = mind_for(sc, 5, tuning=MindTuning(mercy_first_night=0.0), rng=Rng(3, 'x'))
    m1 = mind_for(sc, 5, tuning=MindTuning(mercy_first_night=0.9), rng=Rng(3, 'x'))
    assert m0.decide(req, view, m0.options(req, view)).probs == m1.decide(req, view, m1.options(req, view)).probs


def test_is_human_swap_does_not_change_decisions() -> None:
    outs = []
    for human in (1, 9):
        sc = duel_scenario()
        sc.human = human
        view = sc.view(7)
        assert next(p for p in view.players if p.seat == human).is_human
        m = mind_for(sc, 7, persona('h', vengeful=0.8), rng=Rng(11, 'swap'))
        req = sc.request_vote(7)
        d = m.decide(req, view, m.options(req, view))
        plans = m.speech_variants(sc.request_speak(7), view)
        outs.append((d.option_id, d.probs, [p.model_dump() for p in plans]))
    assert outs[0] == outs[1]


def test_decision_records_rule_best_and_kind() -> None:
    sc, seer = _seer_day2()
    m = mind_for(sc, seer)
    req = sc.request_check(seer)
    d = m.decide(req, sc.view(seer), m.options(req, sc.view(seer)))
    assert d.kind == RequestKind.SEER_CHECK and d.best_option_id and d.thought
    assert d.option_id not in ('check:7', 'check:2')  # 验过的不再验
