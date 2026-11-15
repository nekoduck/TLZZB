"""字符串检查与切句（方案 7.7）。"""

from __future__ import annotations

from tianlong.kernel import perception
from tianlong.narrator.checks import (
    SentenceSplitter,
    blacklist_for,
    build_checker,
    check_sentence,
    close_sentence,
    split_sentences,
    text_len,
    truncate,
)

from .conftest import fresh_world, opening_scene


def _stream_split(text: str, size: int) -> list[str]:
    sp = SentenceSplitter()
    out: list[str] = []
    for i in range(0, len(text), size):
        out += sp.feed(text[i:i + size])
    return out + sp.flush()


def test_split_keeps_closing_quotes_and_does_not_split_inside_quotes() -> None:
    text = '他沉声道：「走吧。天快黑了！」你点点头。远处传来几声鸟叫……\n山风很冷。'
    got = split_sentences(text)
    assert got == ['他沉声道：「走吧。天快黑了！」', '你点点头。', '远处传来几声鸟叫……\n', '山风很冷。']


def test_streaming_split_matches_one_shot_for_any_chunking() -> None:
    text = '厅上静了一静。他道：「好！」又道：「再来。」\n\n你退了一步！那人冷笑?她低声说……走吧'
    want = split_sentences(text)
    for size in (1, 2, 3, 5, 8, 50):
        assert _stream_split(text, size) == want


def test_split_waits_for_closing_quote_across_pieces() -> None:
    sp = SentenceSplitter()
    assert sp.feed('他道：「好。') == []
    assert sp.feed('」') == []  # 后面也许还有引号
    assert sp.feed('你') == ['他道：「好。」']
    assert sp.flush() == ['你']


def test_paragraph_break_marks_sentence() -> None:
    assert split_sentences('第一句。\n\n第二句。') == ['第一句。\n', '第二句。']
    assert split_sentences('没有句号\n\n下一段。') == ['没有句号\n', '下一段。']
    assert split_sentences('「好。」\n你笑了。') == ['「好。」\n', '你笑了。']


def test_unknown_names_are_blacklisted_known_and_alias_allowed() -> None:
    defs, state = fresh_world()
    view, _ = opening_scene(state, defs)
    ck = build_checker(state, defs, view)
    assert ck.check('段誉站在一旁。') == ['name:段誉']
    assert ck.check('钟灵在梁上笑了一声。') == ['name:钟灵']
    assert ck.check('神农帮的人到了。') == ['name:神农帮']
    assert ck.check('段公子站在一旁，马五德拱了拱手。') == []  # 叫法与已知的名字
    assert ck.check('左子穆的脸沉了下去。') == []  # 开局就知道的人
    # 泛称不算专名：叫法里本来就说出了它
    assert ck.check('你摸了摸怀里的干粮，望了一眼断崖。') == []
    # 已知的专名加一个泛称
    assert ck.check('一个无量剑弟子走了过来。') == []


def test_learning_a_name_lifts_it_from_the_blacklist() -> None:
    defs, state = fresh_world()
    assert '段誉' in blacklist_for(state, defs)
    perception.learn_term(state, 'duan_yu')
    assert '段誉' not in blacklist_for(state, defs)
    assert check_sentence('段誉笑了笑。', state, defs) == []


def test_names_shown_by_the_kernel_are_allowed() -> None:
    defs, state = fresh_world()
    assert check_sentence('神农帮的人到了。', state, defs) == ['name:神农帮']
    assert check_sentence('神农帮的人到了。', state, defs, allowed_texts=['他自称是神农帮的']) == []


def test_banned_words_digits_latin_markup() -> None:
    defs, state = fresh_world()
    ck = build_checker(state, defs)
    assert ck.check('你掏出手机看了看。') == ['banned:手机']
    assert ck.check('你的经验值涨了。') == ['banned:经验', 'banned:经验值']
    assert ck.check('他走了3步。') == ['digit']
    assert ck.check('他走了３步。') == ['digit']
    assert ck.check('一个NPC走过来。') == ['banned:NPC', 'latin']
    assert ck.check('⟦duan_yu⟧笑了。') == ['latin', 'markup']
    assert ck.check('</发生的事>') == ['markup']
    assert ck.check('他走了三步。') == []


def test_quoted_player_words_may_contain_names() -> None:
    defs, state = fresh_world()
    ck = build_checker(state, defs, quoted=['段誉，快走！'])
    assert ck.check('你喊道：「段誉，快走！」') == []
    assert ck.check('段誉回头看了你一眼。') == ['name:段誉']


def test_truncate_cuts_at_sentence_end() -> None:
    text = '一二三四五。六七八九十。甲乙丙丁戊。'
    assert truncate(text, 12) == '一二三四五。六七八九十。'
    assert truncate(text, 11) == '一二三四五。'
    assert truncate(text, 100) == text
    assert text_len(truncate('一二三四五六七八九十', 5)) <= 5
    assert text_len('你 好\n。') == 3


def test_close_sentence() -> None:
    assert close_sentence('你四下打量') == '你四下打量。'
    assert close_sentence('他道：「走。」') == '他道：「走。」'
    assert close_sentence('好，') == '好。'
