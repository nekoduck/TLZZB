from __future__ import annotations

from werewolf.kernel.types import Role
from werewolf.llm.render import decision_state, public_state
from werewolf.mind.types import Memory, MindSnapshot, Thought
from werewolf.testing import duel_scenario

from .helpers import persona

SECTIONS = ['【我是谁】', '【此刻心情】', '【交情】', '【想起的事】', '【昨夜的想法】', '【场上】']


def _snap(seat: int, role: Role) -> MindSnapshot:
    return MindSnapshot(
        seat=seat, day=2, role=role, suspicion={2: 0.7, 5: 0.4, 7: 0.1}, public_suspicion={2: 0.5},
        trust={7: 0.8, 8: -0.6}, mood=35, stress=62, state='tense',
        thoughts=[Thought(key='a', text='3号当众踩我', value=-8, day=2)],
        memories=[Memory(event_id=3, day=1, kind='accused_me', about=[3], text='3号第一天踩过我', intensity=6)],
        strategy='deep', focus=2, vote_intent=2, last_thought='昨晚没睡好')


def test_decision_state_sections_in_order_and_content() -> None:
    sc = duel_scenario()
    v = sc.view(3)  # 预言家，验过 7 号和 2 号
    text = decision_state(v, _snap(3, Role.SEER), persona(), ['第 1 天，3 号自称预言家，报 7 号是好人'])
    pos = [text.index(s) for s in SECTIONS]
    assert pos == sorted(pos)
    assert '你的身份是预言家' in text
    assert '7号是好人' in text and '2号是狼人' in text  # 自己的查验
    assert '3号当众踩我' in text and '3号第一天踩过我' in text and '昨晚没睡好' in text
    assert '对8号：有些不满' in text and '对7号：很感激' in text
    assert '2号70%' in text
    assert '紧张' in text and '62' in text


def test_decision_state_wolf_and_witch_private_facts() -> None:
    sc = duel_scenario()
    w = decision_state(sc.view(2), _snap(2, Role.WEREWOLF), persona(), [])
    assert '你的狼队友：5号、8号' in w and '你的身份是狼人' in w
    wi = decision_state(sc.view(6), _snap(6, Role.WITCH), persona(), [])
    assert '解药还在' in wi and '毒药还在' in wi


def test_decision_state_never_marks_human_or_leaks_text() -> None:
    sc = duel_scenario()  # 真人坐 1 号
    for seat in (1, 3, 7):
        text = decision_state(sc.view(seat), _snap(seat, sc.roles[seat]), persona(), [])
        for banned in ('真人', 'is_human', '玩家', '人类', 'human'):
            assert banned not in text
    # 事件里的发言原文不会出现（视图里本来就没有）
    sc.speech(5, '独特的金丝雀字符串QWERTY', [])
    assert 'QWERTY' not in decision_state(sc.view(5), _snap(5, Role.WEREWOLF), persona(), [])
    # 座位一律「N号」，不出现名字（给真人起个名字也不会进来）
    sc2 = duel_scenario()
    sc2.names[1] = '金丝雀小明'
    assert '金丝雀小明' not in decision_state(sc2.view(4), _snap(4, Role.VILLAGER), persona(), [])


def test_public_state_has_no_hidden_facts() -> None:
    sc = duel_scenario()
    pv = sc.view(2).public()
    text = public_state(pv, {3: 0.6, 7: 0.3}, persona(), ['第 1 天，3 号自称预言家'], seat=2)
    assert '【我是谁】' in text and '【场上】' in text
    assert '狼人' not in text and '队友' not in text and '预言家' in text  # 只有公开事实里的称呼
    assert '3号60%' in text
