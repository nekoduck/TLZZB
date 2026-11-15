"""模板叙述、模板选项、戏内回话（方案 4.3 退让表、2.3、2.4）。"""

from __future__ import annotations

from tianlong.kernel import actions, perception
from tianlong.kernel.perception import PlayerView, ViewEvent
from tianlong.narrator.checks import build_checker, split_sentences, text_len
from tianlong.narrator.templates import (
    diegetic_reply,
    duration_text,
    fact_list,
    option_template,
    template_narration,
    template_sentences,
    travel_summary,
    warning_text,
)

from .conftest import act, fresh_world, opening_scene


def _rest(f_text: str, actor: str | None) -> str:
    t = f_text.split('（语气')[0]
    if actor and t.startswith(actor):
        t = t[len(actor):]
    return t


def _assert_covers_in_order(text: str, view: PlayerView) -> None:
    pos = -1
    for f in fact_list(view):
        key = _rest(f.text, f.actor)
        key = key.split('：')[-1] if '：' in key else key
        i = text.find(key, pos + 1)
        assert i > pos, f'fact {f.text!r} missing or out of order in {text!r}'
        pos = i


def test_template_covers_every_fact_in_order_and_fits_length() -> None:
    defs, state = fresh_world()
    view, _ = opening_scene(state, defs)
    text = template_narration(view)
    _assert_covers_in_order(text, view)
    assert 80 <= text_len(text) <= 220
    assert '冷冷地' in text  # 语气写成状语
    ck = build_checker(state, defs, view)
    for s in split_sentences(text):
        assert ck.check(s) == [], s


def test_template_is_deterministic() -> None:
    defs, state = fresh_world()
    view, _ = opening_scene(state, defs)
    defs2, state2 = fresh_world()
    view2, _ = opening_scene(state2, defs2)
    assert template_narration(view) == template_narration(view2)


def _fake_view(n: int) -> PlayerView:
    evs = [
        ViewEvent(id=i + 1, text=f'第{i}个汉子往前走了一步又退了回去', actor=f'汉子{i}', verb='go', outcome='ok')
        for i in range(n)
    ]
    return PlayerView(clock='第一日 辰时', location='一座大厅', location_desc='厅里宽敞。', scene_mode='calm',
                      events=evs)


def test_many_facts_are_capped_at_the_hard_limit() -> None:
    view = _fake_view(40)
    text = template_narration(view, max_len=220, hard_max=400)
    assert 220 < text_len(text) <= 400
    assert template_narration(_fake_view(2), max_len=220, hard_max=400).count('汉子') == 2


def test_no_facts_writes_scene_and_quiet_line() -> None:
    view = PlayerView(clock='第一日 辰时', location='一座大厅', location_desc='厅里宽敞，青砖地扫得发亮。',
                      scene_mode='calm', present=['一个老仆'], body=['肚子饿得发慌'])
    sents = template_sentences(view)
    text = ''.join(sents)
    assert '厅里宽敞' in text
    assert '动静' in text or '变化' in text
    assert '你只觉肚子饿得发慌。' in text


def test_player_words_are_quoted_and_digits_turned_chinese() -> None:
    view = PlayerView(clock='第一日 辰时', location='一座大厅', location_desc='', scene_mode='calm')
    fs = fact_list(view, player_fact='你对青衫公子说', spoken_text='给你3两银子')
    assert fs[0].line == '你对青衫公子说：「给你三两银子」'
    assert '3' not in template_narration(view, player_fact='你对青衫公子说', spoken_text='给你3两银子')


def test_move_turn_writes_the_new_place() -> None:
    defs, state = fresh_world()
    evs = act(state, defs, state.player, 'go', exit='jianhu_gate')
    view = perception.player_view(state, defs, evs)
    text = template_narration(view)
    assert view.location_desc.split('，')[0] in text


def test_option_templates_are_short_intents_without_unknown_names() -> None:
    defs, state = fresh_world()
    p = state.player
    ck = build_checker(state, defs)
    acts = actions.legal_actions(state, defs, p)
    assert len(acts) > 50
    for a in acts:
        t = option_template(state, defs, a)
        assert t and text_len(t) <= 30, (a.signature, t)
        assert ck.check(t) == [], (a.signature, t)
        assert '⟦' not in t
    go = next(a for a in acts if a.verb == 'go')
    t = option_template(state, defs, go)
    assert option_template(state, defs, go, avoid=[t]) != t


def test_diegetic_replies_never_leak_names() -> None:
    defs, state = fresh_world()
    ck = build_checker(state, defs)
    for kind in ('unclear', 'impossible', 'meta', 'absurd', 'whatever'):
        for salt in range(4):
            r = diegetic_reply(kind, salt=salt)
            assert r and ck.check(r) == [], r
    assert '恍惚' in diegetic_reply('absurd') or '恍惚' in diegetic_reply('absurd', salt=1)
    # 缘由里的实体按玩家视角渲染：不知道名字的人用叫法
    r = diegetic_reply('impossible', detail='⟦duan_yu⟧挡在门口', state=state, defs=defs, ok=ck.ok)
    assert '段公子挡在门口' in r and '段誉' not in r
    r = diegetic_reply('impossible', detail='钟灵不肯让开', ok=ck.ok)
    assert '钟灵' not in r


def test_warning_states_danger_and_keeps_scene() -> None:
    defs, state = fresh_world()
    a = actions.build(state, defs, state.player, 'attack', {'target': 'zuo_zimu', 'intent': 'kill'})
    w = warning_text(state, defs, a)
    assert '左子穆' in w and '性命' in w and '一如方才' in w
    assert build_checker(state, defs).check(w) == []


def test_travel_summary() -> None:
    assert travel_summary(13, '剑湖宫·前院山门') == '你一路走了一个多时辰，来到剑湖宫的前院山门。'
    assert duration_text(1) == '一刻工夫'
    assert duration_text(16) == '约莫两个时辰'
    assert duration_text(6) == '小半个时辰'
