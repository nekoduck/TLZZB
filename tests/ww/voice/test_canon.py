"""规范说法：声明类言语行为能从文字里读回来；否定、假设、疑问、转述不算。"""

import pytest

from werewolf.kernel.types import ActKind, Role, SpeechAct
from werewolf.voice.canon import claim_diff, claims_in, seats_in


def keys(text: str, day: int | None = 2) -> set[tuple]:
    out = set()
    for a in claims_in(text, day):
        out.add((a.kind.value, a.target, a.role.value if a.role else a.result))
    return out


@pytest.mark.parametrize(
    ('text', 'want'),
    [
        ('我是预言家，昨晚验了7号，是狼人。', {('claim_role', None, 'seer'), ('claim_check', 7, 'wolf')}),
        ('我才是真预言家！', {('claim_role', None, 'seer')}),
        ('我是一张平民牌，没什么信息。', {('claim_role', None, 'villager')}),
        ('我的身份是女巫，昨晚我救了4号。', {('claim_role', None, 'witch'), ('claim_save', 4, None)}),
        ('我就是猎人，投我我就带人。', {('claim_role', None, 'hunter')}),
        ('昨晚验的三号，好人。', {('claim_check', 3, 'good')}),
        ('5号是我的金水，2号是我的查杀。', {('claim_check', 5, 'good'), ('claim_check', 2, 'wolf')}),
        ('我查杀8号，今天出8号。', {('claim_check', 8, 'wolf'), ('vote_intent', 8, None)}),
        ('我给6号发金水。', {('claim_check', 6, 'good')}),
        ('4号是我的银水。', {('claim_save', 4, None)}),
        ('我把9号毒了。', {('claim_poison', 9, None)}),
        ('第一晚我毒了3号。', {('claim_poison', 3, None)}),
        ('今天我投7号。', {('vote_intent', 7, None)}),
        ('这票挂2号，跟我出2号。', {('vote_intent', 2, None)}),
        ('归票5号。', {('vote_intent', 5, None)}),
    ],
)
def test_canonical_forms(text: str, want: set[tuple]) -> None:
    assert keys(text) == want


@pytest.mark.parametrize(
    'text',
    [
        '我不是预言家。',
        '我可不是狼人，别乱踩。',
        '如果我是预言家，我早就跳了。',
        '你是预言家吗？',
        '难道我是女巫？',
        '3号说他是预言家。',
        '3号说：我是狼人。',
        '8号自称预言家，验了3号是狼人。',
        '我是预言家的金水。',
        '我是好人。',
        '3号验了7号是狼人，我信。',
        '他说他救了4号。',
        '女巫救了4号吧。',
        '昨天我投了7号。',
        '我不投7号。',
        '别出7号，出错了怎么办。',
        '3号投7号我看不懂。',
        '你们为什么出7号？',
        '7号是3号的金水。',
        '狼人毒了5号？不对，是女巫。',
    ],
)
def test_traps_yield_nothing(text: str) -> None:
    assert keys(text) == set(), claims_in(text, 2)


def test_night_numbers() -> None:
    a = claims_in('我是预言家，第一晚验了5号，是好人，昨晚验了8号，是狼人。', day=2)
    checks = {(x.target, x.night) for x in a if x.kind == ActKind.CLAIM_CHECK}
    assert (5, 1) in checks
    b = claims_in('昨晚验了8号，是狼人。', day=3)
    assert b[0].night == 3


def test_seats_and_diff() -> None:
    assert seats_in('3号和七号都踩我，9 号呢') == [3, 7, 9]
    plan = [
        SpeechAct(kind=ActKind.CLAIM_ROLE, role=Role.SEER),
        SpeechAct(kind=ActKind.CLAIM_CHECK, target=7, result='wolf', night=1),
        SpeechAct(kind=ActKind.ACCUSE, target=2),
        SpeechAct(kind=ActKind.VOTE_INTENT, target=7),
    ]
    extra, missing = claim_diff('我是预言家，昨晚验了7号，是狼人。2号我也觉得怪。今天我投7号。', plan, day=1)
    assert extra == [] and missing == []
    extra, missing = claim_diff('7号很可疑，我跟你出5号。', plan, day=1)
    assert [(a.kind.value, a.target) for a in extra] == [('vote_intent', 5)]
    assert {a.kind.value for a in missing} == {'claim_role', 'claim_check', 'vote_intent'}


# ---- 实战里 Claude 写的、被核对误拦的说法（真模型试玩里收集）
@pytest.mark.parametrize(
    ('text', 'want'),
    [
        ('我跟你们讲，我是预言家。', {('claim_role', None, 'seer')}),  # 对桌上讲，不是转述
        ('我和你们讲句实话，我是女巫。', {('claim_role', None, 'witch')}),
        ('你们听我说，我是预言家。', {('claim_role', None, 'seer')}),
        ('我是预言家，这话我说过就算数。', {('claim_role', None, 'seer')}),  # 「就算数」的「就算」不是假设
        ('我今天投4号，欢迎指出漏洞，要是有人能拿出新的事实，我会重新看。', {('vote_intent', 4, None)}),
        ('我投1号，你们要是有不同看法，咱们摆到桌面上说。', {('vote_intent', 1, None)}),
        ('我昨天投3号，今天还投3号。', {('vote_intent', 3, None)}),
        ('前面我投了4号，这轮我投5号。', {('vote_intent', 5, None)}),
    ],
)
def test_live_false_blocks_now_read_right(text: str, want: set[tuple]) -> None:
    assert keys(text) == want


@pytest.mark.parametrize(
    'text',
    [
        '话说回来，我也不是随便下结论，前面投4号、投5号，我都是把理由摆出来的。',  # 回顾，过去时管到并列的小句
        '我第1天怀疑的是1号，表态要投1号，站的也是7号的边，结果1号真出局了。',
        '投3号的那几个人我都记着。',  # 定语，不是表态
        '投3号的时候我就觉得不对。',
        '可我站5号，不等于要跟着4号去投6号，这是两回事。',
        '如果3号是狼，我投3号。',  # 假设管到后面的小句
        '就算我是预言家你们也不信。',
        '3号：我是狼人。',  # 学别人说话
        '好，3号：我是预言家，验了7号是狼人。',
        '3号跟你们说他是预言家。',
    ],
)
def test_recaps_and_quotes_yield_nothing(text: str) -> None:
    assert keys(text) == set()


def test_past_marker_does_not_reach_a_fresh_clause() -> None:
    # 真模型试玩：「第2天」说的是对方什么时候踩我，后面以「我」起头的「我投2号」是这次的表态
    assert keys('2号你第2天怀疑我，我有点破防，那个，我怀疑你，我投2号。') == {('vote_intent', 2, None)}
    assert keys('前面我投了4号、投5号，都是有理由的。') == set()
