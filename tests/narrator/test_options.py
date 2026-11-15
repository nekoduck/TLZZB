"""C4 选项措辞的校验与模板兜底（方案 2.3、7.8）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tianlong.kernel import actions
from tianlong.kernel.state import Speech
from tianlong.llm import LLM, CallMeta, LLMRefusal
from tianlong.llm.schemas import OptionsOut, OptionText
from tianlong.narrator import OptionsReport, phrase_options, template_options
from tianlong.narrator.checks import build_checker, text_len
from tianlong.narrator.options_text import clean_option
from tianlong.narrator.templates import OPTION_MAX, option_template, speech_fact

from .conftest import fresh_world, option


def _three(state: Any, defs: Any) -> list[Any]:
    p = state.player
    acts = {a.signature: a for a in actions.legal_actions(state, defs, p)}
    return [
        option(acts['go:exit=jianhu_gate'], '探索'),
        option(acts['examine:target=duan_yu'], '人物'),
        option(acts['hide:feature=beam'], '自保'),
    ]


def _fake_json(texts: list[tuple[str, str]], log: dict[str, Any] | None = None) -> Any:
    async def call_json(kind: str, system_blocks: Any, user_text: str, schema: Any, *, mock_default: Any = None,
                        escalated: bool = False, ttl: str | None = None) -> Any:
        if log is not None:
            log['user'] = user_text
            log['system'] = system_blocks
            log['default'] = mock_default() if callable(mock_default) else mock_default
        out = OptionsOut(options=[OptionText(id=i, text=t) for i, t in texts])
        return out, CallMeta(kind=kind, provider='fake', model='fake')

    return call_json


async def test_mock_gives_distinct_template_options(llm: LLM) -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)
    texts = await phrase_options(llm, state, defs, opts)
    assert texts == template_options(state, defs, opts)
    assert len(set(texts)) == 3
    ck = build_checker(state, defs)
    for t in texts:
        assert 0 < text_len(t) <= 30 and ck.check(t) == []


async def test_valid_model_text_is_used_and_bad_ones_replaced(llm: LLM, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)
    ids = [o.action.id for o in opts]
    log: dict[str, Any] = {}
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([
        (ids[0], '一、去前院山门那边透口气'),
        (ids[1], '凑近打量段誉，看他有何来历'),  # 玩家还不知道的真名
        (ids[2], '趁众人不留神，悄悄攀上厅顶的横梁，在梁上找个暗处躲起来再说，免得被人瞧见'),  # 太长
    ], log))
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    tpl = template_options(state, defs, opts)
    assert texts == ['去前院山门那边透口气', tpl[1], tpl[2]]
    assert rep.source == ['claude', 'template', 'template']
    assert [(i, p) for i, _, p in rep.rejected] == [(1, ['name:段誉']), (2, ['too_long'])]
    # 提示词：场景、称呼表、三个动作（id、事实句、方向标签）
    for i in ids:
        assert i in log['user']
    assert '探索' in log['user'] and '段公子' in log['user'] and '段誉' not in log['user']
    assert [o.text for o in log['default'].options] == tpl


async def test_ids_must_match_in_order(llm: LLM, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)
    ids = [o.action.id for o in opts]
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([
        (ids[1], '多看段公子几眼'), (ids[0], '去山门看看'), (ids[2], '躲到梁上去'),
    ]))
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    tpl = template_options(state, defs, opts)
    assert texts == [tpl[0], tpl[1], '躲到梁上去']
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([(ids[0], '去山门看看')]))  # 少了两条
    texts = await phrase_options(llm, state, defs, opts)
    assert texts == ['去山门看看', tpl[1], tpl[2]]


async def test_digits_banned_words_duplicates_and_last_choice(llm: LLM, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)
    ids = [o.action.id for o in opts]
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([
        (ids[0], '走3步去山门'), (ids[1], '触发主线：打量段公子'), (ids[2], '走3步去山门'),
    ]))
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    assert texts == template_options(state, defs, opts)
    assert rep.rejected[0][2] == ['digit'] and rep.rejected[1][2] == ['banned:主线']
    # 不与上回合选中的那条重复
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([
        (ids[0], '去山门看看'), (ids[1], '多看段公子几眼'), (ids[2], '躲到梁上去'),
    ]))
    texts = await phrase_options(llm, state, defs, opts, avoid=['去山门看看'])
    assert texts[0] != '去山门看看' and texts[1:] == ['多看段公子几眼', '躲到梁上去']
    state.pending_options = [{'text': '躲到梁上去', 'chosen': True}]
    texts = await phrase_options(llm, state, defs, opts)
    assert texts[2] != '躲到梁上去'


async def test_call_failure_falls_back_to_templates(llm: LLM, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)

    async def refuse(*a: Any, **k: Any) -> Any:
        raise LLMRefusal('cyber')

    monkeypatch.setattr(llm.claude, 'call_json', refuse)
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    assert texts == template_options(state, defs, opts)
    assert 'LLMRefusal' in (rep.error or '')
    assert await phrase_options(llm, state, defs, []) == []


def test_template_options_avoid_repeats() -> None:
    defs, state = fresh_world()
    opts = _three(state, defs)
    first = template_options(state, defs, opts)
    again = template_options(state, defs, opts, avoid=[first[0]])
    assert again[0] != first[0]


def test_clean_option() -> None:
    assert clean_option('一、去山门看看。') == '去山门看看'
    assert clean_option('1. 去山门看看') == '去山门看看'
    assert clean_option('「躲起来」') == '躲起来'
    assert clean_option('- 四下看看') == '四下看看'


# ---------------------------------------------------------------- 开口的选项


def _say_option(state: Any, defs: Any, oid: str, tag: str, desc: str, speech: Speech) -> SimpleNamespace:
    """讲述者给的开口选项的样子：动作是 1 刻的 wait，另带 speech、desc、id、signature。"""
    wait = actions.build(state, defs, state.player, 'wait', {'ticks': 1})
    assert wait is not None
    return SimpleNamespace(action=wait, tag=tag, speech=speech, desc=desc, id=oid, signature=f'say:{oid}')


def _spoken_three(state: Any, defs: Any) -> list[Any]:
    persuade = Speech(
        act='request', to=['duan_yu'], whisper=True, gist='劝对方拱手赔个不是',
        ask_action={'verb': 'setpiece', 'params': {'situation': 'bout_laugh#1', 'name': 'apologize'},
                    'desc': '拱手赔个不是'},
    )
    ask = Speech(act='ask', to=['gan_guanghao'], about='gan_guanghao', gist='问对方的来历')
    teach = Speech(act='request', to=['ma_wude'], gist='教⟦player⟧⟦trade⟧',
                   ask_action={'verb': 'teach', 'params': {'target': 'player', 'art': 'trade'},
                               'desc': '教⟦player⟧⟦trade⟧'})
    return [
        _say_option(state, defs, 's01', '介入', '低声劝⟦duan_yu⟧拱手赔个不是', persuade),
        _say_option(state, defs, 's02', '人物', '问一问⟦gan_guanghao⟧的来历', ask),
        _say_option(state, defs, 's03', '成长', '求⟦ma_wude⟧教⟦player⟧⟦trade⟧', teach),
    ]


async def test_speech_options_get_templates_by_speech_act(llm: LLM) -> None:
    defs, state = fresh_world()
    opts = _spoken_three(state, defs)
    texts = await phrase_options(llm, state, defs, opts)
    assert texts == template_options(state, defs, opts)
    assert texts[0] in ('低声劝段公子拱手赔个不是', '悄悄劝段公子拱手赔个不是')
    assert texts[1] in ('问一问一个神色不定的青年剑客的来历', '向一个神色不定的青年剑客请教来历')
    assert texts[2] in ('求马五德教你行商的本事', '开口请马五德教你行商的本事')
    assert len(set(texts)) == 3
    ck = build_checker(state, defs)
    for t in texts:
        assert 0 < text_len(t) <= 30 and ck.check(t) == []
        assert '静静等着' not in t and '等' not in t  # 配的那一刻 wait 不参与措辞
        assert '段誉' not in t and '干光豪' not in t


def test_every_speech_act_has_an_intent_only_template() -> None:
    """每种言语行为的模板句：只写打算，三十字以内，不露玩家不知道的专名，不写对方答不答应。"""
    defs, state = fresh_world()
    fact = next(fid for fid, b in state.chars[state.player].beliefs.items() if fid in state.facts)
    stop = {'verb': 'wait', 'params': {'ticks': 4}, 'stop': True, 'desc': '住手，别再为难⟦duan_yu⟧'}
    along = {'verb': 'wait', 'params': {'ticks': 1}, 'permit': {'verb': 'follow', 'target': 'duan_yu'},
             'desc': '让⟦player⟧跟⟦duan_yu⟧一道走'}
    cases = [
        ('低声提醒⟦duan_yu⟧当心⟦gong_guangjie⟧', Speech(act='warn', to=['duan_yu'], whisper=True,
                                                    gist='要对方当心场中舞剑的青年'), '提醒'),
        (f'提醒⟦zuo_zimu⟧：{state.facts[fact].text}', Speech(act='warn', to=['zuo_zimu'], fact_refs=[fact]), '提醒'),
        (f'把一件事告诉⟦zuo_zimu⟧：{state.facts[fact].text}', Speech(act='tell', to=['zuo_zimu'], fact_refs=[fact]),
         '左子穆'),
        ('替⟦duan_yu⟧向⟦gong_guangjie⟧求情', Speech(act='plead', to=['gong_guangjie'], ask_action=stop), '求'),
        ('替⟦duan_yu⟧向⟦gong_guangjie⟧求情', Speech(act='request', to=['gong_guangjie'], ask_action=stop), '住手'),
        ('求⟦zuo_zimu⟧让⟦player⟧跟⟦duan_yu⟧一道走', Speech(act='request', to=['zuo_zimu'], ask_action=along), '一道走'),
        ('求⟦gong_guangjie⟧放了⟦player⟧', Speech(act='plead', to=['gong_guangjie'], ask_action={
            'verb': 'release', 'params': {'target': 'player'}, 'desc': '放了⟦player⟧'}), '放了你'),
        ('向⟦zuo_zimu⟧打个招呼', Speech(act='greet', to=['zuo_zimu']), '左子穆'),
        ('安慰⟦ma_wude⟧几句', Speech(act='comfort', to=['ma_wude']), '马五德'),
        ('向⟦ma_wude⟧打听⟦jianhu_gate⟧的事', Speech(act='ask', to=['ma_wude'], about='jianhu_gate'), '前院山门'),
        ('答应⟦ma_wude⟧：把⟦tea_sample⟧交给⟦zuo_zimu⟧', Speech(act='agree', to=['ma_wude'], request='R1'), '应'),
        ('回绝⟦ma_wude⟧：把⟦tea_sample⟧交给⟦zuo_zimu⟧', Speech(act='refuse', to=['ma_wude'], request='R1'), '回绝'),
        ('回答⟦ma_wude⟧的问话', Speech(act='tell', to=['ma_wude'], request='R1', fact_refs=[fact]), '回答'),
    ]
    ck = build_checker(state, defs)
    for i, (desc, sp, must) in enumerate(cases):
        o = _say_option(state, defs, f's{i:02d}', '人物', desc, sp)
        seen: list[str] = []
        for variant in range(3):
            t = option_template(state, defs, o.action, variant=variant, speech=sp, desc=desc, signature=o.signature)
            assert 0 < text_len(t) <= OPTION_MAX, t
            assert ck.check(t) == [], (t, ck.check(t))
            assert not any(w in t for w in ('段誉', '龚光杰', '⟦', '答应了', '成功')), t
            seen.append(t)
        assert any(must in t for t in seen), (must, seen)
        # 给 C4 的事实句：言语行为、对谁说、要旨
        fact_line = speech_fact(state, defs, sp, desc)
        assert fact_line.startswith('开口') and '⟦' not in fact_line
        assert '段誉' not in fact_line and '龚光杰' not in fact_line


async def test_c4_payload_and_checks_for_speech_options(llm: LLM, monkeypatch: pytest.MonkeyPatch) -> None:
    defs, state = fresh_world()
    opts = _spoken_three(state, defs)
    log: dict[str, Any] = {}
    monkeypatch.setattr(llm.claude, 'call_json', _fake_json([
        ('s01', '扯一扯段公子的衣袖，低声劝他快赔个不是'),
        ('s02', '问问干光豪是什么来历'),  # 玩家还不知道的真名
        ('s03', '求东家马五德教你几手行商的本事'),
    ], log))
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    tpl = template_options(state, defs, opts)
    assert texts == ['扯一扯段公子的衣袖，低声劝他快赔个不是', tpl[1], '求东家马五德教你几手行商的本事']
    assert rep.source == ['claude', 'template', 'claude']
    assert [(i, p) for i, _, p in rep.rejected] == [(1, ['name:干光豪'])]
    user = log['user']
    # 言语行为、对谁说（玩家眼里的叫法）、要旨、方向；id 用选项自己的编号
    assert 's01｜开口相劝：对段公子低声说，劝对方拱手赔个不是｜方向：介入' in user
    assert 's02｜开口打听：对一个神色不定的青年剑客说，问对方的来历｜方向：人物' in user
    assert 's03｜开口相求：对马五德说，教你行商的本事｜方向：成长' in user
    assert '段誉' not in user and '干光豪' not in user and '⟦' not in user and '静静等着' not in user
    assert [o.id for o in log['default'].options] == ['s01', 's02', 's03']
    assert [o.text for o in log['default'].options] == tpl


async def test_mixed_body_and_speech_options_stay_distinct(llm: LLM) -> None:
    defs, state = fresh_world()
    body = _three(state, defs)
    said = _spoken_three(state, defs)
    opts = [said[0], body[0], said[1]]
    texts = await phrase_options(llm, state, defs, opts)
    assert len(set(texts)) == 3 and texts[0].endswith('赔个不是')
    again = template_options(state, defs, opts, avoid=[texts[0]])
    assert again[0] != texts[0] and '劝' in again[0]
    # 上回合选过的那句话，这回合换个说法
    state.pending_options = [{'text': texts[2], 'chosen': True}]
    assert (await phrase_options(llm, state, defs, opts))[2] != texts[2]
