"""整局：真人以五种身份各打一局（脚本化客户端），托管整局，夜间对外序列与身份无关。"""

from __future__ import annotations

from collections import Counter

import pytest

from werewolf.director.pacing import HOLD_RANGES
from werewolf.kernel import invariants
from werewolf.kernel.types import EventType, Role
from werewolf.protocol import (
    Accepted,
    EventMsg,
    GameStarted,
    Narration,
    Parsed,
    PrivateInfo,
    ReactionMsg,
    RequestMsg,
    Review,
    SessionState,
)
from werewolf.server.wirecheck import wire_check

from .helpers import assert_clean, play

ROLES = [Role.WEREWOLF, Role.VILLAGER, Role.SEER, Role.WITCH, Role.HUNTER]


@pytest.mark.parametrize('role', ROLES, ids=[r.value for r in ROLES])
@pytest.mark.parametrize('seed', [3, 11])
async def test_human_plays_whole_game(cfg, role, seed):
    s, c = await play(cfg, seed=seed, role=role)
    msgs = c.msgs
    assert s.result is not None and s.game.over
    assert invariants.check(s.game) == []
    assert s.human_role == role
    assert isinstance(msgs[0], GameStarted) and msgs[0].your_role == role and msgs[0].you == s.human_seat
    assert isinstance(msgs[1], SessionState)
    assert_clean(s, msgs)
    # 每个请求都被受理
    reqs = [m.id for m in msgs if isinstance(m, RequestMsg)]
    acc = [m.request_id for m in msgs if isinstance(m, Accepted)]
    assert reqs == acc
    # 复盘：全部身份、全部事件（内核编号）、种子
    review = next(m for m in msgs if isinstance(m, Review))
    assert {x.seat: x.role for x in review.seats} == {i: s.game.role_of(i) for i in range(1, 10)}
    assert [e.id for e in review.events] == list(range(1, len(review.events) + 1))
    assert review.stats.seed == seed
    assert review.stats.speech_sources.get('template', 0) > 0
    ai_thoughts = [t for x in review.seats if not x.is_human for t in x.thoughts]
    assert ai_thoughts and all(t.text for t in ai_thoughts)
    # 私有提示：至少有身份牌
    assert any(isinstance(m, PrivateInfo) and m.key == 'role' for m in msgs)
    # 最后一条是 over 的 state
    assert isinstance(msgs[-1], SessionState) and msgs[-1].over


async def test_human_speech_preview_keep_and_parsed(cfg):
    s, c = await play(cfg, seed=5, role=Role.VILLAGER)
    msgs = c.msgs
    previews = [m for m in msgs if isinstance(m, Parsed) and m.preview]
    finals = [m for m in msgs if isinstance(m, Parsed) and not m.preview]
    human_speeches = [m for m in msgs if isinstance(m, EventMsg) and m.ev.type == EventType.SPEECH
                      and m.ev.actor == s.human_seat]
    assert human_speeches, '真人至少发言一次'
    assert len(finals) == len(human_speeches)
    for ev, fin in zip(human_speeches, finals, strict=True):
        assert fin.seq == ev.seq
        sp = ev.ev.data['speech']
        assert sp['source'] == 'human'
        assert not ev.wait_ack
        assert set(sp['flags']) <= {'injection', 'stage_direction', 'truncated'}
        assert [a.model_dump(mode='json') for a in fin.acts] == sp['acts']
        # parsed{preview:false} 紧跟在发言事件之后（中间至多夹神态）
        i = msgs.index(ev)
        j = msgs.index(fin)
        assert all(isinstance(m, ReactionMsg) for m in msgs[i + 1:j])
    # keep 去掉了预览里的最后一条
    for p in previews:
        if len(p.acts) >= 2:
            acc = next(m for m in msgs if isinstance(m, Accepted) and m.request_id == p.request_id)
            fin = next(m for m in finals if msgs.index(m) > msgs.index(acc))
            assert fin.acts == p.acts[:-1]
            break
    # AI 的发言等 ack（instant 档不等）
    assert all(not m.wait_ack for m in msgs if isinstance(m, EventMsg))


async def test_autopilot_headless_games(cfg):
    for seed in range(4):
        from .helpers import make_session

        s = make_session(cfg, seed=seed, autopilot=True, headless=True)
        res = await s.run()
        assert res.winner is not None or res.reason == 'max_days'
        assert wire_check(s.outbox, s.game, s.human_seat) == []
        assert not any(isinstance(m, RequestMsg) for m in s.outbox)
        assert isinstance(s.review, Review)
        human = next(x for x in s.review.seats if x.is_human)
        assert human.thoughts, '托管的真人座位也记下了决定'


def _night_shapes(msgs):
    """每一夜（night_fall 到 dawn 事件）里对外消息的形状。"""
    shapes, cur = [], None
    for m in msgs:
        if isinstance(m, Narration) and m.key == 'night_fall':
            cur = []
        if cur is None:
            continue
        if isinstance(m, Narration):
            cur.append(('narration', m.key))
        elif isinstance(m, EventMsg):
            cur.append(('event', m.ev.type.value, m.ev.data.get('phase')))
        elif m.t in ('thinking', 'request'):
            cur.append((m.t,))
        if isinstance(m, EventMsg) and m.ev.type == EventType.DAWN:
            shapes.append(tuple(cur))
            cur = None
    return shapes


async def test_night_shape_and_holds_do_not_depend_on_roles(cfg):
    """真人平民：每一夜对外的消息序列都一样，环节时长都从同一分布里取，不论哪些身份还活着。"""
    shapes: Counter = Counter()
    lo, hi = HOLD_RANGES['night_step']
    slo, shi = HOLD_RANGES['skill']
    dead_role_nights = 0
    for seed in range(8):
        s, c = await play(cfg, seed=seed, role=Role.VILLAGER, speed='normal', time_scale=0.0)
        assert_clean(s, c.msgs)
        for sh in _night_shapes(c.msgs):
            shapes[sh] += 1
        for m in c.msgs:
            if isinstance(m, Narration) and m.key in ('wolves_wake', 'seer_wake', 'witch_wake'):
                assert lo <= m.hold_ms <= hi, m
            if isinstance(m, Narration) and m.key == 'skill':
                assert slo <= m.hold_ms <= shi, m
        roles = {r: [i for i in range(1, 10) if s.game.role_of(i) == r] for r in (Role.SEER, Role.WITCH)}
        evs = s.game.events('judge')
        for (who,) in roles.values():
            died = next((e.day for e in evs if e.type == EventType.DEATH and e.target == who), None)
            if died is not None and died < s.game.result.day:
                dead_role_nights += 1
    assert len(shapes) == 1, list(shapes)
    assert dead_role_nights > 0, '对照里要有神职先死的局'


async def test_wolf_leader_stance_on_first_night(cfg):
    s, c = await play(cfg, seed=2, role=Role.WEREWOLF)
    kills = [e for e in s.game.events('judge') if e.type == EventType.WOLF_KILL and e.day == 1]
    reqs = [m for m in c.msgs if isinstance(m, RequestMsg) and m.kind == 'wolf_kill']
    assert reqs and reqs[0].context['first_night'] is True
    assert 'proposals' in reqs[0].context
    assert kills[0].data['by'] == 'leader'
    assert kills[0].data['stance'] in ('jump_seer', 'charge', 'deep')


async def test_run_dir_files(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-canary-secret-123')
    run_dir = tmp_path / 'runs' / 'g1'
    s, c = await play(cfg, seed=4, role=Role.SEER, run_dir=run_dir)
    for name in ('events', 'decisions', 'briefs', 'wire'):
        text = (run_dir / f'{name}.jsonl').read_text(encoding='utf-8')
        assert text.strip()
        assert 'sk-canary-secret-123' not in text
    from werewolf.server.store import read_jsonl

    rows = read_jsonl(run_dir / 'wire.jsonl')
    assert {r['dir'] for r in rows} == {'in', 'out'}
    assert all(isinstance(r['ts_ms'], int) for r in rows)
    assert len(read_jsonl(run_dir / 'events.jsonl')) == len(s.game.events('judge'))
    briefs = read_jsonl(run_dir / 'briefs.jsonl')
    assert all({'request_id', 'brief', 'text', 'tier', 'rejected'} <= set(b) for b in briefs)


@pytest.mark.slow
async def test_many_scripted_games(cfg):
    for seed in range(60):
        role = ROLES[seed % 5]
        s, c = await play(cfg, seed=1000 + seed, role=role)
        assert_clean(s, c.msgs)
        assert invariants.check(s.game) == []
