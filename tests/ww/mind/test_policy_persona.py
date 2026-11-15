"""人格一致（ARCHITECTURE 第 10 节）：同一局面、不同随机流重复 20 次，众数占比 ≥ 60%、≥ 90% 落在规则前两名；
相关数值相差 ≥ 0.5 的两个人格，选项分布的总变差距离 ≥ 0.25（每种依赖的性格各一个局面，各取样 200 次）。"""

from __future__ import annotations

from collections import Counter

import pytest

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import Role
from werewolf.testing import Scenario, act, duel_scenario

from .policy_helpers import forked_counts, mind_for, option_counts, persona, tvd

N = 200


@pytest.mark.parametrize('seat,kind,logic', [(7, 'vote', 0.5), (9, 'vote', 0.8), (1, 'vote', 0.5), (5, 'kill', 0.5)])
def test_same_situation_twenty_forks_is_consistent(seat: int, kind: str, logic: float) -> None:
    sc = duel_scenario()
    per = persona('steady_one', logic=logic)
    req = sc.request_vote(seat) if kind == 'vote' else sc.request_kill(seat)
    counts = forked_counts(sc, seat, per, req, 20)
    ranked = sorted((o for o in mind_for(sc, seat, per).all_options(req, sc.view(seat)) if not o.vetoed),
                    key=lambda o: -o.utility)
    top2 = [o.option_id for o in ranked[:2]]
    modal = counts.most_common(1)[0][1]
    assert modal / 20 >= 0.6, counts
    assert sum(counts[o] for o in top2) / 20 >= 0.9, (counts, top2)


def _plan_counts(sc: Scenario, seat: int, per_kw: dict[str, float], req_kind: str = 'day', n: int = N) -> Counter[str]:
    req = sc.request_speak(seat, kind=req_kind)  # type: ignore[arg-type]
    m = mind_for(sc, seat, persona('trait', **per_kw))
    variants = m.speech_variants(req, sc.view(seat))
    return Counter(m.choose_plan(variants)[0].variant for _ in range(n))


def test_conformity_changes_votes() -> None:
    sc = Scenario()
    sc.night(1, kill=None)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.speech(1, 'x', [act('accuse', 6, weight=1)])
    sc.speech(2, 'x', [act('accuse', 8), act('vote_intent', 8)])
    sc.speech(3, 'x', [act('vote_intent', 8)])
    sc.speech(4, 'x', [act('accuse', 6, weight=1)])
    req = sc.request_vote(7)
    view = sc.view(7)
    hi = option_counts(mind_for(sc, 7, persona('c', conformity=0.9)), req, view, N)
    lo = option_counts(mind_for(sc, 7, persona('c', conformity=0.1)), req, view, N)
    assert tvd(hi, lo) >= 0.25, (hi, lo)


def test_boldness_changes_seer_reveal() -> None:
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 7, 'good'), save=True)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    sc.speech(1, 'x', [act('pass')])
    sc.speech(2, 'x', [act('accuse', 5, weight=1)])
    bold = _plan_counts(sc, 3, {'boldness': 0.9})
    shy = _plan_counts(sc, 3, {'boldness': 0.1})
    assert tvd(bold, shy) >= 0.25, (bold, shy)
    assert bold['reveal'] > shy['reveal']


def test_boldness_changes_hunter_shot() -> None:
    sc = duel_scenario()
    req = sc.request_shoot(9)
    view = sc.view(9)
    bold = option_counts(mind_for(sc, 9, persona('h', boldness=0.9)), req, view, N)
    shy = option_counts(mind_for(sc, 9, persona('h', boldness=0.1)), req, view, N)
    assert tvd(bold, shy) >= 0.25 or shy['shoot:none'] - bold['shoot:none'] >= 0.2 * N, (bold, shy)
    assert shy['shoot:none'] > bold['shoot:none']


def test_talkative_changes_passing() -> None:
    sc = Scenario()
    sc.night(1, kill=4, save=True)
    sc.dawn([])
    sc.start_speeches(list(range(1, 10)))
    talky = _plan_counts(sc, 1, {'talkative': 0.95})
    quiet = _plan_counts(sc, 1, {'talkative': 0.05})
    assert tvd(talky, quiet) >= 0.25, (talky, quiet)
    assert quiet['pass'] > talky['pass']


def test_deception_changes_wolf_on_the_pk_stage() -> None:
    sc = duel_scenario()
    sc.speech(1, 'x', [act('accuse', 2), act('vote_intent', 2)])
    sc.speech(2, 'x', [act('accuse', 1, weight=1)])
    sc.speech(3, 'x', [act('accuse', 2), act('vote_intent', 2)])
    sc.vote({1: 2, 2: 1, 3: 2, 5: 1, 7: 2, 9: 1})
    liar = _plan_counts(sc, 2, {'deception': 0.9}, 'pk')
    honest = _plan_counts(sc, 2, {'deception': 0.3}, 'pk')
    assert tvd(liar, honest) >= 0.25, (liar, honest)
    assert liar['fake_claim'] > 0 and honest['fake_claim'] == 0


def test_deception_raises_jump_wish() -> None:
    sc = Scenario()
    sc.day = 1
    req = sc.request_kill(5)
    view = sc.view(5)

    def stances(dec: float) -> Counter[str | None]:
        c: Counter[str | None] = Counter()
        for i in range(N):
            m = mind_for(sc, 5, persona('w', deception=dec), rng=Rng(i, 'stance'), role=Role.WEREWOLF)
            c[m.decide(req, view, m.options(req, view)).stance] += 1
        return c

    hi, lo = stances(0.95), stances(0.05)
    assert hi['jump_seer'] > lo['jump_seer']
