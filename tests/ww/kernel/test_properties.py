"""性质测试：确定性、存档续接、视图纯度、公开序列与身份无关、不变量、事件形状、JSON 往返。"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.ww.kernel._util import ROLES, action, default_choice, new_game, script, votes
from werewolf.kernel.autoplay import play_random, random_action
from werewolf.kernel.engine import Game
from werewolf.kernel.invariants import check
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    Event,
    EventType,
    GameConfig,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    strip_text,
)
from werewolf.kernel.views import build_public_view, build_view
from werewolf.testing import Scenario, act, duel_scenario

CONFIGS: list[GameConfig] = [
    GameConfig(),
    GameConfig(human_seat=1),
    GameConfig(human_seat=5, allow_abstain=False, allow_empty_kill=False),
    GameConfig(human_seat=9, witch_self_save='always', last_words_nights=2, max_days=4),
    GameConfig(witch_self_save='never', max_days=2),
]


def _config(i: int) -> GameConfig:
    return CONFIGS[i % len(CONFIGS)]


def _steps(game: Game, seed: int) -> Iterator[Request]:
    """随机作答，每答一次 yield 一次刚答的请求。"""
    rng = Rng(seed, 'test')
    while not game.over:
        for req in game.pending():
            game.submit(random_action(game, req, rng))
            yield req


def _log(game: Game) -> list[dict]:
    return [e.model_dump(mode='json') for e in game.events('judge')]


# --------------------------------------------------------------------------- 确定性与存档


@pytest.mark.parametrize('seed', [0, 7, 123456789])
def test_same_seed_same_actions_same_log(seed: int) -> None:
    a = play_random(_config(seed), seed)
    b = play_random(_config(seed), seed)
    assert a.over and b.over
    assert _log(a) == _log(b)
    assert a.result == b.result


def test_different_seeds_differ() -> None:
    assert _log(play_random(GameConfig(), 1)) != _log(play_random(GameConfig(), 2))


@pytest.mark.parametrize('seed', range(12))
def test_dump_load_mid_game_continues_identically(seed: int) -> None:
    cfg = _config(seed)
    ref = Game.new(cfg, seed)
    for _ in _steps(ref, seed):
        pass
    # 在若干个点（含批答到一半时）存档，读回后用同样的随机流继续
    for cut in (0, 1, 5, 17, 40, 77):
        g = Game.new(cfg, seed)
        rng = Rng(seed, 'test')
        n = 0
        while not g.over:
            for req in g.pending():
                if n == cut:
                    data = g.dump()
                    blob = json.dumps(data)  # 必须是 JSON 原生
                    g = Game.load(json.loads(blob))
                    assert g.dump() == data
                    assert g.pending() == [r for r in g.pending()]
                g.submit(random_action(g, req, rng))
                n += 1
        assert _log(g) == _log(ref), f'cut={cut}'
        assert g.result == ref.result


def test_dump_preserves_open_batch_and_rng() -> None:
    g = new_game()
    reqs = g.pending()
    assert len(reqs) == 3  # 三只狼的提议
    g.submit(action(reqs[0], 'kill:4'))
    g2 = Game.load(json.loads(json.dumps(g.dump())))
    assert g2.pending() == g.pending()
    for x in (g, g2):
        for r in x.pending():
            x.submit(action(r, 'kill:4'))
    assert _log(g) == _log(g2)
    assert g.pending() == g2.pending()


# --------------------------------------------------------------------------- 视图


def _visible_stripped(game: Game, seat: Seat) -> list[Event]:
    return [strip_text(e) for e in game.events(seat)]


@pytest.mark.parametrize('seed', range(25))
def test_view_is_pure_function_of_visible_events(seed: int) -> None:
    cfg = _config(seed)
    game = Game.new(cfg, seed)
    wolves = {s for s in range(1, 10) if game.role_of(s) == Role.WEREWOLF}
    seen = 0
    for _ in _steps(game, seed):
        new = game.events('judge', since=seen)
        seen += len(new)
        fan = game.fanout(new)
        for seat in range(1, 10):
            vis = _visible_stripped(game, seat)
            v = game.view(seat)
            assert v == build_view(cfg, seat, vis)
            assert all(not (e.type == EventType.SPEECH and e.data['speech']['text']) for e in v.events)
            if seat not in wolves:
                assert not any(e.vis == 'wolves' for e in v.events)
            assert all(e.vis != 'judge' for e in v.events)
            assert fan[seat] == [strip_text(e) for e in new if game.visible(e, seat)]
        pv = game.public_view()
        assert pv == build_public_view(cfg, game.events('judge'))
        assert all(e.vis == 'public' for e in pv.events)


def test_events_with_text_vs_views_without() -> None:
    game = play_random(GameConfig(), 3)
    speeches = [e for e in game.events(1) if e.type == EventType.SPEECH]
    assert speeches and all(e.data['speech']['text'] for e in speeches)
    assert all(not e.data['speech']['text'] for e in game.view(1).events if e.type == EventType.SPEECH)
    assert all(not e.data['speech']['text'] for evs in game.fanout(speeches).values() for e in evs)


# --------------------------------------------------------------------------- 公开序列与身份无关


def _public_seq(game: Game) -> list[tuple[str, str | None]]:
    return [(e.type.value, e.data.get('phase')) for e in game.events('judge')
            if e.vis == 'public' and e.type != EventType.GAME_OVER]


def _public_full(game: Game) -> list[dict]:
    """公开事件的全部内容（编号除外：私有事件的条数本来就随身份变）。"""
    return [e.model_dump(mode='json', exclude={'id'}) for e in game.events('judge')
            if e.vis == 'public' and e.type != EventType.GAME_OVER]


def _role_swap_games() -> tuple[Game, Game]:
    """同一串公开动作：夜 1 刀 9、白天 1 放逐 7、夜 2 刀 1。A 里 9 是猎人，B 里 7 是猎人；猎人都不开枪。"""
    a_roles = dict(ROLES)  # 7 平民、9 猎人
    b_roles = dict(ROLES)
    b_roles[7], b_roles[9] = Role.HUNTER, Role.VILLAGER
    plan = script({
        ('wolf_propose', 1, None): 'kill:9',
        ('wolf_propose', 2, None): 'kill:1',
        ('vote', 1, None): votes({s: 7 for s in range(1, 10) if s != 7} | {7: 1}),
        ('hunter_shoot', None, None): 'shoot:none',
    })
    out = []
    for roles in (a_roles, b_roles):
        g = new_game(roles, seed=11, max_days=2)
        while not g.over:
            for req in g.pending():
                c = plan(req)
                g.submit(action(req, c if c is not None else default_choice(req)))
        out.append(g)
    return out[0], out[1]


def test_public_sequence_same_for_villager_and_declining_hunter() -> None:
    a, b = _role_swap_games()
    assert _public_seq(a) == _public_seq(b)
    assert _public_full(a) == _public_full(b)
    # 两局都真的走了「有猎人请求」与「没有请求」两条路
    shoot_a = [e for e in a.events('judge') if e.type == EventType.HUNTER_SHOT]
    shoot_b = [e for e in b.events('judge') if e.type == EventType.HUNTER_SHOT]
    assert [e.actor for e in shoot_a] == [9] and [e.actor for e in shoot_b] == [7]
    assert all(e.vis == 'seats' for e in shoot_a + shoot_b)
    hunter_phases = [e.data['after'] for e in a.events('judge')
                     if e.type == EventType.PHASE and e.data['phase'] == 'hunter']
    assert hunter_phases[:2] == ['night', 'vote']


def test_public_sequence_same_when_night_roles_dead_or_alive() -> None:
    """预言家、女巫是否存活不改变公开序列：三个夜间阶段照发。"""
    seqs = []
    for roles in (dict(ROLES), {**ROLES, 3: Role.VILLAGER, 1: Role.SEER}):
        g = new_game(roles, seed=5, max_days=3)
        plan = script({('wolf_propose', 1, None): 'kill:3', ('wolf_propose', 2, None): 'kill:6',
                       ('wolf_propose', 3, None): 'kill:none'})
        while not g.over:
            for req in g.pending():
                c = plan(req)
                g.submit(action(req, c if c is not None else default_choice(req)))
        seqs.append(_public_full(g))
    assert seqs[0] == seqs[1]


# --------------------------------------------------------------------------- 不变量


def test_invariants_every_step_200_random_games() -> None:
    for seed in range(200):
        game = Game.new(_config(seed), seed)
        assert check(game) == []
        for req in _steps(game, seed):
            errs = check(game)
            assert errs == [], (seed, req.id, errs)
        assert game.over


def test_invariants_at_end_1000_random_games() -> None:
    reasons = set()
    for seed in range(1000):
        game = play_random(_config(seed), seed)
        assert game.over, seed
        assert check(game, views=(seed % 50 == 0)) == [], seed
        assert game.result is not None
        reasons.add(game.result.reason)
    assert {'wolves_dead', 'villagers_dead', 'gods_dead'} <= reasons


def test_play_random_respects_max_steps() -> None:
    g = play_random(GameConfig(), 1, max_steps=3)
    assert not g.over and g.pending()


def test_random_action_is_always_legal() -> None:
    game = Game.new(GameConfig(human_seat=2), 9)
    rng = Rng(9, 'x')
    while not game.over:
        for req in game.pending():
            a = random_action(game, req, rng)
            if req.kind != RequestKind.SPEAK:
                assert a.option_id in {o.id for o in req.options}
            game.submit(a)


# --------------------------------------------------------------------------- 事件形状与 JSON 往返

SHAPES: dict[str, set[str]] = {
    'game_start': {'seats', 'names', 'role_counts'},
    'role_dealt': {'role'},
    'wolf_team': {'wolves'},
    'wolf_propose': {'option_id', 'note', 'stance'},
    'wolf_kill': {'by', 'tally', 'stance'},
    'seer_result': {'result', 'night'},
    'witch_notice': {'antidote', 'poison', 'victim_known'},
    'witch_save': set(),
    'witch_poison': set(),
    'dawn': {'deaths', 'peaceful'},
    'death': {'when', 'day'},
    'death_cause': {'cause'},
    'hunter_blocked': set(),
    'hunter_shot': set(),
    'role_revealed': {'role'},
    'speech': {'kind', 'speech'},
    'vote': {'round', 'option_id'},
    'vote_result': {'round', 'tally', 'abstain', 'outcome', 'reason', 'tied'},
    'exile': set(),
    'game_over': {'winner', 'reason', 'roles'},
}
PHASE_EXTRA: dict[str, set[str]] = {
    'day_speech': {'order'},
    'pk_speech': {'order', 'candidates'},
    'last_words': {'order', 'after'},
    'hunter': {'after'},
    'day_vote': {'voters', 'candidates'},
    'pk_vote': {'voters', 'candidates'},
}
VIS: dict[str, str] = {
    'game_start': 'public', 'role_dealt': 'seats', 'wolf_team': 'wolves', 'phase': 'public',
    'wolf_propose': 'wolves', 'wolf_kill': 'wolves', 'seer_result': 'seats', 'witch_notice': 'seats',
    'witch_save': 'seats', 'witch_poison': 'seats', 'dawn': 'public', 'death': 'public', 'death_cause': 'judge',
    'hunter_blocked': 'seats', 'role_revealed': 'public', 'speech': 'public', 'vote': 'public',
    'vote_result': 'public', 'exile': 'public', 'game_over': 'public',
}


def _expected_keys(ev: Event) -> set[str]:
    if ev.type == EventType.PHASE:
        return {'phase', 'day'} | PHASE_EXTRA.get(ev.data['phase'], set())
    return SHAPES[ev.type.value]


def _check_shape(ev: Event) -> None:
    assert set(ev.data) == _expected_keys(ev), (ev.type, sorted(ev.data))
    if ev.type == EventType.HUNTER_SHOT:
        assert ev.vis == ('public' if ev.target is not None else 'seats')
    else:
        assert ev.vis == VIS[ev.type.value], ev.type
    if ev.vis == 'seats':
        assert len(ev.vis_seats) == 1
    assert Event.model_validate_json(ev.model_dump_json()) == ev
    assert json.loads(json.dumps(ev.model_dump(mode='json'))) == ev.model_dump(mode='json')


def test_event_shapes_and_json_round_trip_in_random_games() -> None:
    seen: set[str] = set()
    for seed in range(300):
        game = play_random(_config(seed), seed)
        for ev in game.events('judge'):
            _check_shape(ev)
            seen.add(ev.type.value if ev.type != EventType.PHASE else f'phase:{ev.data["phase"]}')
            assert ev.day == 0 if ev.phase == Phase.SETUP else ev.day >= 1
    want = {t.value for t in EventType} - {'phase'}
    want |= {f'phase:{p.value}' for p in Phase if p not in (Phase.SETUP, Phase.GAME_OVER)}
    assert want <= seen, want - seen


def test_engine_shapes_match_testing_scenario() -> None:
    """`werewolf.testing.Scenario` 拼出的事件与引擎的 data 键一致。"""
    sc = duel_scenario()
    sc2 = Scenario()
    sc2.night(1, kill=9, check=(3, 2, 'wolf'))
    sc2.dawn([9])
    sc2.shot(9, 5)
    sc2.speech(9, '遗言', [act('accuse', 2)], kind='last_words')
    sc2.speech(1, '发言', [act('pass')])
    sc2.vote({1: 2, 2: 1, 3: 2, 4: 1}, round='main')
    sc2.speech(1, 'PK', kind='pk')
    sc2.vote({3: 1, 4: 2}, round='pk')
    for ev in sc.events + sc2.events:
        keys = set(ev.data)
        want = _expected_keys(ev)
        assert keys == want, (ev.type, sorted(keys))


def test_request_context_shapes() -> None:
    ctx_keys: dict[str, set[str]] = {
        'wolf_propose': {'first_night'},
        'wolf_kill': {'proposals'},
        'seer_check': set(),
        'witch_act': {'victim', 'victim_known', 'antidote', 'poison'},
        'hunter_shoot': {'after'},
        'vote': {'round', 'candidates'},
        'speak': {'index', 'total', 'after', 'rivals'},
    }
    seen = set()
    for seed in range(60):
        game = Game.new(_config(seed), seed)
        rng = Rng(seed, 'ctx')
        while not game.over:
            for req in game.pending():
                seen.add(req.kind.value)
                assert set(req.context) == ctx_keys[req.kind.value], (req.kind, req.context)
                assert json.loads(json.dumps(req.context)) == req.context
                if req.kind == RequestKind.WOLF_KILL:
                    for p in req.context['proposals']:
                        assert set(p) == {'seat', 'option_id', 'target', 'note', 'stance'}
                game.submit(random_action(game, req, rng))
    assert seen == set(ctx_keys)
