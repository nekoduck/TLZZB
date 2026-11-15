"""人设文件：合法、有区别、语料干净。"""

from __future__ import annotations

import re

from werewolf.kernel.rng import Rng
from werewolf.mind.types import Traits
from werewolf.voice.canon import ROLE_WORDS, claims_in
from werewolf.voice.personas import draft, load_personas, voice_of

ROLE_RE = re.compile('|'.join(w for ws in ROLE_WORDS.values() for w in ws) + '|金水|查杀|银水')
HYPO_Q = re.compile(r'吗|是不是|难道|会不会|有没有|到底是|谁是|是谁|怎么可能|凭什么|[?？]|如果|要是|假如|假设|万一|就算|哪怕|即使|倘若|除非')


def test_twelve_valid_personas():
    ps = load_personas()
    assert len(ps) == 12
    assert len({p.id for p in ps}) == 12
    assert len({p.name for p in ps}) == 12
    assert len({p.avatar.model_dump_json() for p in ps}) == 12
    assert len({(p.avatar.hair_style, p.avatar.accessory) for p in ps}) >= 8
    for p in ps:
        assert 2 <= len(p.name) <= 4
        assert len(p.tags) <= 3
        assert 150 <= len(p.bio) <= 300, (p.id, len(p.bio))
        assert p.bio.startswith('我') or '我' in p.bio[:20]
        assert 3 <= len(p.choices) <= 5
        assert 3 <= len(p.style.samples) <= 6
        assert p.style.openers and p.style.closers and p.style.fillers and p.style.catchphrases


def test_trait_spread():
    ps = load_personas()
    for name in Traits.model_fields:
        vals = [getattr(p.traits, name) for p in ps]
        assert max(vals) - min(vals) >= 0.5, name


def test_style_corpus_is_clean():
    """口吻词表会被原样拼进台词：不得含座位、身份词、会被读成声明或假设疑问的说法。"""
    for p in load_personas():
        st = p.style
        for s in [*st.catchphrases, *st.openers, *st.closers, *st.fillers]:
            assert not re.search(r'\d|号', s), (p.id, s)
            assert not ROLE_RE.search(s), (p.id, s)
            assert not HYPO_Q.search(s), (p.id, s)
            assert not re.search(r'["“”「」『』()（）\[\]【】*AI]', s), (p.id, s)
            assert claims_in(s) == [] and claims_in(f'我{s}') == []
        for s in st.samples:
            assert not re.search(r'\d', s), (p.id, s)


def test_draft_is_deterministic_and_distinct():
    a = draft(Rng(1, 'd'))
    b = draft(Rng(1, 'd'))
    assert [p.id for p in a] == [p.id for p in b]
    assert len(a) == 8 and len({p.id for p in a}) == 8
    assert [p.id for p in draft(Rng(2, 'd'))] != [p.id for p in a]
    assert len(draft(Rng(3), n=3)) == 3


def test_voice_of_has_no_traits_or_avatar():
    p = load_personas()[0]
    v = voice_of(p)
    dumped = v.model_dump()
    assert 'traits' not in dumped and 'avatar' not in dumped
    assert v.id == p.id and v.bio == p.bio


def test_load_returns_copies():
    a = load_personas()
    a[0].name = '改了'
    assert load_personas()[0].name != '改了'
