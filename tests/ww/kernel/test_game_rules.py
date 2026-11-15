"""ARCHITECTURE 3.1 的规则，一条规则至少一个对局级的测试。身份默认：狼 2、5、8；预 3；女巫 6；猎人 9；民 1、4、7。"""

from __future__ import annotations

from collections import Counter

import pytest

from werewolf.kernel.types import EventType, Phase, RequestKind, Role, Team

from ._util import ROLES, by_type, kinds, new_game, play, script, vote_for, votes, waiting, waiting_speech

# --------------------------------------------------------------------------- 身份与计日


def test_setup_events_and_dealing() -> None:
    g = new_game()
    evs = g.events('judge')
    assert [e.type for e in evs[:11]] == [EventType.GAME_START] + [EventType.ROLE_DEALT] * 9 + [EventType.WOLF_TEAM]
    assert all(e.day == 0 and e.phase == Phase.SETUP for e in evs[:11])
    assert evs[0].data['role_counts'] == {'werewolf': 3, 'villager': 3, 'seer': 1, 'witch': 1, 'hunter': 1}
    for e in evs[1:10]:
        assert e.vis == 'seats' and e.vis_seats == [e.actor] and e.data['role'] == ROLES[e.actor].value
    assert evs[10].data == {'wolves': [2, 5, 8]} and evs[10].vis == 'wolves'


def test_random_deal_uses_config_multiset_and_is_seeded() -> None:
    from werewolf.kernel.engine import Game
    from werewolf.kernel.types import GameConfig

    a, b = Game.new(GameConfig(), 42), Game.new(GameConfig(), 42)
    ra = {s: a.role_of(s) for s in range(1, 10)}
    assert ra == {s: b.role_of(s) for s in range(1, 10)}
    assert Counter(ra.values()) == Counter(GameConfig().roles)
    deals = {tuple(Game.new(GameConfig(), s).role_of(i) for i in range(1, 10)) for s in range(20)}
    assert len(deals) > 10


def test_given_roles_are_validated() -> None:
    bad = dict(ROLES)
    bad[1] = Role.WEREWOLF
    with pytest.raises(ValueError):
        new_game(bad)
    with pytest.raises(ValueError):
        new_game({s: r for s, r in ROLES.items() if s != 9})
    with pytest.raises(ValueError):
        new_game(seed=-1)


def test_day_numbering() -> None:
    g = new_game()
    play(g, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    evs = g.events('judge')
    night1 = [e for e in evs[:-1] if e.phase == Phase.NIGHT_WOLF]
    assert night1 and all(e.day == 1 for e in night1)
    assert {e.day for e in evs if e.phase == Phase.DAY_SPEECH} == {1}
    assert evs[-1].day == 2 and evs[-1].data == {'phase': 'night_wolf', 'day': 2}
    assert all(r.day == 2 for r in g.pending())


# --------------------------------------------------------------------------- 胜负


def test_wolves_win_when_villagers_dead() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:1', ('wolf_propose', 2, None): 'kill:4',
                ('vote', 1, None): vote_for(7)})
    play(g, d)
    assert g.result is not None and g.result.winner == Team.WOLF and g.result.reason == 'villagers_dead'
    assert g.result.day == 2 and sorted(g.result.survivors) == [2, 3, 5, 6, 8, 9]
    over = g.events('judge')[-1]
    assert over.type == EventType.GAME_OVER and over.data['winner'] == 'wolf'
    assert over.data['roles'] == [{'seat': s, 'role': ROLES[s].value} for s in range(1, 10)]


def test_wolves_win_when_gods_dead() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:3', ('wolf_propose', 2, None): 'kill:6',
                ('vote', 1, None): vote_for(9), ('hunter_shoot', None, None): 'shoot:none'})
    play(g, d)
    assert g.result is not None and (g.result.winner, g.result.reason) == (Team.WOLF, 'gods_dead')


def test_good_wins_when_wolves_dead_and_hunter_last_god_turns_it() -> None:
    """猎人是最后一个神：被放逐后一枪带走最后一只狼，好人胜（狼全死优先于神全死）。"""
    g = new_game()
    d = script({
        ('wolf_propose', 1, None): 'kill:3', ('vote', 1, None): vote_for(6),
        ('wolf_propose', 2, None): 'kill:1', ('vote', 2, None): vote_for(2),
        ('wolf_propose', 3, None): 'kill:4', ('vote', 3, None): vote_for(5),
        ('wolf_propose', 4, None): 'kill:none', ('vote', 4, None): vote_for(9),
        ('hunter_shoot', 4, 9): 'shoot:8',
    })
    play(g, d)
    assert g.result is not None and (g.result.winner, g.result.reason) == (Team.GOOD, 'wolves_dead')
    assert g.result.survivors == [7]
    tail = kinds(g.events('judge'))[-9:]
    assert tail == ['exile', 'death', 'death_cause', 'phase:hunter', 'hunter_shot', 'role_revealed', 'death',
                    'death_cause', 'game_over']


def test_max_days_draw() -> None:
    g = new_game(max_days=2)
    play(g)
    assert g.result is not None and g.result.winner is None and g.result.reason == 'max_days'
    assert g.result.day == 2 and len(g.result.survivors) == 9
    assert g.events('judge')[-1].data['winner'] is None
    assert len(by_type(g, EventType.DAWN)) == 2


def test_no_events_or_requests_after_game_over() -> None:
    from werewolf.kernel.types import Action, IllegalAction

    g = new_game(max_days=1)
    play(g)
    assert g.over and g.pending() == []
    with pytest.raises(IllegalAction) as ei:
        g.submit(Action(request_id='r1', seat=1, option_id='pass'))
    assert ei.value.code == 'game_over'


# --------------------------------------------------------------------------- 夜里的顺序


def test_night_phases_fixed_even_when_roles_dead() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:3', ('wolf_propose', 2, None): 'kill:6'})
    play(g, d, until=waiting(RequestKind.SPEAK, day=3))
    phases = [e.data['phase'] for e in by_type(g, EventType.PHASE) if e.day == 3]
    assert phases[:5] == ['night_wolf', 'night_seer', 'night_witch', 'dawn', 'day_speech']
    # 第 3 夜预言家与女巫都死了：没有它们的请求或私有事件
    assert not [e for e in g.events('judge') if e.day == 3 and e.type in (EventType.SEER_RESULT,
                                                                           EventType.WITCH_NOTICE)]


def test_knifed_seer_still_gets_result_and_knifed_witch_can_self_save_first_night() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:3', ('seer_check', 1, 3): 'check:5'})
    play(g, d, until=waiting(RequestKind.WITCH_ACT))
    res = by_type(g, EventType.SEER_RESULT)
    assert len(res) == 1 and res[0].target == 5 and res[0].data == {'result': 'wolf', 'night': 1}

    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:6'})
    play(g, d, until=waiting(RequestKind.WITCH_ACT))
    req = g.pending()[0]
    assert req.options[0].id == 'save' and req.options[0].target == 6
    assert req.context == {'victim': 6, 'victim_known': True, 'antidote': True, 'poison': True}
    play(g, script({('witch_act', 1, 6): 'save'}), until=waiting(RequestKind.SPEAK))
    assert by_type(g, EventType.DAWN)[0].data == {'deaths': [], 'peaceful': True}


# --------------------------------------------------------------------------- 狼人


def test_wolf_plurality_and_events() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, 2): 'kill:4', ('wolf_propose', 1, 5): 'kill:7', ('wolf_propose', 1, 8): 'kill:7'})
    play(g, d, until=waiting(RequestKind.SEER_CHECK))
    props = by_type(g, EventType.WOLF_PROPOSE)
    assert [(e.actor, e.target, e.data['option_id']) for e in props] == [(2, 4, 'kill:4'), (5, 7, 'kill:7'),
                                                                         (8, 7, 'kill:7')]
    assert all(e.vis == 'wolves' for e in props)
    kill = by_type(g, EventType.WOLF_KILL)[0]
    assert kill.target == 7 and kill.data == {'by': 'plurality', 'tally': {'kill:4': 1, 'kill:7': 2}, 'stance': None}
    assert kill.vis == 'wolves'


def test_wolf_tie_takes_lowest_living_wolf() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, 2): 'kill:4', ('wolf_propose', 1, 5): 'kill:7', ('wolf_propose', 1, 8): 'kill:1'})
    play(g, d, until=waiting(RequestKind.SEER_CHECK))
    kill = by_type(g, EventType.WOLF_KILL)[0]
    assert kill.target == 4 and kill.data['by'] == 'tiebreak'
    # 2 号死后，最小的存活狼是 5
    g = new_game()
    d = script({('vote', 1, None): vote_for(2), ('wolf_propose', 2, 5): 'kill:7', ('wolf_propose', 2, 8): 'kill:1'})
    play(g, d, until=waiting(RequestKind.SEER_CHECK, day=2))
    assert [e.actor for e in by_type(g, EventType.WOLF_PROPOSE) if e.day == 2] == [5, 8]
    kill = [e for e in by_type(g, EventType.WOLF_KILL) if e.day == 2][0]
    assert kill.target == 7 and kill.data['by'] == 'tiebreak'


def test_wolves_may_knife_teammate_or_self_and_empty_kill_option() -> None:
    g = new_game()
    req = g.pending()[0]
    assert {o.id for o in req.options} == {f'kill:{s}' for s in range(1, 10)} | {'kill:none'}
    assert req.context == {'first_night': True}
    g = new_game(allow_empty_kill=False)
    assert 'kill:none' not in {o.id for o in g.pending()[0].options}
    d = script({('wolf_propose', 1, None): 'kill:2'})
    play(g, d, until=waiting(RequestKind.SPEAK))
    assert by_type(g, EventType.DAWN)[0].data['deaths'] == [2]


def test_human_wolf_leader_gets_proposals_and_decides() -> None:
    g = new_game(human_seat=5)
    assert sorted(r.seat for r in g.pending()) == [2, 8]
    assert all(r.kind == RequestKind.WOLF_PROPOSE for r in g.pending())
    from ._util import action

    r2, r8 = sorted(g.pending(), key=lambda r: r.seat)
    assert g.submit(action(r8, 'kill:3', note='刀预言家', stance='charge')) == []
    out = g.submit(action(r2, 'kill:4', stance='jump_seer'))
    assert [e.type for e in out] == [EventType.WOLF_PROPOSE, EventType.WOLF_PROPOSE]
    assert out[1].data == {'option_id': 'kill:3', 'note': '刀预言家', 'stance': 'charge'}
    lead = g.pending()
    assert len(lead) == 1 and lead[0].kind == RequestKind.WOLF_KILL and lead[0].seat == 5
    assert lead[0].context['proposals'] == [
        {'seat': 2, 'option_id': 'kill:4', 'target': 4, 'note': None, 'stance': 'jump_seer'},
        {'seat': 8, 'option_id': 'kill:3', 'target': 3, 'note': '刀预言家', 'stance': 'charge'},
    ]
    out = g.submit(action(lead[0], 'kill:7', stance='deep'))
    kill = out[0]
    assert kill.type == EventType.WOLF_KILL and kill.target == 7
    assert kill.data['by'] == 'leader' and kill.data['stance'] == 'deep'


def test_human_wolf_alone_gets_direct_kill_request() -> None:
    g = new_game(human_seat=5)
    d = script({('vote', 1, None): vote_for(2), ('vote', 2, None): vote_for(8)})
    play(g, d, until=waiting(RequestKind.WOLF_KILL, day=3))
    req = g.pending()[0]
    assert req.seat == 5 and req.context['proposals'] == []
    assert not [e for e in by_type(g, EventType.WOLF_PROPOSE) if e.day == 3]


def test_dead_human_wolf_falls_back_to_plurality() -> None:
    g = new_game(human_seat=5)
    play(g, script({('vote', 1, None): vote_for(5)}), until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    assert sorted(r.seat for r in g.pending()) == [2, 8]


# --------------------------------------------------------------------------- 预言家


def test_seer_checks_unchecked_living_others_and_stops_with_one_left() -> None:
    g = new_game()
    picks = iter([1, 2, 4, 5, 6, 7, 8])
    d = script({('seer_check', None, 3): lambda req: f'check:{next(picks)}'})
    play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=3))
    play(g, until=waiting(RequestKind.SEER_CHECK, day=3))
    req_n3 = g.pending()[0]
    assert {o.target for o in req_n3.options} == {4, 5, 6, 7, 8, 9}
    play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=8))
    res = by_type(g, EventType.SEER_RESULT)
    assert [(e.day, e.target, e.data['result']) for e in res] == [
        (1, 1, 'good'), (2, 2, 'wolf'), (3, 4, 'good'), (4, 5, 'wolf'), (5, 6, 'good'), (6, 7, 'good'),
        (7, 8, 'wolf')]
    # 只剩 9 号可验：只有一个选项，不发请求
    play(g, until=waiting(RequestKind.SPEAK, day=8))
    assert not [e for e in by_type(g, EventType.SEER_RESULT) if e.day == 8]
    assert [e.data['phase'] for e in by_type(g, EventType.PHASE) if e.day == 8][:3] == ['night_wolf', 'night_seer',
                                                                                        'night_witch']


# --------------------------------------------------------------------------- 女巫


def test_witch_notice_and_potions_lifecycle() -> None:
    g = new_game()
    d = script({('wolf_propose', None, None): 'kill:1', ('witch_act', 1, None): 'save',
                ('wolf_propose', 2, None): 'kill:4', ('witch_act', 2, None): 'poison:2',
                ('witch_act', 3, None): lambda r: pytest.fail('witch_act with only pass')})
    play(g, d, until=waiting(RequestKind.SPEAK, day=3))
    notes = by_type(g, EventType.WITCH_NOTICE)
    assert [(e.day, e.target, e.data) for e in notes] == [
        (1, 1, {'antidote': True, 'poison': True, 'victim_known': True}),
        (2, None, {'antidote': False, 'poison': True, 'victim_known': False}),
        (3, None, {'antidote': False, 'poison': False, 'victim_known': False}),
    ]
    assert all(e.vis == 'seats' and e.vis_seats == [6] for e in notes)
    assert [(e.day, e.target) for e in by_type(g, EventType.WITCH_SAVE)] == [(1, 1)]
    assert [(e.day, e.target) for e in by_type(g, EventType.WITCH_POISON)] == [(2, 2)]
    assert by_type(g, EventType.DAWN)[1].data['deaths'] == [2, 4]


def test_witch_act_context_after_antidote_used() -> None:
    g = new_game()
    d = script({('wolf_propose', None, None): 'kill:1', ('witch_act', 1, None): 'save'})
    play(g, d, until=waiting(RequestKind.WITCH_ACT, day=2))
    req = g.pending()[0]
    assert req.context == {'victim': None, 'victim_known': False, 'antidote': False, 'poison': True}
    assert 'save' not in {o.id for o in req.options}
    assert {o.target for o in req.options if o.id.startswith('poison:')} == {1, 2, 3, 4, 5, 7, 8, 9}
    assert g.view(6).witch is not None and g.view(6).witch.tonight_victim is None


@pytest.mark.parametrize(('rule', 'night', 'can'), [
    ('first_night', 1, True), ('first_night', 2, False), ('never', 1, False), ('always', 2, True)])
def test_witch_self_save_rule(rule: str, night: int, can: bool) -> None:
    g = new_game(witch_self_save=rule)
    d = script({('wolf_propose', night, None): 'kill:6', ('wolf_propose', None, None): 'kill:none'})
    play(g, d, until=waiting(RequestKind.WITCH_ACT, day=night))
    assert ('save' in {o.id for o in g.pending()[0].options}) == can


def test_knifed_and_poisoned_dies_once_cause_poison_and_hunter_blocked() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:9', ('witch_act', 1, None): 'poison:9'})
    play(g, d, until=waiting(RequestKind.SPEAK))
    assert by_type(g, EventType.DAWN)[0].data['deaths'] == [9]
    assert len(by_type(g, EventType.DEATH)) == 1
    assert by_type(g, EventType.DEATH_CAUSE)[0].data == {'cause': 'poison'}
    blocked = by_type(g, EventType.HUNTER_BLOCKED)
    assert len(blocked) == 1 and blocked[0].vis_seats == [9] and blocked[0].phase == Phase.HUNTER
    assert not by_type(g, EventType.HUNTER_SHOT)
    assert g.view(9).hunter_can_shoot is False


def test_antidote_only_cancels_the_knife() -> None:
    g = new_game(witch_self_save='always')
    d = script({('wolf_propose', 1, None): 'kill:4', ('witch_act', 1, None): 'save'})
    play(g, d, until=waiting(RequestKind.SPEAK))
    assert by_type(g, EventType.DAWN)[0].data == {'deaths': [], 'peaceful': True}
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:4', ('witch_act', 1, None): 'poison:7'})
    play(g, d, until=waiting(RequestKind.SPEAK))
    assert by_type(g, EventType.DAWN)[0].data['deaths'] == [4, 7]
    assert [e.data['cause'] for e in by_type(g, EventType.DEATH_CAUSE)] == ['wolf', 'poison']


def test_dead_witch_gets_no_notice() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:6'})
    play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    play(g, until=waiting(RequestKind.SPEAK, day=2))
    assert [e.day for e in by_type(g, EventType.WITCH_NOTICE)] == [1]


# --------------------------------------------------------------------------- 天亮与死亡批次


def test_dawn_lists_deaths_sorted_without_causes() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:7', ('witch_act', 1, None): 'poison:1'})
    out = play(g, d, until=waiting(RequestKind.SPEAK))
    i = kinds(out).index('dawn')
    assert kinds(out)[i - 1:i + 7] == ['phase:dawn', 'dawn', 'death', 'death_cause', 'death', 'death_cause',
                                       'phase:hunter', 'phase:last_words']
    dawn = out[i]
    assert dawn.data == {'deaths': [1, 7], 'peaceful': False}
    assert all('cause' not in e.data and 'role' not in e.data for e in out if e.vis == 'public')
    assert [e.data for e in out if e.type == EventType.DEATH] == [{'when': 'night', 'day': 1}] * 2


def test_peaceful_night_has_no_hunter_phase() -> None:
    g = new_game()
    out = play(g, until=waiting(RequestKind.SPEAK))
    assert 'phase:hunter' not in kinds(out)
    assert by_type(g, EventType.DAWN)[0].data == {'deaths': [], 'peaceful': True}


def test_hunter_knifed_shoots_and_is_revealed() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:9', ('hunter_shoot', 1, 9): 'shoot:5'})
    play(g, d, until=waiting(RequestKind.HUNTER_SHOOT))
    req = g.pending()[0]
    assert req.seat == 9 and req.context == {'after': 'night'} and req.phase == Phase.HUNTER
    assert {o.id for o in req.options} == {f'shoot:{s}' for s in range(1, 9)} | {'shoot:none'}
    out = play(g, d, until=waiting(RequestKind.SPEAK))
    assert kinds(out)[:5] == ['hunter_shot', 'role_revealed', 'death', 'death_cause', 'phase:last_words']
    shot = out[0]
    assert shot.vis == 'public' and shot.actor == 9 and shot.target == 5 and shot.data == {}
    assert out[1].target == 9 and out[1].data == {'role': 'hunter'}
    assert out[2].data == {'when': 'shot', 'day': 1} and out[3].data == {'cause': 'shot'}
    # 首夜死者有遗言，被带走的人跟随猎人，排在最后
    assert out[4].data == {'phase': 'last_words', 'day': 1, 'order': [9, 5], 'after': 'night'}
    assert all(g.view(s).known_roles.get(9) == Role.HUNTER for s in range(1, 10))
    assert g.view(1).players[8].revealed_role == Role.HUNTER


def test_hunter_declining_leaves_only_private_event() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:9'})
    out = play(g, d, until=waiting(RequestKind.SPEAK))
    shot = [e for e in out if e.type == EventType.HUNTER_SHOT]
    assert len(shot) == 1 and shot[0].vis == 'seats' and shot[0].vis_seats == [9] and shot[0].target is None
    assert not [e for e in out if e.type == EventType.ROLE_REVEALED]
    assert g.view(1).known_roles == {1: Role.VILLAGER}


def test_exiled_hunter_shoots_and_shot_victim_has_last_words() -> None:
    g = new_game()
    d = script({('vote', 1, None): vote_for(9), ('hunter_shoot', 1, 9): 'shoot:2'})
    play(g, d, until=waiting(RequestKind.HUNTER_SHOOT))
    assert g.pending()[0].context == {'after': 'vote'}
    out = play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    lw = [e for e in out if e.type == EventType.PHASE and e.data['phase'] == 'last_words'][0]
    assert lw.data == {'phase': 'last_words', 'day': 1, 'order': [9, 2], 'after': 'vote'}
    speeches = [e for e in out if e.type == EventType.SPEECH]
    assert [(e.actor, e.data['kind']) for e in speeches] == [(9, 'last_words'), (2, 'last_words')]


def test_last_words_only_for_first_nights() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:1', ('wolf_propose', 2, None): 'kill:9',
                ('hunter_shoot', 2, 9): 'shoot:4'})
    play(g, d, until=waiting(RequestKind.SPEAK, day=2))
    lw = [e for e in by_type(g, EventType.PHASE) if e.data['phase'] == 'last_words']
    assert [(e.day, e.data['order']) for e in lw] == [(1, [1])]
    # 第 2 夜的猎人没有遗言，被他带走的人也没有
    assert g.pending()[0].speech_kind == 'day'
    g = new_game(last_words_nights=2)
    play(g, d, until=waiting(RequestKind.SPEAK, day=2))
    assert g.pending()[0].speech_kind == 'last_words' and g.pending()[0].seat == 9


def test_last_words_request_context() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:7', ('witch_act', 1, None): 'poison:1'})
    play(g, d, until=waiting(RequestKind.SPEAK))
    req = g.pending()[0]
    assert (req.seat, req.speech_kind, req.phase) == (1, 'last_words', Phase.LAST_WORDS)
    assert req.context == {'index': 0, 'total': 2, 'after': 'night', 'rivals': []}
    assert not g.is_alive(1)


def test_win_is_judged_before_last_words() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:1', ('wolf_propose', 2, None): 'kill:4',
                ('vote', 1, None): vote_for(7)})
    play(g, d)
    assert kinds(g.events('judge'))[-6:] == ['phase:dawn', 'dawn', 'death', 'death_cause', 'phase:hunter',
                                             'game_over']


# --------------------------------------------------------------------------- 白天发言


def test_day_speech_order_starts_after_smallest_death() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:9', ('witch_act', 1, None): 'poison:4',
                ('hunter_shoot', 1, 9): 'shoot:5'})
    seen: list[tuple[int, dict]] = []

    def rec(req):  # noqa: ANN001, ANN202
        if req.speech_kind == 'day':
            seen.append((req.seat, req.context))
        return d(req)

    play(g, rec, until=waiting(RequestKind.VOTE))
    ph = [e for e in by_type(g, EventType.PHASE) if e.data['phase'] == 'day_speech'][0]
    # 死者 4、9（5 被猎人带走），从 4 之后第一个活人 6 起
    assert ph.data['order'] == [6, 7, 8, 1, 2, 3]
    assert [s for s, _ in seen] == ph.data['order']
    assert [c for _, c in seen] == [{'index': i, 'total': 6, 'after': None, 'rivals': []} for i in range(6)]


def test_speech_order_wraps_around() -> None:
    g = new_game()
    play(g, script({('wolf_propose', 1, None): 'kill:9'}), until=lambda gm: any(
        r.speech_kind == 'day' for r in gm.pending()))
    assert [e for e in by_type(g, EventType.PHASE) if e.data['phase'] == 'day_speech'][0].data['order'] == [
        1, 2, 3, 4, 5, 6, 7, 8]


def test_peaceful_night_order_is_a_ring_of_the_living_and_seeded() -> None:
    starts = set()
    for seed in range(12):
        g = new_game(seed=seed)
        play(g, until=waiting(RequestKind.SPEAK))
        order = by_type(g, EventType.PHASE)[-1].data['order']
        assert sorted(order) == list(range(1, 10))
        assert order == list(range(order[0], 10)) + list(range(1, order[0]))
        starts.add(order[0])
        g2 = new_game(seed=seed)
        play(g2, until=waiting(RequestKind.SPEAK))
        assert by_type(g2, EventType.PHASE)[-1].data['order'] == order
    assert len(starts) > 3


# --------------------------------------------------------------------------- 投票


def _to_vote(g):  # noqa: ANN001, ANN202
    play(g, until=waiting(RequestKind.VOTE))
    return g.pending()


def test_main_vote_options_and_phase() -> None:
    g = new_game()
    reqs = _to_vote(g)
    assert sorted(r.seat for r in reqs) == list(range(1, 10))
    ph = by_type(g, EventType.PHASE)[-1]
    assert ph.data == {'phase': 'day_vote', 'day': 1, 'voters': list(range(1, 10)), 'candidates': list(range(1, 10))}
    for r in reqs:
        assert [o.id for o in r.options] == [f'vote:{s}' for s in range(1, 10) if s != r.seat] + ['vote:abstain']
        assert r.context == {'round': 'main', 'candidates': [s for s in range(1, 10) if s != r.seat]}
    g = new_game(allow_abstain=False)
    assert all('vote:abstain' not in {o.id for o in r.options} for r in _to_vote(g))


def test_unique_top_is_exiled() -> None:
    g = new_game()
    d = script({('vote', 1, None): votes({1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 6: None})})
    out = play(g, d, until=waiting_speech('last_words'))
    vr = [e for e in out if e.type == EventType.VOTE_RESULT][0]
    assert vr.target == 4 and vr.data == {
        'round': 'main', 'tally': [{'target': 4, 'voters': [1, 2, 3]}, {'target': 1, 'voters': [4, 5]}],
        'abstain': [6, 7, 8, 9], 'outcome': 'exile', 'reason': 'top', 'tied': []}
    assert kinds(out)[-6:] == ['vote_result', 'exile', 'death', 'death_cause', 'phase:hunter', 'phase:last_words']
    assert [e.data for e in out if e.type == EventType.DEATH] == [{'when': 'vote', 'day': 1}]
    assert g.pending()[0].context == {'index': 0, 'total': 1, 'after': 'vote', 'rivals': []}


def test_all_abstain_nobody_out_and_night_follows() -> None:
    g = new_game()
    out = play(g, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    vr = [e for e in out if e.type == EventType.VOTE_RESULT][0]
    assert vr.target is None and vr.data['outcome'] == 'none' and vr.data['reason'] == 'all_abstain'
    assert vr.data['tally'] == [] and vr.data['abstain'] == list(range(1, 10))
    assert kinds(out)[-2:] == ['vote_result', 'phase:night_wolf']


def test_tie_goes_to_pk_then_pk_tie_nobody_out() -> None:
    g = new_game()
    d = script({('vote', 1, None): lambda r: votes({1: 4, 2: 4, 3: 7, 5: 7, 4: 1, 6: 1})(r) if r.context['round']
                == 'main' else votes({2: 7, 3: 4})(r)})
    play(g, d, until=waiting_speech('pk'))
    vr = by_type(g, EventType.VOTE_RESULT)[0]
    assert vr.data['outcome'] == 'pk' and vr.data['reason'] == 'tie' and vr.data['tied'] == [1, 4, 7]
    pk = by_type(g, EventType.PHASE)[-1]
    assert pk.data == {'phase': 'pk_speech', 'day': 1, 'order': [1, 4, 7], 'candidates': [1, 4, 7]}
    req = g.pending()[0]
    assert (req.seat, req.speech_kind) == (1, 'pk')
    assert req.context == {'index': 0, 'total': 3, 'after': None, 'rivals': [4, 7]}
    play(g, d, until=waiting(RequestKind.VOTE, day=1))
    pv = by_type(g, EventType.PHASE)[-1]
    assert pv.data == {'phase': 'pk_vote', 'day': 1, 'voters': [2, 3, 5, 6, 8, 9], 'candidates': [1, 4, 7]}
    for r in g.pending():
        assert [o.id for o in r.options] == ['vote:1', 'vote:4', 'vote:7', 'vote:abstain']
        assert r.context == {'round': 'pk', 'candidates': [1, 4, 7]}
    out = play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    vr2 = [e for e in out if e.type == EventType.VOTE_RESULT][0]
    assert vr2.data['round'] == 'pk' and vr2.data['outcome'] == 'none' and vr2.data['reason'] == 'pk_tie'
    assert vr2.data['tied'] == [4, 7] and vr2.target is None
    assert all(e.data['round'] == 'pk' for e in out if e.type == EventType.VOTE)


def test_pk_exile() -> None:
    g = new_game()
    d = script({('vote', 1, None): lambda r: votes({1: 4, 4: 1})(r) if r.context['round'] == 'main'
                else vote_for(4)(r)})
    out = play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    vr = [e for e in out if e.type == EventType.VOTE_RESULT]
    assert [v.data['outcome'] for v in vr] == ['pk', 'exile'] and vr[1].target == 4
    assert not g.is_alive(4)


def test_pk_when_everyone_is_tied_all_living_vote() -> None:
    g = new_game()
    d = script({
        ('wolf_propose', 1, None): 'kill:1', ('vote', 1, None): vote_for(2),
        ('wolf_propose', 2, None): 'kill:3', ('vote', 2, None): vote_for(5),
        ('wolf_propose', 3, None): 'kill:6',
        ('vote', 3, None): lambda r: votes({4: 7, 7: 4, 8: 9, 9: 8})(r) if r.context['round'] == 'main' else None,
    })
    play(g, d, until=waiting(RequestKind.VOTE, day=3))
    play(g, d, until=lambda gm: any(r.context.get('round') == 'pk' for r in gm.pending()))
    pv = by_type(g, EventType.PHASE)[-1]
    assert pv.data['voters'] == [4, 7, 8, 9] and pv.data['candidates'] == [4, 7, 8, 9]
    for r in g.pending():
        assert r.seat not in {o.target for o in r.options}


def test_dead_do_not_vote() -> None:
    g = new_game()
    play(g, script({('wolf_propose', 1, None): 'kill:9'}), until=waiting(RequestKind.VOTE))
    assert 9 not in {r.seat for r in g.pending()}
    assert all(9 not in {o.target for o in r.options} for r in g.pending())


def test_exile_then_night_follows_after_last_words() -> None:
    g = new_game()
    out = play(g, script({('vote', 1, None): vote_for(1)}), until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    assert kinds(out)[-3:] == ['phase:last_words', 'speech', 'phase:night_wolf']


def test_roles_not_revealed_on_death() -> None:
    g = new_game()
    d = script({('wolf_propose', 1, None): 'kill:3', ('vote', 1, None): vote_for(2)})
    play(g, d, until=waiting(RequestKind.WOLF_PROPOSE, day=2))
    assert not by_type(g, EventType.ROLE_REVEALED)
    v = g.view(1)
    assert v.known_roles == {1: Role.VILLAGER}
    assert all(p.revealed_role is None for p in v.players)
    assert {p.seat for p in v.players if not p.alive} == {2, 3}
