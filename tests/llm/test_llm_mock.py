from __future__ import annotations

from typing import Any

import pytest

from tianlong.config import Settings
from tianlong.llm import STAR_PREFIX, make_llm
from tianlong.llm.claude import BatchItem, CallMeta, split_sentences
from tianlong.llm.errors import LLMUnavailable
from tianlong.llm.jev import ChoiceAnswer, ScoreAnswer
from tianlong.llm.schemas import Decision, OptionsOut, OptionText

QUESTIONS: dict[str, Any] = {
    'action': {
        'type': 'choice',
        'instructions': '此刻你会做哪一件事？',
        'criteria': {'a01': '收剑退后', 'a02': f'{STAR_PREFIX}卖个破绽逼对手认输', 'none': '什么也不做'},
    },
    'speak': {'type': 'noul', 'instructions': '你此刻会开口说话。'},
    'weight': {'type': 'score', 'instructions': '分量？', 'criteria': ['无关', '有用', '改变局面']},
}


async def test_mock_jev_star_and_defaults(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    a = await llm.jev.ask('J2', '状态', QUESTIONS)
    act = a['action']
    assert isinstance(act, ChoiceAnswer)
    assert act.choice == 'a02' and act.confidence == 1.0 and act.p('a02') == 1.0
    assert a['speak'].noul == 0.0
    w = a['weight']
    assert isinstance(w, ScoreAnswer)
    assert w.score == 1.0 and w.level == 1 and w.confidence == 1.0


async def test_mock_jev_first_option_without_star(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    q = {'action': {'type': 'choice', 'instructions': 'x', 'criteria': {'b1': '甲', 'b2': '乙'}}}
    a = await llm.jev.ask('J3', 's', q)
    assert a['action'].choice == 'b1'
    # 描述是对象时也认得 ★
    q2 = {'action': {'type': 'choice', 'instructions': 'x',
                     'criteria': {'b1': {'what': '甲'}, 'b2': {'what': STAR_PREFIX + '乙'}}}}
    a2 = await llm.jev.ask('J3', 's', q2)
    assert a2['action'].choice == 'b2'


async def test_mock_jev_hints(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    a = await llm.jev.ask('J2', 's', QUESTIONS, hints={'speak': 0.8, 'weight': 7, 'action': 'none'})
    assert a['speak'].noul == 0.8
    assert a['weight'].score == 2.0  # 截在刻度之内
    assert a['action'].choice == 'none'
    a2 = await llm.jev.ask('J2', 's', QUESTIONS, hints={'weight': 0.5, 'speak': True})
    assert a2['speak'].noul == 1.0
    assert a2['weight'].score == 0.5 and a2['weight'].levels == {0: 0.5, 1: 0.5, 2: 0.0}
    assert a2['weight'].confidence == pytest.approx(0.25)  # 官方公式：两级相邻各半


async def test_mock_jev_records_without_cost(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    await llm.jev.ask('J5', 's', {'mind': {'type': 'noul', 'instructions': 'x'}}, hints={'mind': 0.9})
    rec = llm.recorder.records[-1]
    assert rec['provider'] == 'mock-jev' and rec['cost'] == 0.0 and rec['hints'] == {'mind': 0.9}
    assert llm.ledger.spent == 0.0


async def test_mock_claude_call_json(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    d = Decision(inner='先看看', action='none', speech=None, display='皱眉')
    obj, meta = await llm.claude.call_json('C2_soul', ['规则', '人物卡'], '处境', Decision, mock_default=lambda: d)
    assert obj == d and meta.mock and meta.provider == 'mock-claude'
    obj2, _ = await llm.claude.call_json('C4', ['规则'], 'x', OptionsOut,
                                         mock_default={'options': [{'id': 'a01', 'text': '你走过去'}]})
    assert obj2.options == [OptionText(id='a01', text='你走过去')]
    with pytest.raises(LLMUnavailable):
        await llm.claude.call_json('C1', ['规则'], 'x', OptionsOut)
    assert llm.recorder.records[-1]['error']['type'] == 'LLMUnavailable'


async def test_mock_claude_stream_sentences(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    text = '你推开门。厅里静得出奇！有人问：「谁？」\n你没有答话'
    metas: list[CallMeta] = []
    pieces = [p async for p in llm.claude.stream_text('C3', ['规则'], 'x', mock_text=text, meta_out=metas)]
    assert ''.join(pieces) == text
    assert pieces[0] == '你推开门。'
    assert pieces[1] == '厅里静得出奇！'
    assert len(pieces) >= 4
    assert metas[0].mock and metas[0].stop_reason == 'end_turn'
    assert llm.recorder.records[-1]['response']['pieces'] == pieces


async def test_mock_stream_early_stop_still_recorded(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    gen = llm.claude.stream_text('C3', ['规则'], 'x', mock_text='一。二。三。四。')
    got = []
    async for p in gen:
        got.append(p)
        if len(got) == 2:
            break
    await gen.aclose()
    rec = llm.recorder.records[-1]
    assert rec['response']['pieces'] == ['一。', '二。'] and rec['response']['stopped_early'] is True


async def test_mock_batch(settings: Settings) -> None:
    llm = make_llm(settings, mock=True, run_dir=None)
    items = [BatchItem('x1', ['sys'], 'u1', OptionsOut, mock_default={'options': []}),
             BatchItem('x2', ['sys'], 'u2', None, mock_default='纯文本')]
    bid = await llm.claude.batch_submit('P1', items)
    st = await llm.claude.batch_poll(bid)
    assert st.ended
    res = await llm.claude.batch_results(bid, 'P1')
    assert res['x1'].ok and res['x1'].obj == OptionsOut(options=[])
    assert res['x2'].ok and res['x2'].text == '纯文本'


def test_split_sentences() -> None:
    assert split_sentences('甲。乙！丙？') == ['甲。', '乙！', '丙？']
    assert split_sentences('他说：「走吧。」然后走了') == ['他说：「走吧。」', '然后走了']
    assert split_sentences('') == []
    assert ''.join(split_sentences('a……b\n\nc')) == 'a……b\n\nc'
