"""模板作曲：把 SpeechBrief 写成一段话（ARCHITECTURE 5）。

分层：言语行为句式 x 语气 x 人设口吻（开场、收尾、语气词、口头禅、句长）。声明类行为只用
`canon.py` 认得的说法，所以文字读回来和简报的要点逐条对得上；不写任何要点之外的声明、座位或身份。
排序像说话：先亮身份，再报查验用药，再辩解、站边、指认，最后表态投票。

只见 SpeechBrief；随机性全部来自传入的 rng。为了让同一个 rng 上连续的几段话不重句，
compose 在 rng 对象上记着最近用过的句子（只影响选哪个句式，不影响确定性）。
"""

from __future__ import annotations

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import ActKind, Role, Seat, Tone
from werewolf.mind.types import BriefPoint, SpeechBrief
from werewolf.voice import phrases as P
from werewolf.voice.brief import is_gut
from werewolf.voice.canon import role_word

_RANK = {
    ActKind.CLAIM_ROLE: 0, ActKind.CLAIM_CHECK: 1, ActKind.CLAIM_SAVE: 2, ActKind.CLAIM_POISON: 3,
    ActKind.DEFEND: 4, ActKind.BACK_CLAIM: 5, ActKind.DOUBT_CLAIM: 6, ActKind.ACCUSE: 7, ActKind.VOUCH: 8,
    ActKind.QUESTION: 9, ActKind.VOTE_INTENT: 10, ActKind.PASS: 11,
}
_CLAIMS = {ActKind.CLAIM_ROLE, ActKind.CLAIM_CHECK, ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON, ActKind.VOTE_INTENT}
_LEN = {'short': 0, 'medium': 1, 'long': 2}
_MEM_ATTR = '_ww_voice_used'
_MEM_CAP = 160


class _Ctx:
    def __init__(self, brief: SpeechBrief, rng: Rng, level: int) -> None:
        self.brief = brief
        self.rng = rng
        self.level = level
        self.style = brief.persona.style
        self.tone = _effective_tone(brief)
        self.mods = P.TONE_MODS[self.tone]
        self.group = P.TONE_GROUP[self.tone]
        persona_delta = {'short': -1, 'medium': 0, 'long': 1}[self.style.sentence_len]
        self.verb = max(0, min(2, _LEN[brief.length] + persona_delta)) if level == 0 else 0
        self.decor = level == 0
        self.terse = level >= 2
        self.used: set[str] = set(getattr(rng, _MEM_ATTR, {}))  # 键是去掉句末标点的句子
        self.out: list[tuple[str, bool]] = []  # (句子, 是否含声明)

    def chance(self, p: float) -> bool:
        return self.rng.chance(p)

    def choose(self, make) -> str:  # noqa: ANN001
        """试几次，取第一个没用过的；都用过了就取最后一个。"""
        s = ''
        for _ in range(30):
            s = make()
            if _key(s) not in self.used:
                break
        self.used.add(_key(s))
        return s

    def add(self, text: str, claim: bool = False) -> None:
        self.out.append((text, claim))


def _key(s: str) -> str:
    return s.rstrip('。！？')


def _effective_tone(brief: SpeechBrief) -> Tone:
    if brief.tone != Tone.CALM:
        return brief.tone
    return {'tilted': Tone.ANGRY, 'tense': Tone.NERVOUS, 'confident': Tone.FIRM}.get(brief.mood.state, Tone.CALM)


def _night_phrase(night: int | None, day: int) -> str:
    if night is None:
        return ''
    if night == day:
        return '昨晚'
    if night == day - 1 and day > 1:
        return '前晚'
    return f'第{night}晚'


def _seat(s: Seat | None) -> str:
    return f'{s}号'


# --------------------------------------------------------------------------- 单句


def _claim_core(ctx: _Ctx, pt: BriefPoint) -> str | None:
    a = pt.act
    k = a.kind
    t = _seat(a.target) if a.target is not None else ''
    n = _night_phrase(a.night, ctx.brief.day)
    r = ctx.rng
    if k == ActKind.CLAIM_ROLE:
        if a.role is None:
            return None
        w = role_word(a.role)
        if ctx.terse:
            return r.choice(P.TERSE[k]).format(w=w)
        pool = list(P.CLAIM_ROLE_PATTERNS)
        if a.role == Role.VILLAGER:
            pool += P.CLAIM_ROLE_VILLAGER * 2
        if a.weight >= 3 or ctx.group == 'firm':
            pool += P.CLAIM_ROLE_FIRM
        return r.choice(pool).format(w=w)
    if a.target is None:
        return None
    if k == ActKind.CLAIM_CHECK:
        if a.result is None:
            return None
        if ctx.terse:
            return r.choice(P.TERSE[k]).format(t=t, r='狼人' if a.result == 'wolf' else '好人')
        pool = P.CHECK_WOLF if a.result == 'wolf' else P.CHECK_GOOD
        return r.choice(pool).format(t=t, n=n)
    if k == ActKind.CLAIM_SAVE:
        return r.choice(P.TERSE[k] if ctx.terse else P.SAVE_PATTERNS).format(t=t, n=n)
    if k == ActKind.CLAIM_POISON:
        if ctx.terse:
            return r.choice(P.TERSE[k]).format(t=t)
        pool = list(P.POISON_PATTERNS)
        if n in ('', '昨晚'):
            pool.append(P.POISON_BA)
        return r.choice(pool).format(t=t, n=n)
    if k == ActKind.VOTE_INTENT:
        if ctx.terse:
            return r.choice(P.TERSE[k]).format(t=t)
        pool = list(P.VOTE_PATTERNS)
        if a.weight >= 3 or ctx.group == 'firm':
            pool += P.VOTE_FIRM
        return r.choice(pool).format(t=t)
    return None


def _soft_core(ctx: _Ctx, pt: BriefPoint) -> str | None:
    a = pt.act
    if a.kind == ActKind.DEFEND and a.target is not None:
        bank = P.DEFEND_OTHER
    else:
        bank = P.ACT_PATTERNS.get(a.kind)
    if not bank or (a.kind != ActKind.DEFEND and a.target is None):
        return None
    if pt.hedge:
        reg = 'soft'
    elif a.weight <= 1 and 'low' in bank:
        reg = 'low'
    elif a.weight >= 3:
        reg = 'firm'
    else:
        reg = ctx.group
    pool = bank.get(reg) or bank['plain']
    return ctx.rng.choice(pool).format(t=_seat(a.target) if a.target is not None else '')


def _end(ctx: _Ctx, pt: BriefPoint | None) -> str:
    if pt is not None and pt.act.kind == ActKind.QUESTION:
        return '！' if ctx.group == 'firm' else '？'
    return ctx.rng.choice(ctx.mods['end'])


def _decorate(ctx: _Ctx, core: str, pt: BriefPoint | None, claim: bool, first: bool) -> str:
    """加语气前后缀、人设语气词、句末标点。"""
    prefix = ''
    suffix = ''
    if ctx.decor:
        p_pre = (0.2, 0.35, 0.5)[ctx.verb]
        p_suf = (0.1, 0.25, 0.4)[ctx.verb]
        pre_pool = ctx.mods['cprefix' if claim else 'prefix']
        suf_pool = ctx.mods['csuffix' if claim else 'suffix']
        if ctx.chance(p_pre):
            prefix = ctx.rng.choice(pre_pool)
        elif ctx.style.fillers and ctx.chance(0.45):
            prefix = ctx.rng.choice(ctx.style.fillers) + '，'
        if ctx.chance(p_suf):
            suffix = ctx.rng.choice(suf_pool)
        if not first and not prefix and ctx.verb >= 1 and ctx.chance(0.3):
            prefix = ctx.rng.choice(P.CLAIM_CONNECTORS if claim else P.CONNECTORS)
    return f'{prefix}{core}{suffix}{_end(ctx, pt)}'


def _reason_sentence(ctx: _Ctx, pt: BriefPoint, inline_core: str | None) -> str | None:
    """理由。返回独立的一句；或在 inline_core 给定时返回「核心，理由」合成的一句（不含句末标点）。"""
    if not pt.reason:
        return None
    rs = pt.reason
    if is_gut(rs):
        pool = P.REASON_GUT
    elif pt.hedge:
        pool = P.REASON_HEDGED_SELF if rs.startswith('我') else P.REASON_HEDGED
    elif inline_core is not None:
        pool = P.REASON_INLINE
    else:
        pool = P.REASON_FRAMES
    body = ctx.rng.choice(pool).format(r=rs)
    return f'{inline_core}，{body}' if inline_core is not None else body


# --------------------------------------------------------------------------- 整段


def _opener(ctx: _Ctx) -> str | None:
    b = ctx.brief
    st = ctx.style
    p = (0.25, 0.5, 0.7)[ctx.verb]
    if not ctx.decor or not ctx.chance(p):
        return None
    sit = b.situation
    if b.kind == 'pk':
        generic = P.OPENERS['pk']
    elif b.kind == 'last_words':
        generic = list(P.OPENERS['last_words'])
        if sit.died:
            generic += P.OPENERS[f'died_{sit.died}']
    elif sit.total > 1 and sit.index == 0:
        generic = P.OPENERS['first']
    elif sit.total > 1 and sit.index == sit.total - 1:
        generic = P.OPENERS['last']
    else:
        generic = P.OPENERS['mid']
    pool = st.openers if st.openers and ctx.chance(0.6) else generic
    return ctx.choose(lambda: ctx.rng.choice(pool) + '。')


def _closer(ctx: _Ctx, force: bool = False) -> str | None:
    st = ctx.style
    p = (0.25, 0.5, 0.75)[ctx.verb]
    if not ctx.decor or not (force or ctx.chance(p)):
        return None
    generic = list(P.CLOSERS['pk' if ctx.brief.kind == 'pk' else 'last_words' if ctx.brief.kind == 'last_words' else 'day'])
    if ctx.verb == 2:
        generic += P.CLOSERS['long']
    pool = st.closers if st.closers and ctx.chance(0.6) else generic
    return ctx.choose(lambda: ctx.rng.choice(pool) + '。')


def _pass_line(ctx: _Ctx) -> str:
    k = ctx.brief.kind
    if k == 'pk':
        pool = P.PASS_PK
    elif k == 'last_words':
        pool = P.PASS_LAST
    else:
        pool = P.PASS_PATTERNS.get(ctx.group) or P.PASS_PATTERNS['plain']
        if ctx.chance(0.3):
            pool = pool + P.PASS_PATTERNS['plain']
    return ctx.choose(lambda: _decorate(ctx, ctx.rng.choice(pool), None, False, True))


def _build(brief: SpeechBrief, rng: Rng, level: int) -> list[tuple[str, bool]]:
    ctx = _Ctx(brief, rng, level)
    pts = sorted(enumerate(brief.points), key=lambda ip: (_RANK.get(ip[1].act.kind, 99), ip[0]))
    pts = [pt for _, pt in pts]
    if level >= 3:
        pts = [pt for pt in pts if pt.act.kind in _CLAIMS]
    acts = [pt for pt in pts if pt.act.kind != ActKind.PASS]

    opener = _opener(ctx)
    if opener:
        ctx.add(opener)
    # 回应被点名的人（简报里的 addressed 都是公开信息）
    if ctx.decor and brief.addressed and ctx.verb >= 1 and acts and ctx.chance(0.4):
        a_txt = '、'.join(_seat(s) for s in brief.addressed)
        ctx.add(ctx.choose(lambda: ctx.rng.choice(P.ADDRESS_PATTERNS).format(a=a_txt) + ctx.rng.choice(['。', '。', '！'])))

    first = not opener
    rank = 0
    said: set[str] = set()  # 已经说过的理由：一段话里同一个理由只讲一遍
    for pt in acts:
        claim = pt.act.kind in _CLAIMS
        if claim:
            core = _claim_core(ctx, pt)
        else:
            core = _soft_core(ctx, pt)
        if core is None:
            continue
        with_reason = bool(
            ctx.decor and pt.reason and pt.reason not in said
            and (ctx.verb == 2 or (ctx.verb == 1 and rank < 2) or rank < 1)
        )
        if with_reason and pt.reason:
            said.add(pt.reason)
        rank += 1
        if with_reason and not claim and pt.act.kind != ActKind.QUESTION and ctx.chance(0.4):
            def make_inline(core=core, pt=pt, claim=claim, first=first) -> str:  # noqa: ANN001
                c = _soft_core(ctx, pt) or core
                body = _reason_sentence(ctx, pt, c)
                return _decorate(ctx, body or c, pt, claim, first)
            ctx.add(ctx.choose(make_inline))
            first = False
            continue

        def make(core=core, pt=pt, claim=claim, first=first) -> str:  # noqa: ANN001
            c = (_claim_core(ctx, pt) if claim else _soft_core(ctx, pt)) or core
            return _decorate(ctx, c, pt, claim, first)

        ctx.add(ctx.choose(make), claim)
        first = False
        if with_reason:
            rs = _reason_sentence(ctx, pt, None)
            if rs:
                ctx.add(ctx.choose(lambda rs=rs: rs + '。'))

    if not acts:
        ctx.add(_pass_line(ctx))

    # 口头禅与收尾
    flair = bool(opener)
    cps = ctx.style.catchphrases
    if ctx.decor and cps and ctx.chance((0.15, 0.3, 0.4)[ctx.verb]):
        ctx.add(ctx.choose(lambda: ctx.rng.choice(cps) + '。'))
        flair = True
    closer = _closer(ctx, force=not flair and (ctx.verb >= 1 or ctx.chance(0.5)))
    if closer:
        ctx.add(closer)
    return ctx.out


def _remember(rng: Rng, sentences: list[str]) -> None:
    mem: dict[str, None] = getattr(rng, _MEM_ATTR, {})
    for s in sentences:
        mem.pop(_key(s), None)
        mem[_key(s)] = None
    while len(mem) > _MEM_CAP:
        mem.pop(next(iter(mem)))
    setattr(rng, _MEM_ATTR, mem)


def compose(brief: SpeechBrief, rng: Rng) -> str:
    """把简报写成一段话；不超过 brief.max_chars。"""
    chosen: list[tuple[str, bool]] = []
    for level in range(4):
        chosen = _build(brief, rng, level)
        if sum(len(s) for s, _ in chosen) <= brief.max_chars:
            break
    else:
        # 还是太长：从后往前丢不含声明的句子，最后才丢声明句
        while len(chosen) > 1 and sum(len(s) for s, _ in chosen) > brief.max_chars:
            idx = next((i for i in range(len(chosen) - 1, -1, -1) if not chosen[i][1]), len(chosen) - 1)
            chosen.pop(idx)
    text = ''.join(s for s, _ in chosen)
    if len(text) > brief.max_chars:
        text = text[: brief.max_chars]
    _remember(rng, [s for s, _ in chosen])
    return text


def candidates(brief: SpeechBrief, rng: Rng, n: int = 6) -> list[str]:
    """n 段措辞不同的备选，供 Jev 选句。"""
    out: list[str] = []
    for _ in range(n * 4):
        t = compose(brief, rng)
        if t and t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out
