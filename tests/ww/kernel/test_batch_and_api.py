"""批次语义、非法动作、请求编号、可见性与接口的小契约。"""

from __future__ import annotations

import pytest

from werewolf.kernel.types import (
    Action,
    ActKind,
    EventType,
    IllegalAction,
    RequestKind,
    Speech,
    SpeechAct,
    strip_text,
)

from ._util import action, kinds, new_game, play, waiting


def _vote_batch():  # noqa: ANN202
    g = new_game()
    play(g, until=waiting(RequestKind.VOTE))
    return g


def test_vote_batch_writes_nothing_until_complete_and_order_is_by_seat() -> None:
    logs = []
    for reverse in (False, True):
        g = _vote_batch()
        n0 = len(g.events('judge'))
        reqs = sorted(g.pending(), key=lambda r: r.seat, reverse=reverse)
        for i, r in enumerate(reqs[:-1]):
            assert g.submit(action(r, f'vote:{1 if r.seat != 1 else 2}')) == []
            assert len(g.events('judge')) == n0
            assert len(g.pending()) == len(reqs) - i - 1
            assert r.id not in {p.id for p in g.pending()}
        out = g.submit(action(reqs[-1], f'vote:{1 if reqs[-1].seat != 1 else 2}'))
        assert kinds(out)[:10] == ['vote'] * 9 + ['vote_result']
        assert [e.actor for e in out[:9]] == list(range(1, 10))
        assert out == g.events('judge')[n0:]
        logs.append([e.model_dump() for e in g.events('judge')])
    assert logs[0] == logs[1]


def test_wolf_propose_batch() -> None:
    g = new_game()
    reqs = g.pending()
    assert sorted(r.seat for r in reqs) == [2, 5, 8] and all(r.kind == RequestKind.WOLF_PROPOSE for r in reqs)
    n0 = len(g.events('judge'))
    r8 = next(r for r in reqs if r.seat == 8)
    r2 = next(r for r in reqs if r.seat == 2)
    r5 = next(r for r in reqs if r.seat == 5)
    assert g.submit(action(r8, 'kill:1', note='刀1')) == []
    assert g.submit(action(r2, 'kill:1')) == []
    assert len(g.events('judge')) == n0
    out = g.submit(action(r5, 'kill:4'))
    assert kinds(out)[:5] == ['wolf_propose', 'wolf_propose', 'wolf_propose', 'wolf_kill', 'phase:night_seer']
    assert [e.actor for e in out[:3]] == [2, 5, 8]
    assert out[2].data['note'] == '刀1'


def test_single_request_batches() -> None:
    g = new_game()
    play(g, until=waiting(RequestKind.SEER_CHECK))
    assert len(g.pending()) == 1
    play(g, until=waiting(RequestKind.SPEAK))
    assert len(g.pending()) == 1


def test_request_ids_unique_and_sequential() -> None:
    g = new_game()
    ids: list[str] = []

    def rec(req):  # noqa: ANN001, ANN202
        ids.append(req.id)
        return None

    play(g, rec)
    assert ids == [f'r{i}' for i in range(1, len(ids) + 1)]


def _state(g):  # noqa: ANN001, ANN202
    return (len(g.events('judge')), [r.id for r in g.pending()], g.dump())


def test_illegal_actions_leave_state_unchanged() -> None:
    g = _vote_batch()
    before = _state(g)
    r = g.pending()[0]
    other = g.pending()[1]
    cases = [
        (Action(request_id='r999', seat=r.seat, option_id='vote:abstain'), 'unknown_request'),
        (Action(request_id=r.id, seat=other.seat, option_id='vote:abstain'), 'wrong_seat'),
        (Action(request_id=r.id, seat=r.seat, option_id=f'vote:{r.seat}'), 'not_an_option'),
        (Action(request_id=r.id, seat=r.seat), 'not_an_option'),
    ]
    for act, code in cases:
        with pytest.raises(IllegalAction) as ei:
            g.submit(act)
        assert ei.value.code == code
        assert _state(g) == before
    g.submit(action(r, 'vote:abstain'))
    with pytest.raises(IllegalAction) as ei:
        g.submit(action(r, 'vote:abstain'))
    assert ei.value.code == 'unknown_request'


def _speak_game():  # noqa: ANN202
    g = new_game()
    play(g, until=waiting(RequestKind.SPEAK))
    return g


@pytest.mark.parametrize('speech', [
    Speech(text=''),
    Speech(text='   \n '),
    Speech.model_construct(text='长' * 601, acts=[], tone='calm', emote=None, source='ai', flags=[]),
    Speech.model_construct(text='好', acts=[SpeechAct(kind=ActKind.PASS)] * 13, tone='calm', emote=None,
                           source='ai', flags=[]),
    Speech(text='好', acts=[SpeechAct(kind=ActKind.ACCUSE, target=10)]),
    Speech(text='好', acts=[SpeechAct(kind=ActKind.ACCUSE, target=0)]),
    Speech(text='好', emote='dance'),
    Speech(text='好', flags=['jev_failed']),
])
def test_bad_speech_rejected(speech: Speech) -> None:
    g = _speak_game()
    before = _state(g)
    r = g.pending()[0]
    with pytest.raises(IllegalAction) as ei:
        g.submit(Action(request_id=r.id, seat=r.seat, speech=speech))
    assert ei.value.code == 'bad_speech'
    assert _state(g) == before


def test_missing_speech() -> None:
    g = _speak_game()
    r = g.pending()[0]
    with pytest.raises(IllegalAction) as ei:
        g.submit(Action(request_id=r.id, seat=r.seat, option_id='pass'))
    assert ei.value.code == 'missing_speech'


def test_speech_stored_as_json_dict_and_stripped_for_views() -> None:
    g = _speak_game()
    r = g.pending()[0]
    sp = Speech(text='我是预言家，' + '啊' * 590, acts=[SpeechAct(kind=ActKind.CLAIM_ROLE, role='seer'),
                                                 SpeechAct(kind=ActKind.ACCUSE, target=9, weight=3)] * 6,
                emote='frown', flags=['stage_direction'], source='human')
    out = g.submit(Action(request_id=r.id, seat=r.seat, speech=sp))
    ev = out[0]
    assert ev.type == EventType.SPEECH and ev.actor == r.seat
    assert ev.data == {'kind': 'day', 'speech': sp.model_dump(mode='json')}
    assert g.events(r.seat)[-len(out)].data['speech']['text'] == sp.text
    for s in range(1, 10):
        v_ev = next(e for e in g.view(s).events if e.id == ev.id)
        assert v_ev.data['speech']['text'] == '' and v_ev.data['speech']['acts'] == ev.data['speech']['acts']
    assert next(e for e in g.public_view().events if e.id == ev.id).data['speech']['text'] == ''
    fan = g.fanout(out)
    assert set(fan) == set(range(1, 10)) and fan[1][0] == strip_text(ev)


def test_visibility_and_events_since() -> None:
    g = new_game()
    play(g, until=waiting(RequestKind.SPEAK))
    judge = g.events('judge')
    assert [e.id for e in judge] == list(range(1, len(judge) + 1))
    assert g.events('judge', since=5)[0].id == 6
    wolf_ids = {e.id for e in judge if e.vis == 'wolves'}
    assert wolf_ids
    for s in (2, 5, 8):
        assert wolf_ids <= {e.id for e in g.events(s)}
    for s in (1, 3, 4, 6, 7, 9):
        assert not wolf_ids & {e.id for e in g.events(s)}
        assert not wolf_ids & {e.id for e in g.view(s).events}
    for s in range(1, 10):
        assert not [e for e in g.events(s) if e.vis == 'judge']
        assert all(e.vis != 'seats' or s in e.vis_seats for e in g.events(s))
    causes = [e for e in judge if e.type == EventType.DEATH_CAUSE]
    assert all(not g.visible(e, s) for e in causes for s in range(1, 10))
    fan = g.fanout(judge)
    for s in range(1, 10):
        assert [e.id for e in fan[s]] == [e.id for e in g.events(s)]


def test_judge_accessors() -> None:
    g = new_game()
    assert g.role_of(3).value == 'seer' and g.is_alive(3)
    assert not g.over and g.result is None
    pv = g.public_view()
    assert pv.day == 1 and [p.name for p in pv.players] == [f'{s}号' for s in range(1, 10)]
    g2 = new_game(names={1: '阿青'}, human_seat=1)
    assert g2.public_view().players[0].name == '阿青' and g2.public_view().players[0].is_human
    assert g2.events('judge')[0].data['names'][0] == {'seat': 1, 'name': '阿青'}
