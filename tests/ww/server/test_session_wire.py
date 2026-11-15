"""真人动作的错误语义、断线重连与重放、上帝视角、速度。"""

from __future__ import annotations

import asyncio

from werewolf.kernel.types import EventType, Role
from werewolf.protocol import (
    Accepted,
    ActionMsg,
    ErrorMsg,
    EventMsg,
    GodView,
    Parsed,
    Preview,
    RequestMsg,
    ServerMsg,
    SessionState,
    SetOptions,
)
from werewolf.server.session import REPLAY_TYPES

from .helpers import ScriptedClient, assert_clean, make_session, play


async def test_illegal_actions_keep_or_close_request(cfg):
    done: set[str] = set()

    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if not isinstance(msg, RequestMsg) or msg.kind in done:
            return False
        if msg.kind == 'speak':
            done.add('speak')
            c.answered.add(msg.id)

            async def bad_speech() -> None:
                await c.to_server(ActionMsg(request_id=msg.id, text='   '))
                await c.to_server(ActionMsg(request_id=msg.id, option_id='vote:1'))
                await c.to_server(ActionMsg(request_id=msg.id, text='我是好人'))
                await c.to_server(ActionMsg(request_id=msg.id, text='我是好人'))
                await c.to_server(Preview(request_id=msg.id, text='再看看'))
            c.data['speak'] = msg.id
            c.spawn(bad_speech())
            return True
        if 'option' not in done:
            done.add('option')
            c.answered.add(msg.id)

            async def bad_option() -> None:
                await c.to_server(ActionMsg(request_id=msg.id, option_id='nope:9'))
                await c.to_server(ActionMsg(request_id='bogus', option_id=msg.options[0].id))
                await c.to_server(Preview(request_id=msg.id, text='我是预言家'))
                await c.to_server(ActionMsg(request_id=msg.id, option_id=msg.options[-1].id))
                await c.to_server(ActionMsg(request_id=msg.id, option_id=msg.options[0].id))
            c.data['option'] = msg.id
            c.spawn(bad_option())
            return True
        return False

    c = ScriptedClient(1, hook=hook)
    s, c = await play(cfg, seed=8, role=Role.SEER, client=c)
    assert_clean(s, c.msgs)

    def trail(rid: str, *others: str) -> list[tuple]:
        out = []
        for m in c.msgs:
            if isinstance(m, ErrorMsg) and m.request_id in (rid, *others):
                out.append(('error', m.code, m.detail, m.request_id == rid))
            elif isinstance(m, Accepted) and m.request_id == rid:
                out.append(('accepted',))
        return out

    assert trail(c.data['option'], 'bogus') == [
        ('error', 'illegal_action', 'not_an_option', True),
        ('error', 'illegal_action', 'unknown_request', False),
        ('error', 'illegal_action', 'not_an_option', True),  # 不是发言却来预览
        ('accepted',),
        ('error', 'illegal_action', 'unknown_request', True),  # 答过了
    ]
    assert trail(c.data['speak']) == [
        ('error', 'illegal_action', 'bad_speech', True),
        ('error', 'illegal_action', 'bad_speech', True),  # 发言请求却给了选项
        ('accepted',),
        ('error', 'illegal_action', 'unknown_request', True),
        ('error', 'illegal_action', 'unknown_request', True),
    ]


async def _resume_game(cfg, *, at: str, seed: int = 6) -> None:
    holder: dict[str, object] = {}

    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if 'cut' in holder:
            return False
        n_events = sum(isinstance(m, EventMsg) for m in c.msgs)
        hit = (at == 'request' and isinstance(msg, RequestMsg) and n_events > 30) or \
              (at == 'event' and isinstance(msg, EventMsg) and n_events == 25)
        if not hit:
            return False
        holder['cut'] = msg
        assert c.session is not None
        c.session.detach()
        return True

    c1 = ScriptedClient(seed, hook=hook)
    s = make_session(cfg, seed=seed, role=Role.VILLAGER, client=c1)
    run = asyncio.ensure_future(s.run())
    for _ in range(200):
        await asyncio.sleep(0)
        if 'cut' in holder:
            break
    assert 'cut' in holder
    n_out = len(s.outbox)
    await asyncio.sleep(0.05)
    assert len(s.outbox) == n_out, '没有连接时会话暂停'
    c2 = ScriptedClient(seed + 1)
    c2.session = s
    await s.attach(c2.send)
    await asyncio.wait_for(run, 60)
    await c1.drain()
    await c2.drain()

    i_state = next(i for i, m in enumerate(c2.msgs) if isinstance(m, SessionState))
    replayed, live = c2.msgs[:i_state], c2.msgs[i_state + 1:]
    state = c2.msgs[i_state]
    assert all(getattr(m, 'replay', False) for m in replayed)
    original = [m for m in c1.msgs if m.t in REPLAY_TYPES and not (isinstance(m, Parsed) and m.preview)]
    assert [m.model_copy(update={'replay': False}) for m in replayed] == original
    cut = holder['cut']
    if at == 'request':
        assert isinstance(cut, RequestMsg)
        assert state.open_request == cut
        assert isinstance(live[0], (Accepted, Parsed)) or any(isinstance(m, Accepted) and m.request_id == cut.id
                                                              for m in live)
    else:
        assert state.open_request is None
    assert not any(m.t in ('thinking', 'reaction', 'cost', 'error', 'accepted', 'request') for m in replayed)
    # 现场接着发，整条（重放去掉）与一直连着一样干净
    assert_clean(s, list(s.outbox))
    assert_clean(s, [*c1.msgs, *live])


async def test_resume_at_open_request(cfg):
    await _resume_game(cfg, at='request')


async def test_resume_mid_stream_pauses(cfg):
    await _resume_game(cfg, at='event')


async def test_god_view_rejected_until_settled(cfg):
    """真人遗言或开枪之前要上帝视角被拒；结算完 can_god_view 之后被接受，先补 god 再 state。"""
    for seed in range(40):
        for role in (Role.HUNTER, Role.VILLAGER):
            got = await _god_game(cfg, seed, role)
            if got:
                return
    raise AssertionError('40 个种子里真人都没死在终局之前')


async def _god_game(cfg, seed: int, role: Role) -> bool:
    probe: dict[str, object] = {}

    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if isinstance(msg, RequestMsg) and (msg.kind == 'hunter_shoot' or msg.speech_kind == 'last_words') \
                and 'early' not in probe:
            probe['early'] = msg.id
            c.answered.add(msg.id)

            async def early() -> None:
                await c.to_server(SetOptions(god_view=True))
                await c.answer(msg)
            c.spawn(early())
            return True
        if isinstance(msg, SessionState) and msg.can_god_view and 'asked' not in probe:
            probe['asked'] = len(c.msgs)
            c.spawn(c.to_server(SetOptions(god_view=True)))
        return False

    c = ScriptedClient(seed, hook=hook)
    s, c = await play(cfg, seed=seed, role=role, client=c)
    if 'early' not in probe or 'asked' not in probe:
        return False
    assert_clean(s, c.msgs)
    msgs = c.msgs
    rid = probe['early']
    i_req = next(i for i, m in enumerate(msgs) if isinstance(m, RequestMsg) and m.id == rid)
    i_err = next(i for i, m in enumerate(msgs) if isinstance(m, ErrorMsg) and m.code == 'not_allowed')
    i_acc = next(i for i, m in enumerate(msgs) if isinstance(m, Accepted) and m.request_id == rid)
    assert i_req < i_err < i_acc
    gods = [i for i, m in enumerate(msgs) if isinstance(m, GodView)]
    assert len(gods) == 1
    g = msgs[gods[0]]
    assert isinstance(g, GodView)
    assert {r.seat: r.role for r in g.roles} == {i: s.game.role_of(i) for i in range(1, 10)}
    hidden = [e for e in g.events if not s.game.visible(e, s.human_seat)]
    assert hidden == g.events and hidden, '补发的是此前不可见的事件'
    nxt = msgs[gods[0] + 1]
    assert isinstance(nxt, SessionState) and nxt.god_view
    # 之后的 event 改发全部事件：不可见的也发（还有夜晚的话）
    after = [m.ev for m in msgs[gods[0]:] if isinstance(m, EventMsg)]
    if any(e.type == EventType.DAWN for e in after):
        assert any(e.vis != 'public' for e in after)
    seqs = [m.seq for m in msgs if isinstance(m, EventMsg)]
    assert seqs == list(range(1, len(seqs) + 1))
    return True


async def test_god_view_not_allowed_while_alive(cfg):
    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if isinstance(msg, RequestMsg) and 'tried' not in c.data:
            c.data['tried'] = True
            c.answered.add(msg.id)

            async def go() -> None:
                await c.to_server(SetOptions(god_view=True))
                await c.answer(msg)
            c.spawn(go())
            return True
        return False

    c = ScriptedClient(2, hook=hook)
    s, c = await play(cfg, seed=2, role=Role.SEER, client=c)
    errs = [m for m in c.msgs if isinstance(m, ErrorMsg)]
    assert errs and errs[0].code == 'not_allowed'


async def test_autopilot_never_offers_god_view(cfg):
    c = ScriptedClient(0)
    s, c = await play(cfg, seed=1, autopilot=True, client=c)
    assert not any(isinstance(m, SessionState) and m.can_god_view for m in c.msgs)
    assert not any(isinstance(m, RequestMsg) for m in c.msgs)
    assert_clean(s, c.msgs)


async def test_speed_change_reports_state(cfg):
    def hook(c: ScriptedClient, msg: ServerMsg) -> bool:
        if isinstance(msg, RequestMsg) and 'sped' not in c.data:
            c.data['sped'] = True
            c.answered.add(msg.id)

            async def go() -> None:
                await c.to_server(SetOptions(speed='fast'))
                await c.to_server(SetOptions(speed='instant'))
                await c.answer(msg)
            c.spawn(go())
            return True
        return False

    c = ScriptedClient(3, hook=hook)
    s, c = await play(cfg, seed=3, role=Role.WITCH, client=c, speed='normal', time_scale=0.0)
    states = [m.speed for m in c.msgs if isinstance(m, SessionState)]
    assert states[0] == 'normal' and 'fast' in states and states[-1] == 'instant'
    i = next(i for i, m in enumerate(c.msgs) if isinstance(m, SessionState) and m.speed == 'instant')
    assert all(m.hold_ms == 0 and not m.wait_ack for m in c.msgs[i:] if isinstance(m, EventMsg))
    assert_clean(s, c.msgs)


async def test_acks_gate_ai_speeches(cfg):
    """normal 档：别人的发言带 wait_ack，下一条主线消息在 ack 之后才发；thinking 在 ack 之后。"""
    c = ScriptedClient(4)
    s, c = await play(cfg, seed=4, role=Role.VILLAGER, client=c, speed='normal', time_scale=0.0)
    waits = [m for m in c.msgs if isinstance(m, EventMsg) and m.wait_ack]
    assert waits
    assert all(m.ev.type == EventType.SPEECH and m.ev.actor != s.human_seat for m in waits)
    assert all(m.hold_ms > 0 for m in c.msgs if isinstance(m, EventMsg) and m.ev.type == EventType.PHASE)
    assert_clean(s, c.msgs)
