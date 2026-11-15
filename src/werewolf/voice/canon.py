"""规范说法：声明类言语行为在文字里的固定样子（ARCHITECTURE 5、6.2）。

模板（`voice/compose`）写声明时必须用这里认得的说法；正则解析（`llm/parse`）和台词核对
（`llm/check`）用同一套规则把它们读回来。所以「文字说了什么」与「计划里的 acts」能逐条对上。

只认五种声明类行为：CLAIM_ROLE、CLAIM_CHECK、CLAIM_SAVE、CLAIM_POISON、VOTE_INTENT。
宁可漏，不可错：否定句、假设句、疑问句、转述别人的话，一律不算声明。

认得的说法（T 是座位号，阿拉伯数字或汉字数字加「号」）：
- 自称身份：「我是预言家」「我才是女巫」「我就是猎人」「我是一张平民牌」「我的身份是预言家」
- 报查验：「我验了T号，是狼人」「昨晚验的T号，好人」「T号是我的查杀」「T号是我的金水」
          「我查杀T号」「我给T号发金水」
- 报用药：「我救了T号」「T号是我的银水」「我毒了T号」「我把T号毒了」
- 表态投票：「我投T号」「今天出T号」「这票挂T号」「归票T号」「跟我出T号」
"""

from __future__ import annotations

import re

from werewolf.kernel.types import ActKind, Role, Seat, SpeechAct

CN_NUM: dict[str, int] = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_NUM = r'[1-9一二三四五六七八九]'
SEAT_RE = re.compile(rf'({_NUM})\s*号')
_T = rf'(?P<t>{_NUM})\s*号'

ROLE_WORDS: dict[Role, tuple[str, ...]] = {
    Role.SEER: ('预言家',),
    Role.WITCH: ('女巫',),
    Role.HUNTER: ('猎人',),
    Role.VILLAGER: ('平民', '村民', '白板', '民牌'),
    Role.WEREWOLF: ('狼人',),
}
_ROLE_ALT = '|'.join(w for words in ROLE_WORDS.values() for w in words)
_WORD_ROLE: dict[str, Role] = {w: r for r, words in ROLE_WORDS.items() for w in words}

_SENT_SPLIT = re.compile(r'[。！？!?；;\n]+')
_CLAUSE_SPLIT = re.compile(r'[，,、]+')

# 这些出现在一句里，这句就不算声明
_HYPO = re.compile(r'如果|要是|假如|假设|假使|万一|就算|哪怕|即使|倘若|除非')
_QUESTION = re.compile(r'吗|是不是|难道|会不会|有没有|到底是|谁是|是谁|怎么可能|凭什么|[?？]')
# 转述：别人做主语的「说、称、跳」
_REPORTED = re.compile(rf'(?:{_NUM}\s*号|他|她|你|你们|他们|有人|别人|对面|对方|谁)(?:[^，,。]{{0,6}})?(?:说|讲|称|自称|声称|表示|喊|跳|起跳|报|认为|觉得)')
# 别人做主语的动作（验、救、毒、投）
_OTHER_SUBJ = rf'(?:{_NUM}\s*号|他|她|你|你们|他们|有人|别人|对面|对方|谁|大家|狼|狼人|狼队|预言家|女巫|猎人)'

_CLAIM_ROLE = re.compile(
    rf'(?<![不没])我(?:的身份)?(?:才|就|真的|真|确实|其实|可|明牌|这把|这局)*(?:是|拿的是|拿了|摸的是|摸到)'
    rf'(?:一张|个|一个|张|真|真的|这局的|唯一的)*(?P<role>{_ROLE_ALT})(?:牌)?(?!的|们|阵营|队)'
)
_NEG_ROLE = re.compile(rf'我(?:才|可|真|并|又|也)?(?:不是|没说(?:过)?我是|没跳|不可能是)[^，,。]{{0,4}}(?:{_ROLE_ALT})')

_RESULT = r'(?P<res>狼人|狼|查杀|好人|金水|好的)'
_CHECK_VERB = re.compile(
    rf'(?P<pre>[^，,。]{{0,12}}?)(?:验|查验|摸)(?:了|的是|的|过|到)?\s*{_T}\s*[，,：:\s]*'
    rf'(?:他|她|结果|这张牌|身份|结果是)?\s*(?:是|为|就是|是个|是张)?\s*(?:个|一个|一张|我的)?\s*{_RESULT}'
)
_CHECK_MINE = re.compile(rf'{_T}\s*(?:就)?是我(?:的|发的|给的|验出来的|验出的)(?P<res>查杀|金水)')
_CHECK_I = re.compile(rf'我(?:这边|今天|昨晚|昨夜|现在)?(?:就)?(?:给)?\s*(?P<res>查杀|金水)\s*(?:的是|是|了)?\s*{_T}')
_CHECK_GIVE = re.compile(rf'我(?:昨晚|昨夜|今天)?给\s*{_T}\s*(?:发|的是|发了|发的是)(?:了)?(?:一个|个|一张)?(?P<res>金水|查杀)')

_SAVE = re.compile(rf'(?P<pre>[^，,。]{{0,10}}?)(?:救了|救的是|救的|救过|解药(?:给|用在)(?:了)?)\s*{_T}')
_SAVE_MINE = re.compile(rf'{_T}\s*(?:就)?是我(?:的|救的|开的)?银水')
_POISON = re.compile(rf'(?P<pre>[^，,。]{{0,10}}?)(?:毒了|毒的是|毒的|毒死了|毒药(?:给|用在|撒在)(?:了)?)\s*{_T}')
_POISON_BA = re.compile(rf'我(?:昨晚|昨夜|晚上)?把\s*{_T}\s*(?:给)?毒(?:了|死了|掉了)')

_VOTE = re.compile(
    rf'(?P<pre>[^，,。]{{0,10}}?)(?:投|出|归票|归|挂|票给|票挂|票)(?P<past>了|过)?\s*{_T}(?!\s*(?:的票|那票|一票))'
)
_VOTE_NEG = re.compile(r'(?:不|别|不要|不会|不想|没|没有|先不|未必|不一定|不能|怎么|为什么|凭什么|干嘛)\s*(?:再|就|去|要|会|该|能)?\s*$')
_VOTE_PAST = re.compile(r'(?:昨天|昨日|上一轮|上轮|第[一二三四五六七八九\d]天|刚才|之前|当时|前面)')

_NIGHT_ORD = re.compile(r'第\s*([1-9一二三四五六七八九])\s*(?:晚|夜|个晚上|天晚上|天夜里)')
_NIGHT_FIRST = re.compile(r'首夜|头一晚|头夜|第一个晚上')
_NIGHT_PREV = re.compile(r'前天晚上|前天夜里|前晚|前夜|上上晚')


def seat_num(token: str) -> Seat:
    return CN_NUM.get(token) or int(token)


def seats_in(text: str) -> list[Seat]:
    """文字里提到的座位号，按出现的先后，去重。"""
    out: list[Seat] = []
    for m in SEAT_RE.finditer(text):
        s = seat_num(m.group(1))
        if s not in out:
            out.append(s)
    return out


def role_word(role: Role) -> str:
    return ROLE_WORDS[role][0]


def _night_of(sentence: str, day: int | None) -> int | None:
    m = _NIGHT_ORD.search(sentence)
    if m:
        return seat_num(m.group(1))
    if _NIGHT_FIRST.search(sentence):
        return 1
    if _NIGHT_PREV.search(sentence) and day:
        return max(1, day - 1)
    return day  # 没说哪一夜，按最近的一夜


def _result(word: str) -> str:
    return 'wolf' if word in ('狼人', '狼', '查杀') else 'good'


_WITH = re.compile(rf'(?:跟|和|同|随|陪|与|听)(?:着)?(?:{_NUM}\s*号|你们|你|他们|他|她|大家)(?:一起|走|的)?')


def _other_subject(pre: str) -> bool:
    """动词前面这一小段里，做主语的是不是别人。「我」在最后出现则不是；「跟你」「和 3 号一起」不算主语。"""
    pre = _WITH.sub('', pre).strip()
    if not pre:
        return False
    last_me = pre.rfind('我')
    last_other = -1
    for m in re.finditer(_OTHER_SUBJ, pre):
        last_other = m.start()
    return last_other > last_me


def claims_in(text: str, day: int | None = None) -> list[SpeechAct]:
    """从文字里读出声明类言语行为。`day` 是说话时的天数，用来把「昨晚」换成夜数。

    同一（种类，目标）只留第一条；CLAIM_ROLE 只留第一条。
    """
    acts: list[SpeechAct] = []
    seen: set[tuple[str, int | None]] = set()

    def add(kind: ActKind, **kw: object) -> None:
        key = (kind.value, kw.get('target'))  # type: ignore[arg-type]
        if kind == ActKind.CLAIM_ROLE:
            key = (kind.value, None)
        if key in seen:
            return
        seen.add(key)  # type: ignore[arg-type]
        acts.append(SpeechAct(kind=kind, **kw))  # type: ignore[arg-type]

    for sentence in _SENT_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        hypo_or_q = bool(_HYPO.search(sentence)) or bool(_QUESTION.search(sentence))
        reported = bool(_REPORTED.search(sentence))
        night = _night_of(sentence, day)

        # ---- 自称身份
        if not hypo_or_q and not reported and not _NEG_ROLE.search(sentence):
            m = _CLAIM_ROLE.search(sentence)
            if m:
                add(ActKind.CLAIM_ROLE, role=_WORD_ROLE[m.group('role')], weight=3)

        if hypo_or_q:
            continue

        # ---- 报查验
        for m in _CHECK_VERB.finditer(sentence):
            if reported or _other_subject(m.group('pre')):
                continue
            add(ActKind.CLAIM_CHECK, target=seat_num(m.group('t')), result=_result(m.group('res')), night=night)
        if not reported:
            for rx in (_CHECK_MINE, _CHECK_I, _CHECK_GIVE):
                for m in rx.finditer(sentence):
                    add(ActKind.CLAIM_CHECK, target=seat_num(m.group('t')), result=_result(m.group('res')),
                        night=night)

        # ---- 报用药
        for m in _SAVE.finditer(sentence):
            if reported or _other_subject(m.group('pre')) or '我' not in m.group('pre'):
                continue
            add(ActKind.CLAIM_SAVE, target=seat_num(m.group('t')), night=night)
        if not reported:
            for m in _SAVE_MINE.finditer(sentence):
                add(ActKind.CLAIM_SAVE, target=seat_num(m.group('t')), night=night)
        for m in _POISON.finditer(sentence):
            if reported or _other_subject(m.group('pre')) or '我' not in m.group('pre'):
                continue
            add(ActKind.CLAIM_POISON, target=seat_num(m.group('t')), night=night)
        if not reported:
            for m in _POISON_BA.finditer(sentence):
                add(ActKind.CLAIM_POISON, target=seat_num(m.group('t')), night=night)

        # ---- 表态投票（逐个小句看，免得前半句的否定管到后半句）
        for clause in _CLAUSE_SPLIT.split(sentence):
            for m in _VOTE.finditer(clause):
                pre = m.group('pre')
                if m.group('past') or _VOTE_PAST.search(clause[: m.start('t')]):
                    continue  # 说的是过去投过谁
                if _VOTE_NEG.search(pre) or _other_subject(pre) or _REPORTED.search(clause):
                    continue
                add(ActKind.VOTE_INTENT, target=seat_num(m.group('t')))
    return acts


CLAIM_KINDS: frozenset[ActKind] = frozenset(
    {ActKind.CLAIM_ROLE, ActKind.CLAIM_CHECK, ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON, ActKind.VOTE_INTENT}
)


def claim_key(act: SpeechAct) -> tuple[str, int | None, str | None]:
    """比较两条声明是不是同一件事：种类、目标、（身份或查验结果）。夜数不比。"""
    extra = act.role.value if act.kind == ActKind.CLAIM_ROLE and act.role else act.result
    return (act.kind.value, act.target, extra)


def claim_diff(text: str, planned: list[SpeechAct], day: int | None = None) -> tuple[list[SpeechAct], list[SpeechAct]]:
    """文字与计划对账。返回（文字里多出来的声明，计划里有而文字里没读到的声明）。"""
    found = {claim_key(a): a for a in claims_in(text, day)}
    want = {claim_key(a): a for a in planned if a.kind in CLAIM_KINDS}
    extra = [a for k, a in found.items() if k not in want]
    missing = [a for k, a in want.items() if k not in found]
    return extra, missing
