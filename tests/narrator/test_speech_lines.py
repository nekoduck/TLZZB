"""说话的事写成通顺的一句（T1、P3）：模板叙述里不再有「对你答应了：答应情境」「问：问对方的来历」
「打了个招呼：自称你」；同一回合里同一句只写一次；玩家自己那一招的选项不带主语。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tianlong.kernel.perception import PlayerView, ViewEvent
from tianlong.narrator import narrate_text
from tianlong.narrator.narrate import covered_heuristic
from tianlong.narrator.templates import (
    Fact,
    _join_lines,
    fact_list,
    option_template,
    speech_sentence,
    template_narration,
)

from .conftest import fresh_world


def _sp(act: str, to: list[str], gist: str = '', stance: str = 'plain', **kw: object) -> dict:
    return {'act': act, 'to': to, 'stance': stance, 'gist': gist, 'spoken_text': None, 'whisper': False, **kw}


@pytest.mark.parametrize(
    ('actor', 'sp', 'by_player', 'want'),
    [
        # 要旨里的「对方」写成听话的人
        ('你', _sp('ask', ['干光豪'], '问对方的来历'), True, '你问干光豪的来历。'),
        ('你', _sp('comfort', ['段公子'], '宽慰对方几句'), True, '你宽慰段公子几句。'),
        ('你', _sp('request', ['段公子'], '劝对方拱手赔个不是', whisper=True), True, '你低声劝段公子拱手赔个不是。'),
        ('马五德', _sp('bargain', ['你'], '要对方先把碎银交给自己'), False, '马五德要你先把碎银交给自己。'),
        ('左子穆', _sp('taunt', ['辛双清'], '激对方动手，语带讥讽', 'mocking'), False, '左子穆似笑非笑地激辛双清动手。'),
        ('钟灵', _sp('plead', ['司空玄'], '求对方放了自己，语气恳求', 'pleading'), False, '钟灵恳切地求司空玄放了自己。'),
        # 答应、回绝：不再是「对你答应了：答应……」
        ('段公子', _sp('agree', ['你'], '答应拱手赔个不是'), False, '段公子答应你拱手赔个不是。'),
        ('你', _sp('agree', ['马五德'], '答应把碎银交给马五德'), True, '你答应把碎银交给马五德。'),
        ('司空玄', _sp('refuse', ['你'], '不肯逼问钟灵，语气冷淡', 'cold'), False, '司空玄冷冷地回绝了你，不肯逼问钟灵。'),
        ('一个瘦高个儿', _sp('refuse', ['你'], '不肯回答，语气冷淡', 'cold'), False, '一个瘦高个儿冷冷地回绝了你，不肯回答。'),
        ('司空玄', _sp('agree', ['你'], '答应了'), False, '司空玄答应了你。'),
        # 招呼：玩家自己不写「自称你」；别人只有语气的要旨不重复写
        ('你', _sp('greet', ['干光豪'], '报了自己的姓名'), True, '你向干光豪打了个招呼。'),
        ('容子矩', _sp('greet', ['左子穆'], '语气热络', 'warm'), False, '容子矩热络地向左子穆打了个招呼。'),
        ('干光豪', _sp('greet', ['你'], '自称干光豪'), False, '干光豪向你打了个招呼，自称干光豪。'),
        # 玩家从选项里提的请求：所求之事以对方为主语
        ('你', _sp('request', ['马五德'], '教你行商的本事'), True, '你求马五德教你行商的本事。'),
        ('你', _sp('plead', ['司空玄'], '放了钟灵'), True, '你求司空玄放了钟灵。'),
        ('你', _sp('ask', ['马五德'], '问起左子穆'), True, '你向马五德问起左子穆。'),
        # 告知：转述的事实里嵌着的冒号改成逗号
        ('段誉', _sp('tell', ['你'], '龚光杰对段誉出言相激：激对方动手'), False, '段誉对你说：龚光杰对段誉出言相激，激对方动手。'),
        ('马五德', _sp('warn', ['你'], '叫你别多嘴，语气冷淡', 'cold'), False, '马五德冷冷地提醒你：叫你别多嘴。'),
        ('司空玄', _sp('threaten', ['你', '段誉'], '要对方识相些，否则不客气', 'angry'), False,
         '司空玄沉着脸要你和段誉识相些，否则不客气。'),
        ('左子穆', _sp('tell', ['甲', '乙', '丙'], '神农帮的人到了山下'), False, '左子穆对众人说：神农帮的人到了山下。'),
    ],
)
def test_speech_sentence_reads_as_one_sentence(actor: str, sp: dict, by_player: bool, want: str) -> None:
    assert speech_sentence(actor, sp, by_player=by_player) == want


def _view(*events: ViewEvent) -> PlayerView:
    return PlayerView(clock='第一日 辰时', location='一座大厅', location_desc='', scene_mode='calm', events=list(events))


def test_bad_record_style_lines_never_reach_the_template() -> None:
    view = _view(
        ViewEvent(id=1, text='你对段公子请求：劝对方拱手赔个不是', actor='你', verb='say', outcome='ok', by_player=True,
                  speech=_sp('request', ['段公子'], '劝对方拱手赔个不是', whisper=True)),
        ViewEvent(id=2, text='段公子对你答应了：答应拱手赔个不是', actor='段公子', verb='say', outcome='ok',
                  speech=_sp('agree', ['你'], '答应拱手赔个不是')),
        ViewEvent(id=3, text='段公子拱手赔了个不是', actor='段公子', verb='apologize', outcome='ok'),
    )
    text = template_narration(view)
    assert text.startswith('你低声劝段公子拱手赔个不是。')
    assert '段公子答应你拱手赔个不是' in text and '拱手赔了个不是' in text
    for bad in ('请求：', '答应了：', '对方', '情境'):
        assert bad not in text, (bad, text)
    # 给 J5 核对的事实句仍是内核那一句
    assert fact_list(view)[1].text == '段公子对你答应了：答应拱手赔个不是'


def test_player_free_speech_is_quoted_with_a_plain_head() -> None:
    ev = ViewEvent(id=1, text='你对干光豪问：这里是什么地方', actor='你', verb='say', outcome='ok', by_player=True,
                   speech={**_sp('ask', ['干光豪'], '这里是什么地方'), 'spoken_text': '这里是什么地方？'})
    assert fact_list(_view(ev))[0].line == '你向干光豪问道：「这里是什么地方？」'
    ev2 = ViewEvent(id=2, text='你对甲、乙、丙说：好看', actor='你', verb='say', outcome='ok', by_player=True,
                    speech={**_sp('tell', ['甲', '乙', '丙'], '好看'), 'spoken_text': '这场比剑当真好看'})
    assert fact_list(_view(ev2))[0].line == '你对众人说：「这场比剑当真好看」'


def test_unheard_whisper_keeps_the_overheard_line() -> None:
    ev = ViewEvent(id=1, text='甲凑近乙低声说了几句', actor='甲', verb='say', outcome='ok',
                   speech={'act': 'whisper', 'to': ['乙'], 'whisper': True})
    assert fact_list(_view(ev))[0].line == '甲凑近乙低声说了几句。'


def test_the_same_line_twice_in_a_turn_is_written_once() -> None:
    """同一个人在两个微轮里把同一句话又说了一遍：原来写成「向你打听……，又向你打听……」。"""
    sp = _sp('ask', ['你'], '打听这一带最近出了什么事')
    view = _view(
        ViewEvent(id=1, text='司空玄对你问：打听这一带最近出了什么事', actor='司空玄', verb='say', outcome='ok', speech=sp),
        ViewEvent(id=2, text='司空玄对你问：打听这一带最近出了什么事', actor='司空玄', verb='say', outcome='ok', speech=sp),
        ViewEvent(id=3, text='司空玄看了你一眼', actor='司空玄', verb='look', outcome='ok'),
    )
    fs = fact_list(view)
    assert [f.event_id for f in fs] == [1, 3]
    text = template_narration(view)
    assert text.count('打听这一带最近出了什么事') == 1
    assert '，又向你打听' not in text


def test_own_setpiece_option_drops_the_subject() -> None:
    """情境里玩家自己那一招的描述以「你」开头：选项写打算，不写成「试着你报上来意」。"""
    defs, state = fresh_world()
    me = state.player
    for desc, want in (
        (f'⟦{me}⟧报上来意', '报上来意'),
        (f'⟦{me}⟧说明自己的来意', '说明自己的来意'),
        (f'⟦{me}⟧和⟦duan_yu⟧闲聊起来', '和段公子闲聊起来'),
    ):
        a = SimpleNamespace(verb='setpiece', params={'situation': 's#1', 'name': 'x'}, desc=desc, signature=desc)
        texts = {option_template(state, defs, a, variant=v) for v in range(4)}
        assert texts == {want, f'试着{want}'}, texts
        assert not any(t.startswith('你') or '试着你' in t for t in texts)
    # 别人的招（不是玩家开头的）照旧
    other = SimpleNamespace(verb='setpiece', params={}, desc='上前劝架', signature='x')
    assert option_template(state, defs, other) in ('试着上前劝架', '上前劝架')


async def test_reworded_speech_lines_count_as_covered(llm, world) -> None:
    """照模板句写出来的叙述算「写到了」：核对之后不在段末把同一句再补一遍。"""
    defs, state = world
    view = _view(
        ViewEvent(id=1, text='你对段公子请求：劝对方拱手赔个不是', actor='你', verb='say', outcome='ok', by_player=True,
                  speech=_sp('request', ['段公子'], '劝对方拱手赔个不是', whisper=True)),
        ViewEvent(id=2, text='段公子对你答应了：答应拱手赔个不是', actor='段公子', verb='say', outcome='ok',
                  speech=_sp('agree', ['你'], '答应拱手赔个不是')),
    )
    fs = fact_list(view)
    assert all(covered_heuristic(f, template_narration(view)) for f in fs)
    text = await narrate_text(llm, state, defs, view)
    assert text.count('你低声劝段公子拱手赔个不是') == 1, text
    assert text.count('段公子答应你拱手赔个不是') == 1, text


def test_said_then_done_is_joined_with_suiji() -> None:
    """同一个人先说了话、紧接着做事：并成一句时接「随即」；接连两件事（都不是话）仍接「又」。"""
    said = Fact(text='x', line='段公子答应你拱手赔个不是。', actor='段公子', event_id=1, spoken=True)
    did = Fact(text='y', line='段公子拱手赔了个不是。', actor='段公子', event_id=2)
    assert _join_lines([said, did]) == ['段公子答应你拱手赔个不是，随即拱手赔了个不是。']
    a = Fact(text='a', line='段公子退了一步。', actor='段公子', event_id=3)
    b = Fact(text='b', line='段公子整了整衣襟。', actor='段公子', event_id=4)
    assert _join_lines([a, b]) == ['段公子退了一步，又整了整衣襟。']
