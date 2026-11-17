"""狼队分工（第 4.2 节）：三只狼各自从对狼可见的事件算出同一份分工；真人头狼报的 stance 优先；
想悍跳的 AI 狼里留座位最小的一只；队友已公开自称预言家后，别的狼不再自称预言家。"""

from __future__ import annotations

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import EventType, Phase, Role
from werewolf.mind.strategy.wolf import pack_plan
from werewolf.testing import Scenario, act

from .policy_helpers import mind_for, persona

WOLVES = (2, 5, 8)


def _night1(stances: dict[int, str], leader: int | None = None, leader_stance: str | None = None) -> Scenario:
    sc = Scenario(human=leader)
    sc.day = 1
    sc.set_phase(Phase.NIGHT_WOLF)
    for w, st in sorted(stances.items()):
        sc.add(EventType.WOLF_PROPOSE, actor=w, target=4, data={'option_id': 'kill:4', 'note': '', 'stance': st},
               vis='wolves')
    by = 'leader' if leader is not None else 'plurality'
    sc.add(EventType.WOLF_KILL, target=4, data={'by': by, 'tally': {'kill:4': len(stances)}, 'stance': leader_stance},
           vis='wolves')
    sc.set_phase(Phase.NIGHT_SEER)
    sc.set_phase(Phase.NIGHT_WITCH)
    sc.dawn([4])
    return sc


def test_three_wolves_derive_the_same_plan() -> None:
    sc = _night1({2: 'charge', 5: 'jump_seer', 8: 'jump_seer'})
    plans = [pack_plan(sc.view(w)) for w in WOLVES]
    assert plans[0] == plans[1] == plans[2] == {2: 'charge', 5: 'jump_seer', 8: 'charge'}
    minds = [mind_for(sc, w, persona(f'w{w}'), rng=Rng(w, 'x')) for w in WOLVES]
    assert all(m.snapshot().pack == plans[0] for m in minds)
    assert pack_plan(sc.view(3)) == {}  # 好人没有分工


def test_nobody_wants_to_jump_means_no_jump() -> None:
    sc = _night1({2: 'deep', 5: 'charge', 8: 'deep'})
    assert set(pack_plan(sc.view(2)).values()) <= {'deep', 'charge'}


def test_human_leader_stance_wins() -> None:
    sc = _night1({2: 'jump_seer', 5: 'deep'}, leader=8, leader_stance='jump_seer')
    plans = [pack_plan(sc.view(w)) for w in WOLVES]
    assert plans[0] == plans[1] == plans[2]
    assert plans[0][8] == 'jump_seer' and plans[0][2] == 'charge' and plans[0][5] == 'deep'
    sc2 = _night1({2: 'jump_seer', 5: 'jump_seer'}, leader=8, leader_stance='deep')
    p2 = pack_plan(sc2.view(5))
    assert p2[8] == 'deep' and p2[2] == 'jump_seer' and p2[5] == 'charge'


def test_public_seer_claim_by_a_mate_fixes_the_jumper() -> None:
    sc = _night1({2: 'jump_seer', 5: 'charge', 8: 'deep'})
    sc.start_speeches([5, 6, 7, 8, 9, 1, 2, 3])
    sc.speech(5, 'x', [act('claim_role', role=Role.SEER), act('claim_check', 3, result='wolf', night=1)])
    plan = pack_plan(sc.view(8))
    assert plan[5] == 'jump_seer' and plan[2] == 'charge'
    # 别的狼不再自称预言家
    for w in (2, 8):
        m = mind_for(sc, w, persona(f'liar{w}', deception=1.0, boldness=1.0))
        for p in m.speech_variants(sc.request_speak(w), sc.view(w)):
            assert not any(a.role == Role.SEER for a in p.acts), (w, p.id)
