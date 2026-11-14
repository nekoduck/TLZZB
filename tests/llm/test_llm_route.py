from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from tianlong.config import JevThresholds, Settings
from tianlong.llm.jev import wrap_answers
from tianlong.llm.route import choice_margin, mode, sample_choice, should_escalate

TH = JevThresholds()


def choice(probs: dict[str, float], conf: float) -> dict:
    top = max(probs, key=lambda k: probs[k])
    return {'type': 'choice', 'choice': top, 'confidence': conf, 'probabilities': probs}


def test_confident_choice_stays() -> None:
    a = choice({'a01': 0.9, 'a02': 0.07, 'none': 0.03}, 0.85)
    assert not should_escalate(a, TH)


def test_low_confidence_escalates() -> None:
    a = choice({'a01': 0.5, 'a02': 0.1, 'none': 0.4}, 0.25)
    assert should_escalate(a, TH)


def test_close_top_two_escalates_even_with_ok_confidence() -> None:
    # confidence 过线，但头两名只差 0.1
    a = choice({'a01': 0.55, 'a02': 0.45}, 0.60)
    assert choice_margin(a) == pytest.approx(0.10)
    assert should_escalate(a, TH)


def test_important_always_escalates() -> None:
    a = choice({'a01': 1.0, 'a02': 0.0}, 1.0)
    assert should_escalate(a, TH, important=True)
    assert should_escalate(a, TH, True)


def test_needs_thought_in_answer_group() -> None:
    group = wrap_answers({
        'action': choice({'a01': 0.95, 'none': 0.05}, 0.9),
        'speak': {'type': 'noul', 'noul': 0.9},
        'needs_thought': {'type': 'noul', 'noul': 0.65},
    })
    assert should_escalate(group, TH)
    group['needs_thought']['noul'] = 0.55
    assert not should_escalate(group, TH)


def test_keys_limit_which_choices_count() -> None:
    group = {
        'kind': choice({'say': 0.95, 'act': 0.05}, 0.9),
        'target2': choice({'x': 0.5, 'none': 0.5}, 0.0),
    }
    assert should_escalate(group, TH)
    assert not should_escalate(group, TH, keys=['kind'])


def test_thresholds_as_mapping() -> None:
    a = choice({'a01': 0.7, 'a02': 0.3}, 0.4)
    assert should_escalate(a, {'choice_confidence': 0.55, 'choice_margin': 0.15, 'needs_thought': 0.6})
    assert not should_escalate(a, {'choice_confidence': 0.3, 'choice_margin': 0.15, 'needs_thought': 0.6})


def rolls(seq: list[int]) -> Callable[[], int]:
    it = iter(seq)
    return lambda: next(it)


def test_sample_choice_deterministic_and_proportional() -> None:
    probs = {'a01': 0.6, 'a02': 0.3, 'none': 0.1}
    # 0.1 < floor 0.15 → 'none' 出局；在 0.9 里按 2:1 分
    picks = Counter(sample_choice(probs, rolls([r]), 0.15) for r in range(1, 101))
    assert set(picks) == {'a01', 'a02'}
    assert picks['a01'] == 67 and picks['a02'] == 33
    # 同一个数，同一个结果
    assert sample_choice(probs, rolls([42]), 0.15) == sample_choice(probs, rolls([42]), 0.15)
    assert sample_choice(probs, rolls([1]), 0.15) == 'a01'
    assert sample_choice(probs, rolls([100]), 0.15) == 'a02'


def test_sample_choice_single_candidate_does_not_roll() -> None:
    def boom() -> int:
        raise AssertionError('should not roll')

    assert sample_choice({'a01': 0.9, 'a02': 0.1}, boom, 0.15) == 'a01'


def test_sample_choice_argmax_when_nothing_passes_floor() -> None:
    def boom() -> int:
        raise AssertionError('should not roll')

    probs = {f'a{i}': 0.1 for i in range(9)} | {'a9': 0.1, 'none': 0.0}
    probs['a3'] = 0.12
    assert sample_choice(probs, boom, 0.15) == 'a3'


def test_sample_choice_rejects_bad_roll() -> None:
    for bad in (0, 101, 1.5, -0.1):
        with pytest.raises(ValueError):
            sample_choice({'a': 0.5, 'b': 0.5}, lambda bad=bad: bad, 0.15)


def test_sample_choice_accepts_unit_float() -> None:
    probs = {'a01': 0.6, 'a02': 0.3, 'none': 0.1}
    assert sample_choice(probs, lambda: 0.0, 0.15) == 'a01'
    assert sample_choice(probs, lambda: 0.66, 0.15) == 'a01'
    assert sample_choice(probs, lambda: 0.67, 0.15) == 'a02'
    assert sample_choice(probs, lambda: 0.999, 0.15) == 'a02'


def test_mode(settings: Settings) -> None:
    assert mode(settings, 'classify') == 'jev_first'
    settings.route.decide_soul = 'claude_first'
    assert mode(settings, 'decide_soul') == 'claude_first'
    assert mode(settings, 'unknown_category') == 'jev_first'
