from __future__ import annotations

import pytest

from werewolf.llm.check import check_rules, check_speech
from werewolf.llm.smoke import brief_from_case, eval_check_rules, load_yaml
from werewolf.mind.types import PublicMood

from .conftest import build_gateway, noul
from .helpers import wolf_brief

CASES = load_yaml('check_cases.yaml')


def test_case_file_shape() -> None:
    assert sum(c['leak'] for c in CASES) >= 20 and sum(not c['leak'] for c in CASES) >= 20
    assert len({c['id'] for c in CASES}) == len(CASES)


@pytest.mark.parametrize('case', [c for c in CASES if c['leak'] and not c.get('needs_jev')], ids=lambda c: c['id'])
def test_rules_block_leaking_cases(case) -> None:
    assert check_rules(case['text'], brief_from_case(case)), case['why']


@pytest.mark.parametrize('case', [c for c in CASES if not c['leak']], ids=lambda c: c['id'])
def test_rules_pass_clean_cases(case) -> None:
    assert check_rules(case['text'], brief_from_case(case)) == []


def test_eval_check_rules_summary() -> None:
    r = eval_check_rules(CASES)
    assert r['clean_blocked'] == []
    assert set(r['leak_missed']) == {c['id'] for c in CASES if c.get('needs_jev')}


def test_violation_labels() -> None:
    b = wolf_brief()
    assert check_rules('我是预言家，昨晚验了3号，是狼人。今天我投3号。', b) == []
    bad = check_rules('我是预言家，昨晚验了3号，是狼人。我跟你投7号。', b)
    assert 'off_plan:vote_intent:7号' in bad and any(x.startswith('seat_not_in_plan') for x in bad)
    assert 'missing:vote_intent:3号' in check_rules('我是预言家，昨晚验了3号，是狼人。', b)
    assert 'missing:claim_role:seer' in check_rules('昨晚验了3号，是狼人。今天我投3号。', b)
    assert check_rules('', b) == ['empty']
    assert any(x.startswith('too_long') for x in check_rules('我是预言家，昨晚验了3号，是狼人。今天我投3号。' * 8, b))
    assert 'out_of_character' in check_rules('作为AI，我是预言家，昨晚验了3号，是狼人。今天我投3号。', b)
    assert any(x.startswith('leak_phrase') for x in check_rules('我们狼人要赢。我是预言家，昨晚验了3号，是狼人。今天我投3号。', b))
    # 「别说我是狼」「我不是狼」不算露底
    assert check_rules('3号你别说我是狼，我不是狼。我是预言家，昨晚验了3号，是狼人。今天我投3号。', b) == []


def test_seat_rule_allows_addressed_own_record_self_and_rivals() -> None:
    b = wolf_brief().model_copy(update={'addressed': [6], 'own_record': ['第 1 天，2 号投了 9 号'],
                                        'mood': PublicMood(hints=['4号连着两天踩我'])})
    ok = '6号别急。我是预言家，昨晚验了3号，是狼人。今天我投3号。9号昨天我也看过，我是2号。4号你别踩我。'
    assert check_rules(ok, b) == []
    assert any(x.startswith('seat_not_in_plan:1号') for x in check_rules(ok + '1号呢？', b))


async def test_check_speech_runs_jev_after_rules(cfg) -> None:
    calls = []

    def fn(kind, state, questions):
        calls.append((kind, state))
        return {q: noul(0.1) for q in questions}

    gw = build_gateway(cfg, jev_fn=fn)
    text = '我是预言家，昨晚验了3号，是狼人。今天我投3号。'
    assert await check_speech(gw, text, wolf_brief()) == []
    assert calls and calls[0][0] == 'J5_check' and text in calls[0][1] and '真人' not in calls[0][1]
    # 状态里只有口径与要点，没有任何队友、底牌
    assert '5号' not in calls[0][1] and '队友' not in calls[0][1]


async def test_check_speech_skips_jev_when_rules_fail(cfg) -> None:
    gw = build_gateway(cfg, jev_fn=lambda *a: {})
    bad = await check_speech(gw, '我们狼人投7号', wolf_brief())
    assert bad and gw.llm.jev.calls == []


@pytest.mark.parametrize(('qid', 'p', 'blocked'), [('reveals_wolf', 0.49, False), ('reveals_wolf', 0.5, True),
                                                   ('wrong_role', 0.7, True), ('night_leak', 0.9, True)])
async def test_check_speech_block_threshold(cfg, qid: str, p: float, blocked: bool) -> None:
    def fn(kind, state, questions):
        return {q: noul(p if q == qid else 0.0) for q in questions}

    gw = build_gateway(cfg, jev_fn=fn)
    res = await check_speech(gw, '我是预言家，昨晚验了3号，是狼人。今天我投3号。', wolf_brief())
    assert bool(res) is blocked
    if blocked:
        assert res[0].startswith(f'jev:{qid}')


async def test_check_speech_threshold_comes_from_settings(cfg) -> None:
    gw = build_gateway(cfg, jev_fn=lambda k, s, q: {n: noul(0.6) for n in q})
    gw.ww = gw.ww.model_copy(update={'check': gw.ww.check.model_copy(update={'block': 0.7})})
    assert await check_speech(gw, '我是预言家，昨晚验了3号，是狼人。今天我投3号。', wolf_brief()) == []


async def test_check_speech_jev_failure_falls_back_to_rules(cfg) -> None:
    def boom(*a):
        raise ValueError('x')

    gw = build_gateway(cfg, jev_fn=boom)
    assert await check_speech(gw, '我是预言家，昨晚验了3号，是狼人。今天我投3号。', wolf_brief()) == []
    off = build_gateway(cfg, jev_fn=lambda *a: {}, offline=True)
    assert await check_speech(off, '我是预言家，昨晚验了3号，是狼人。今天我投3号。', wolf_brief()) == []
