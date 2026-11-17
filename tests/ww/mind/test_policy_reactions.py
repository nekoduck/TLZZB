"""神态反应与身份无关（第 10 节「知识边界」）：同一座位、人设、随机流、公开事件流下，不论心智是平民、
狼人（被指认者的队友）还是预言家（验过被指认者），反应序列相同；公开动作相同的方案，语气与神态也相同。
另：反应只针对公开事件，每条至多一个，嘀咕取固定词表。"""

from __future__ import annotations

from werewolf.kernel.claims import speech_acts
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import EMOTES, Event, EventType, GameConfig, Role
from werewolf.kernel.views import build_view
from werewolf.mind.mind import Mind
from werewolf.mind.types import Reaction
from werewolf.testing import Scenario, duel_scenario
from werewolf.voice.phrases import QUIPS

from .policy_helpers import persona

SEAT = 9
ACCUSED = 2  # 第 1 天被 3 号指认、第 2 天被 7 号指认


def _stream(role: Role) -> list[Event]:
    """duel_scenario 的公开事件，开头插两条私有事件（条数固定，所以公开事件的 id 在三种身份下相同）。"""
    sc = duel_scenario()
    pub = [e for e in sc.events if e.vis == 'public']
    head = [pub[0]]
    private: list[Event] = [Event(id=0, day=0, phase=pub[0].phase, type=EventType.ROLE_DEALT, actor=SEAT,
                                  data={'role': role.value}, vis='seats', vis_seats=[SEAT])]
    if role == Role.WEREWOLF:
        private.append(Event(id=0, day=0, phase=pub[0].phase, type=EventType.WOLF_TEAM,
                             data={'wolves': [ACCUSED, 5, SEAT]}, vis='wolves'))
    elif role == Role.SEER:
        private.append(Event(id=0, day=1, phase=pub[0].phase, type=EventType.SEER_RESULT, actor=SEAT,
                             target=ACCUSED, data={'result': 'wolf', 'night': 1}, vis='seats', vis_seats=[SEAT]))
    else:
        private.append(private[0].model_copy())
    out: list[Event] = []
    for i, e in enumerate(head + private + pub[1:], start=1):
        out.append(e.model_copy(update={'id': i}))
    return out


def _run(role: Role) -> tuple[Mind, list[Reaction], list[Event]]:
    cfg = GameConfig()
    m = Mind(SEAT, persona('react', talkative=0.8, vengeful=0.7), role, cfg, Rng(5, 'same'))
    evs = _stream(role)
    reacts: list[Reaction] = []
    for i, e in enumerate(evs):
        view = build_view(cfg, SEAT, evs[: max(i + 1, 2)])
        reacts += m.observe(e, view)
    return m, reacts, evs


def test_reactions_are_role_independent() -> None:
    v, rv, evs = _run(Role.VILLAGER)
    w, rw, _ = _run(Role.WEREWOLF)
    s, rs, _ = _run(Role.SEER)
    assert rv == rw == rs
    assert rv, 'some reactions expected'
    assert w.beliefs.p(ACCUSED) != v.beliefs.p(ACCUSED)  # 私有判断确实不同
    ids = {e.id: e for e in evs}
    for r in rv:
        assert r.seat == SEAT and r.emote in EMOTES
        assert r.quip_key is None or r.quip_key in QUIPS
        assert ids[r.event_id].vis == 'public'
    assert len({r.event_id for r in rv}) == len(rv)  # 每条事件至多一个


def test_public_style_is_role_independent_when_acts_match() -> None:
    v, _, evs = _run(Role.VILLAGER)
    w, _, _ = _run(Role.WEREWOLF)
    req = Scenario().request_speak(SEAT).model_copy(update={'day': 2})
    cfg = v.config
    view_v = build_view(cfg, SEAT, evs)
    view_w = build_view(cfg, SEAT, _stream(Role.WEREWOLF))
    pv = v.speech_variants(req, view_v)
    pw = w.speech_variants(req, view_w)
    pairs = [(a, b) for a in pv for b in pw if a.public_acts() == b.public_acts()]
    assert pairs
    for a, b in pairs:
        assert (a.tone, a.emote, a.length) == (b.tone, b.emote, b.length)
    assert v.public_mood() == w.public_mood()


def test_private_events_give_no_reactions() -> None:
    m, _, evs = _run(Role.SEER)
    fresh = Mind(SEAT, persona('react'), Role.SEER, m.config, Rng(5, 'same'))
    priv = [e for e in evs if e.vis != 'public']
    for e in priv:
        assert fresh.observe(e, build_view(m.config, SEAT, evs[: e.id])) == []


def test_bystanders_mostly_keep_a_straight_face() -> None:
    """话不是冲着自己来的发言，旁观者大多没表情：全桌平均每段发言的旁观反应不到 0.7 个（旁观概率 0.25 时约 0.9 个）。"""
    sc = duel_scenario()
    cfg = GameConfig()
    evs = sc.events
    speeches = [e for e in evs if e.type == EventType.SPEECH]
    total = 0
    n = 0
    for k in range(12):
        for seat in range(1, 10):
            m = Mind(seat, persona(f'b{k}', talkative=0.8), sc.roles[seat], cfg, Rng(100 + k, f'by{seat}'))
            for i, e in enumerate(evs):
                if not sc.visible(e, seat):
                    continue
                rs = m.observe(e, build_view(cfg, seat, evs[: max(i + 1, 12)]))
                if e.type == EventType.SPEECH and e.actor != seat:
                    hit = any(a.target == seat for a in speech_acts(e))
                    if not hit:
                        total += len(rs)
                        n += 1
    per_speech = total / (n / 8)  # 每段发言约 8 个旁观者
    assert len(speeches) >= 10 and per_speech < 0.7, per_speech
