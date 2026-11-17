import re
from typing import get_args

import pytest

from werewolf.director.narration import DECK, HIGH, KEYS, line, lines, placeholders, render
from werewolf.kernel.rng import Rng
from werewolf.protocol import NarrationKey

ALL_KEYS = list(get_args(NarrationKey))
KNOWN_PLACEHOLDERS = {'day', 'seats', 'seat'}

# 露底的说法：台词不得断言谁是什么身份、夜里谁做了什么、药还在不在
LEAKS = ['是狼人', '是好人', '是预言家', '是女巫', '是猎人', '是平民', '查验结果', '验出', '救了', '毒了', '刀了',
         '被刀', '被毒', '解药已', '毒药已', '还有解药', '还有毒药', '没有药', '已经死', '已出局的', '他是', '她是',
         '身份是', '死因']


def test_deck_covers_every_key() -> None:
    assert set(DECK) == set(ALL_KEYS)
    assert set(KEYS) == set(ALL_KEYS)


@pytest.mark.parametrize('key', ALL_KEYS)
def test_at_least_four_distinct_calm_lines(key: str) -> None:
    calm = lines(key, 0.0)
    assert len(set(calm)) >= 4
    assert len(calm) == len(set(calm))
    assert set(calm) <= set(lines(key, 1.0))


def test_some_keys_get_more_atmosphere_at_high_tension() -> None:
    richer = [k for k in ALL_KEYS if len(lines(k, HIGH)) > len(lines(k, 0.0))]
    assert len(richer) >= len(ALL_KEYS) // 2
    rng = Rng(3)
    calm = set(lines('night_fall', 0.0))
    hot = {line('night_fall', 1.0, rng, day=2) for _ in range(300)}
    assert any(render(t, day=2) in hot for t in lines('night_fall', 1.0) if t not in calm)
    cool = {line('night_fall', 0.0, rng, day=2) for _ in range(300)}
    assert cool <= {render(t, day=2) for t in calm}


@pytest.mark.parametrize('key', ALL_KEYS)
def test_no_role_leaks_and_no_literal_seats(key: str) -> None:
    for text, _ in DECK[key]:
        assert not re.search(r'\d', text), text
        assert '号' not in text, text  # 座位号只能经占位符进来
        assert placeholders(text) <= KNOWN_PLACEHOLDERS, text
        for bad in LEAKS:
            assert bad not in text, (text, bad)
        assert len(text) <= 40


def test_night_lines_do_not_depend_on_anything_but_rng() -> None:
    # 夜里与技能环节的台词每次都照念：同样的 rng 给出同样的句子，没有别的输入
    for key in ('wolves_wake', 'seer_wake', 'witch_wake', 'seer_sleep', 'witch_sleep', 'skill'):
        a = [line(key, 0.2, Rng(1, key)) for _ in range(5)]
        b = [line(key, 0.2, Rng(1, key)) for _ in range(5)]
        assert a == b
        for t, _ in DECK[key]:
            assert not placeholders(t)


@pytest.mark.parametrize('key', ALL_KEYS)
def test_avoid_is_honoured(key: str) -> None:
    rng = Rng(7, key)
    fmt = {'day': 2, 'seat': 5, 'seats': [3, 7]}
    pool = [render(t, **fmt) for t in lines(key, 0.0)]
    for i in range(len(pool)):
        avoid = pool[:i] + pool[i + 1:]
        for _ in range(10):
            got = line(key, 0.0, rng, avoid=avoid, **fmt)
            assert got not in avoid
    # 所有句子都避开了也不报错
    everything = [render(t, **fmt) for t, _ in DECK[key]]
    assert line(key, 1.0, rng, avoid=everything, **fmt)


def test_avoid_accepts_raw_templates() -> None:
    rng = Rng(1)
    calm = lines('dawn_deaths', 0.0)
    for _ in range(30):
        got = line('dawn_deaths', 0.0, rng, avoid=calm[1:], seats='3号')
        assert got == render(calm[0], seats='3号')


def test_avoid_falls_back_to_high_tension_variant_before_repeating() -> None:
    rng = Rng(2)
    calm = [render(t, day=1) for t in lines('night_fall', 0.0)]
    for _ in range(20):
        assert line('night_fall', 0.0, rng, avoid=calm, day=1) not in calm


def test_formatting() -> None:
    rng = Rng(4)
    for _ in range(30):
        got = line('dawn_deaths', 0.3, rng, seats='3号、7号')
        assert '3号、7号' in got and '{' not in got
        got = line('dawn_deaths', 0.3, rng, seats=[3, 7])
        assert '3号、7号' in got
        got = line('exile', 0.9, rng, seat=6)
        assert '6号' in got
    assert render('第{day}天，{seat}先说', day=3, seat=2) == '第3天，2号先说'


def test_missing_placeholders_are_safe() -> None:
    assert render('第{day}天，请发言。') == '今天，请发言。'
    assert render('第{day}夜，天黑请闭眼。') == '今夜，天黑请闭眼。'
    assert render('昨夜，{seats}倒下了。') == '昨夜，有玩家倒下了。'
    assert render('{seat}出局。') == '这位玩家出局。'
    assert render('{nope}好的{', extra=1) == '好的{'
    rng = Rng(5)
    for key in ALL_KEYS:
        for t in (0.0, 0.6, 1.0):
            for _ in range(10):
                got = line(key, t, rng)
                assert got and '{' not in got and '}' not in got


def test_line_prefers_variants_with_given_placeholders() -> None:
    rng = Rng(6)
    for _ in range(50):
        got = line('speech_start', 0.0, rng, seat=4)
        assert '4号' in got


def test_values_with_braces_are_literal() -> None:
    assert render('{seats}出局', seats='{day}') == '{day}出局'


def test_unknown_key_returns_empty() -> None:
    assert line('nope', 0.5, Rng(1)) == ''  # type: ignore[arg-type]
