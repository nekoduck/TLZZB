"""C4 选项措辞的校验与模板兜底（方案 2.3、7.8）。"""

from __future__ import annotations

from typing import Any

import pytest

from tianlong.kernel import actions
from tianlong.llm import LLM, CallMeta, LLMRefusal
from tianlong.llm.schemas import OptionsOut, OptionText
from tianlong.narrator import OptionsReport, phrase_options, template_options
from tianlong.narrator.checks import build_checker, text_len
from tianlong.narrator.options_text import clean_option

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
        (ids[0], '走3步去山门'), (ids[1], '完成任务：打量段公子'), (ids[2], '走3步去山门'),
    ]))
    rep = OptionsReport()
    texts = await phrase_options(llm, state, defs, opts, report=rep)
    assert texts == template_options(state, defs, opts)
    assert rep.rejected[0][2] == ['digit'] and rep.rejected[1][2] == ['banned:任务']
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
