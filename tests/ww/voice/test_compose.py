"""模板作曲：声明读回来对得上、篇幅、变化。"""

from __future__ import annotations

import random

import pytest

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import ActKind, Tone
from werewolf.mind.types import PublicAct, PublicMood, PublicPlan, Situation
from werewolf.voice import canon
from werewolf.voice.brief import build_brief
from werewolf.voice.compose import candidates, compose

from .conftest import random_brief

CLAIM = canon.CLAIM_KINDS


def _allowed(brief) -> set[int]:
    s = {brief.seat, *brief.addressed}
    s.update(p.act.target for p in brief.points if p.act.target is not None)
    return s


def test_claims_roundtrip_property(pv, voices):
    rnd = random.Random(20240601)
    for i in range(1500):
        brief = random_brief(rnd, pv, voices)
        rng = Rng(i, 'prop')
        text = compose(brief, rng)
        planned = [p.act for p in brief.points if p.act.kind in CLAIM]
        extra, missing = canon.claim_diff(text, planned, brief.day)
        ctx = f'{brief.persona.id} {brief.tone} {brief.length} {brief.max_chars}\n{text}\n{[p.text for p in brief.points]}'
        assert extra == [], ctx
        assert missing == [], ctx
        assert len(text) <= brief.max_chars, ctx
        assert set(canon.seats_in(text)) <= _allowed(brief), ctx
        assert text and '\n' not in text
        for bad in '"“”「」『』()（）[]【】*':
            assert bad not in text, ctx
        for bad in ('AI', '模型', '程序', '机器人'):
            assert bad not in text


def test_candidates_distinct_and_valid(pv, voices):
    rnd = random.Random(7)
    for i in range(60):
        brief = random_brief(rnd, pv, voices)
        cs = candidates(brief, Rng(i, 'cand'), n=6)
        assert len(set(cs)) == len(cs) >= (4 if brief.max_chars >= 120 else 1)
        planned = [p.act for p in brief.points if p.act.kind in CLAIM]
        for t in cs:
            assert canon.claim_diff(t, planned, brief.day) == ([], [])
            assert len(t) <= brief.max_chars


def test_compose_deterministic(pv, voices):
    rnd = random.Random(3)
    brief = random_brief(rnd, pv, voices)
    assert compose(brief, Rng(5, 'x')) == compose(brief, Rng(5, 'x'))


def _brief(pv, voice, acts, *, tone=Tone.CALM, length='medium', max_chars=180, kind='day', seat=3, day=2):
    plan = PublicPlan(seat=seat, day=day, kind=kind, acts=acts, tone=tone, length=length)
    return build_brief(plan, pv, voice, PublicMood(), Situation(index=1, total=4), [], max_chars)


def test_claim_phrasing_exact_reading(pv, voices):
    acts = [
        PublicAct(kind=ActKind.CLAIM_ROLE, role='seer', weight=3),
        PublicAct(kind=ActKind.CLAIM_CHECK, target=7, result='wolf', night=2),
        PublicAct(kind=ActKind.VOTE_INTENT, target=7),
    ]
    for v in voices:
        for seed in range(10):
            text = compose(_brief(pv, v, acts), Rng(seed))
            found = {(a.kind.value, a.target, a.role.value if a.role else a.result) for a in canon.claims_in(text, 2)}
            assert found == {('claim_role', None, 'seer'), ('claim_check', 7, 'wolf'), ('vote_intent', 7, None)}, text


def test_pass_is_natural_and_claim_free(pv, voices):
    for v in voices:
        text = compose(_brief(pv, v, [PublicAct(kind=ActKind.PASS)]), Rng(1))
        assert text
        assert canon.claims_in(text, 2) == []
        assert canon.seats_in(text) == []


@pytest.mark.parametrize('kind', ['day', 'pk', 'last_words'])
@pytest.mark.parametrize('max_chars', [40, 60, 120])
def test_max_chars_respected(pv, voices, kind, max_chars):
    acts = [
        PublicAct(kind=ActKind.CLAIM_ROLE, role='seer'),
        PublicAct(kind=ActKind.CLAIM_CHECK, target=7, result='good', night=1),
        PublicAct(kind=ActKind.CLAIM_CHECK, target=8, result='wolf', night=2),
        PublicAct(kind=ActKind.ACCUSE, target=5, reason='quiet'),
        PublicAct(kind=ActKind.VOTE_INTENT, target=8),
    ]
    for v in voices:
        for seed in range(8):
            text = compose(_brief(pv, v, acts, length='long', max_chars=max_chars, kind=kind), Rng(seed))
            assert len(text) <= max_chars
            planned = [a for a in acts if a.kind in CLAIM]
            if max_chars >= 60:
                assert canon.claim_diff(text, planned, 2) == ([], [])


def test_same_persona_five_briefs_no_repeated_sentence(pv, voices):
    rnd = random.Random(11)
    for v in voices:
        rng = Rng(99, 'var')
        seen: list[str] = []
        for _ in range(5):
            brief = random_brief(rnd, pv, [v])
            text = compose(brief, rng)
            sents = [s for s in text.replace('！', '。').replace('？', '。').split('。') if s]
            for s in sents:
                assert s not in seen, (v.id, s)
            seen.extend(sents)


def test_two_personas_same_brief_differ(pv, voices):
    acts = [PublicAct(kind=ActKind.ACCUSE, target=8, reason='quiet'), PublicAct(kind=ActKind.VOTE_INTENT, target=8)]
    outs = {}
    for v in voices:
        outs[v.id] = [compose(_brief(pv, v, acts), Rng(s)) for s in range(6)]
    ids = list(outs)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            same = sum(x == y for x, y in zip(outs[a], outs[b], strict=True))
            assert same <= 1, (a, b)
    # 每个人设自己也有区别于别人的口吻：口头禅、开场、收尾至少有一种在多数输出里露面
    for v in voices:
        flair = [*v.style.catchphrases, *v.style.openers, *v.style.closers, *v.style.fillers]
        hit = sum(any(f in t for f in flair) for t in outs[v.id])
        assert hit >= 3, v.id


def test_hedge_and_reason_woven(pv, voices):
    acts = [PublicAct(kind=ActKind.ACCUSE, target=8, reason='gut', weight=2)]
    seen_reason = False
    for v in voices:
        for seed in range(10):
            text = compose(_brief(pv, v, acts, length='long'), Rng(seed))
            seen_reason |= '直觉' in text
            assert '8号' in text
    assert seen_reason
