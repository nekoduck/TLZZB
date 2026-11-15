"""法官台词牌库。全部原创，主流狼人杀法官的口吻，简短。

纪律：牌库是固定的，不看局面；台词里不出现座位号（只能经占位符传入）、不说谁是什么身份、
不说夜里谁做了什么。夜间与技能环节的台词每次都照念，与该身份死活无关。

占位符：`{day}`、`{seats}`、`{seat}`。用法是把值作为关键字参数传给 `line`：

    line('dawn_deaths', t, rng, seats=[3, 7])        # → 「天亮了。昨夜倒下的是 3号、7号。」之类
    line('speech_start', t, rng, day=2, seat=5)

`seats` 可以是字符串（原样）或座位序列（渲染成「3号、7号」）；`seat` 可以是整数或字符串。
缺了的占位符不会报错：`第{day}天` 缺 day 时变成「今天」，`{seats}` 缺时是「有玩家」，`{seat}` 缺时是「这位玩家」。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, get_args

from werewolf.kernel.rng import Rng
from werewolf.protocol import NarrationKey

HIGH = 0.5  # 紧张度到这里，才可能抽到气氛更重的那几句

# 每个 key 一组 (台词, 最低紧张度)。最低紧张度为 0 的是常规句，每个 key 至少 4 句。
DECK: dict[str, list[tuple[str, float]]] = {
    'deal': [
        ('身份牌已经发到每个人手里，请悄悄看一眼，别让旁人瞧见。', 0),
        ('发牌完毕。看清自己的身份，记在心里。', 0),
        ('每人一张牌，各自查看，不要声张。', 0),
        ('牌已发好。今晚之后，谁也别轻信谁。', 0),
        ('请确认你的身份。游戏马上开始。', 0),
    ],
    'night_fall': [
        ('天黑请闭眼。', 0),
        ('入夜了，所有人请闭眼。', 0),
        ('天黑了，请大家闭上眼睛。', 0),
        ('夜幕落下，请闭眼。', 0),
        ('第{day}夜，天黑请闭眼。', 0),
        ('夜色更沉了。天黑，请闭眼。', HIGH),
        ('又一个难熬的夜晚。所有人，闭眼。', HIGH),
        ('村子安静下来，只剩风声。天黑请闭眼。', HIGH),
    ],
    'wolves_wake': [
        ('狼人请睁眼。', 0),
        ('狼人请睁眼，互相确认同伴。', 0),
        ('狼人请睁眼，请选择今晚的目标。', 0),
        ('狼人请睁眼，商量一下今晚的去向。', 0),
        ('狼人请睁眼。今夜，你们想动谁？', HIGH),
        ('狼人请睁眼，时间不多了，尽快决定。', HIGH),
    ],
    'wolves_sleep': [
        ('狼人请闭眼。', 0),
        ('好的，狼人请闭眼。', 0),
        ('狼人已决定，请闭眼。', 0),
        ('狼人请闭上眼睛。', 0),
        ('狼人请闭眼，夜还没有结束。', HIGH),
    ],
    'seer_wake': [
        ('预言家请睁眼。', 0),
        ('预言家请睁眼，你要查验谁？', 0),
        ('预言家请睁眼，请选择要查验的玩家。', 0),
        ('预言家请睁眼，今晚想看清哪一位？', 0),
        ('预言家请睁眼。真相藏在谁身上？', HIGH),
    ],
    'seer_sleep': [
        ('预言家请闭眼。', 0),
        ('好的，预言家请闭眼。', 0),
        ('预言家请闭上眼睛。', 0),
        ('预言家，请闭眼。', 0),
        ('预言家请闭眼，记住你看到的。', HIGH),
    ],
    'witch_wake': [
        ('女巫请睁眼。', 0),
        ('女巫请睁眼，你要用药吗？', 0),
        ('女巫请睁眼，解药和毒药，今晚怎么安排？', 0),
        ('女巫请睁眼，请决定是否使用药水。', 0),
        ('女巫请睁眼。这一夜，要不要出手？', HIGH),
    ],
    'witch_sleep': [
        ('女巫请闭眼。', 0),
        ('好的，女巫请闭眼。', 0),
        ('女巫请闭上眼睛。', 0),
        ('女巫，请闭眼。', 0),
        ('女巫请闭眼，天快亮了。', HIGH),
    ],
    'dawn_peaceful': [
        ('天亮了。昨晚是平安夜。', 0),
        ('天亮请睁眼。昨夜无人出局。', 0),
        ('天亮了，大家都还在。昨晚平安。', 0),
        ('天亮请睁眼，昨晚是个平安夜。', 0),
        ('天亮了。昨夜平安——可这份平安意味着什么？', HIGH),
        ('天亮请睁眼。没有人倒下，但别松懈。', HIGH),
    ],
    'dawn_deaths': [
        ('天亮了。昨晚出局的是：{seats}。', 0),
        ('天亮请睁眼。昨夜，{seats}倒下了。', 0),
        ('天亮了，{seats}没能等到天亮。', 0),
        ('天亮请睁眼。昨晚死亡的玩家是 {seats}。', 0),
        ('天亮了。又有人离开了我们：{seats}。', HIGH),
        ('天亮请睁眼。昨夜的代价是 {seats}。', HIGH),
    ],
    'skill': [
        ('技能环节，有技能可以发动的玩家请发动。', 0),
        ('现在是技能环节。', 0),
        ('进入技能环节，请稍候。', 0),
        ('技能环节开始，能发动的请发动。', 0),
        ('技能环节。所有人屏住呼吸。', HIGH),
    ],
    'last_words': [
        ('请{seats}发表遗言。', 0),
        ('{seats}，请留下你的遗言。', 0),
        ('遗言环节：{seats}，请讲。', 0),
        ('{seats}，还有什么要交代的吗？请说遗言。', 0),
        ('{seats}，这是你最后说话的机会了。', HIGH),
    ],
    'speech_start': [
        ('第{day}天，请按顺序发言。', 0),
        ('开始发言，从{seat}起。', 0),
        ('白天发言环节，从{seat}开始。', 0),
        ('请大家依次发言，{seat}先说。', 0),
        ('第{day}天，局势不明朗，请{seat}先发言。', HIGH),
        ('真话假话都在今天说完。{seat}，请开始。', HIGH),
    ],
    'vote_start': [
        ('发言结束，请投票。', 0),
        ('现在开始投票，请选择你要放逐的玩家。', 0),
        ('请投出你的一票。', 0),
        ('投票环节开始。', 0),
        ('该做决定了。请投票。', HIGH),
        ('这一票可能决定胜负，请慎重。', HIGH),
    ],
    'vote_result': [
        ('投票结果如下。', 0),
        ('票已收齐，公布结果。', 0),
        ('投票结束，来看看票型。', 0),
        ('所有人都投完了，公布票型。', 0),
        ('票已收齐。结果是……', HIGH),
    ],
    'pk_start': [
        ('出现平票，{seats}上 PK 台发言。', 0),
        ('票数相同，请{seats}依次进行 PK 发言。', 0),
        ('平票。{seats}，请做最后的陈述。', 0),
        ('进入 PK 环节：{seats}。', 0),
        ('平票！{seats}，说服大家吧。', HIGH),
    ],
    'pk_vote': [
        ('PK 发言结束，请再次投票。', 0),
        ('请在台上的玩家中投出一票。', 0),
        ('第二轮投票开始。', 0),
        ('PK 投票，请选择。', 0),
        ('最后一轮投票，没有回头路了。', HIGH),
    ],
    'exile': [
        ('{seat}被放逐。', 0),
        ('{seat}出局。', 0),
        ('根据投票结果，{seat}离场。', 0),
        ('{seat}被大家投了出去。', 0),
        ('{seat}被放逐了。这一票，投对了吗？', HIGH),
    ],
    'no_exile': [
        ('今天无人出局。', 0),
        ('没有人被放逐。', 0),
        ('本轮投票无人出局。', 0),
        ('今天没能放逐任何人。', 0),
        ('无人出局。天又要黑了。', HIGH),
    ],
    'hunter_shot': [
        ('猎人开枪，带走了{seat}。', 0),
        ('枪响了，{seat}倒下。', 0),
        ('猎人发动技能，{seat}出局。', 0),
        ('一声枪响，{seat}被带走。', 0),
        ('猎人扣下扳机——{seat}应声倒地。', HIGH),
    ],
    'game_over': [
        ('游戏结束。', 0),
        ('胜负已分，游戏结束。', 0),
        ('游戏到此结束，翻开所有身份牌。', 0),
        ('本局结束，来看看每个人的身份。', 0),
        ('尘埃落定。游戏结束。', HIGH),
    ],
}

KEYS: tuple[str, ...] = get_args(NarrationKey)

_PLACEHOLDER = re.compile(r'\{(\w+)\}')
_DAY_PHRASE = re.compile(r'第\{day\}([天夜])')
_FALLBACK: dict[str, str] = {'seats': '有玩家', 'seat': '这位玩家', 'day': ''}


def _seat_text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f'{value}号'
    return str(value)


def _value(name: str, value: Any) -> str:
    if name == 'seats' and isinstance(value, Sequence) and not isinstance(value, str):
        return '、'.join(_seat_text(v) for v in value)
    if name in ('seats', 'seat'):
        return _seat_text(value)
    return str(value)


def render(text: str, **fmt: Any) -> str:
    """填占位符。缺的有安全的退路（见模块说明），不认识的占位符去掉，多给的参数忽略，从不报错。"""
    given = {k: _value(k, v) for k, v in fmt.items() if v is not None}
    if 'day' not in given:
        text = _DAY_PHRASE.sub(lambda m: '今天' if m.group(1) == '天' else '今夜', text)
    return _PLACEHOLDER.sub(lambda m: given.get(m.group(1), _FALLBACK.get(m.group(1), '')), text)


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def lines(key: str, tension: float = 0.0) -> list[str]:
    """该 key 在这个紧张度下可以抽到的台词模板（未填占位符）。"""
    return [t for t, lo in DECK.get(key, []) if tension >= lo]


def line(key: NarrationKey, tension: float, rng: Rng, avoid: Sequence[str] = (), **fmt: Any) -> str:
    """抽一句法官台词并填好占位符。

    - 紧张度 ≥ 0.5 时才可能抽到气氛更重的句子；
    - 不返回 `avoid` 里的句子（按模板或填好后的文字比较），除非整副牌都在里面；
    - 优先抽占位符都给齐了的句子；
    - 未知的 key 返回空串。
    """
    deck = DECK.get(key)
    if not deck:
        return ''
    avoided = set(avoid)
    eligible = lines(key, tension)
    everything = [t for t, _ in deck]

    def fresh(pool: list[str]) -> list[str]:
        return [t for t in pool if t not in avoided and render(t, **fmt) not in avoided]

    pool = fresh(eligible) or fresh(everything) or eligible or everything
    given = {k for k, v in fmt.items() if v is not None}
    complete = [t for t in pool if placeholders(t) <= given]
    return render(rng.choice(complete or pool), **fmt)
