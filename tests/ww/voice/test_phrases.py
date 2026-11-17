"""嘀咕与词表：不含座位、身份词、投票意向。"""

from __future__ import annotations

import re

from werewolf.kernel.rng import Rng
from werewolf.voice.phrases import QUIPS, quip

BAD = re.compile(r'\d|号|投|出|狼|预言|女巫|猎人|平民|村民|白板|民牌|金水|查杀|银水|验|票|归|挂')
KEYS = ['doubt', 'agree', 'annoyed', 'surprised', 'amused', 'nervous', 'thinking']


def test_tables_clean():
    for key, lines in QUIPS.items():
        for s in lines:
            assert not BAD.search(s), (key, s)
            assert len(s) <= 12


def test_quip_clean_for_every_persona_and_key(voices):
    for v in voices:
        for key in KEYS:
            seen = set()
            for seed in range(40):
                s = quip(v, key, Rng(seed, 'q'))
                assert s is not None and not BAD.search(s), (v.id, key, s)
                assert len(s) <= 16
                seen.add(s)
            assert len(seen) >= 3


def test_quip_unknown_key_and_determinism(voices):
    assert quip(voices[0], 'no_such_key', Rng(1)) is None
    assert quip(voices[0], 'doubt', Rng(1, 'a')) == quip(voices[0], 'doubt', Rng(1, 'a'))
