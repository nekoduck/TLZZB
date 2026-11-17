"""叙述与选项的字符串检查（方案 7.7「按句缓冲，每句过了检查再显示」、2.3、9.7）。

- **称呼。** 内容库里的全部专名（`defs.lexicon` 的专名表与各实体的名字），减去玩家已经知道的
  （`state.known_terms` 里的实体、`state.term_alias` 的叫法），就是黑名单。命中即违规。
  两种名字不进黑名单：名字本身就藏在它自己的叫法里（「水桶」之于「一只水桶」，叫法本来就会说出它）；
  名字出现在内核已经给玩家看过的文字里（称呼表、事实句、地点描述……），玩家已经见过。
- **禁用词。** `defs.lexicon` 的 `banned_modern` 与 `banned_game`。
- **阿拉伯数字、拉丁字母、提示词标记。**
- **长度。** 按句末截断（`truncate`）。

另有给流式中文用的切句器 `SentenceSplitter`：句末是 。！？… 或换行；收尾的引号、括号跟着句子走；
引号里面的句末标点不切。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.perception import PlayerView
from tianlong.kernel.state import WorldState

TERMINALS = '。！？!?…'
CLOSERS = '」』”’）)'
OPENERS = '「『“‘（('
_PAIR_DEPTH_RESET = 160  # 引号一直不收：超过这么长就在句末硬切

_DIGIT_RE = re.compile(r'[0-9０-９]')
_LATIN_RE = re.compile(r'[A-Za-zＡ-Ｚａ-ｚ]')
_MARKUP_RE = re.compile(r'[⟦⟧{}<>＜＞#＃*＊_\\|｜]')
_WS_RE = re.compile(r'\s+')

_CN_DIGITS = str.maketrans('0123456789０１２３４５６７８９', '〇一二三四五六七八九〇一二三四五六七八九')


# ---------------------------------------------------------------- 长度


def text_len(text: str) -> int:
    """字数：不算空白与换行。"""
    return len(_WS_RE.sub('', text or ''))


def ends_sentence(text: str) -> bool:
    t = (text or '').rstrip()
    while t and t[-1] in CLOSERS:
        t = t[:-1]
    return bool(t) and t[-1] in TERMINALS


def close_sentence(text: str) -> str:
    """补上句末的句号（已有句末标点的不动）。"""
    t = (text or '').strip()
    if not t:
        return ''
    if ends_sentence(t):
        return t
    t = t.rstrip('，、；：,;:')
    return t + '。'


def truncate(text: str, max_len: int) -> str:
    """超过上限就在上限之内的最后一个句末截断；一句也放不下时硬截并补句号。"""
    if text_len(text) <= max_len:
        return text
    out: list[str] = []
    n = 0
    for s in split_sentences(text):
        k = text_len(s)
        if n + k > max_len:
            break
        out.append(s)
        n += k
    if out:
        return ''.join(out).rstrip()
    # 第一句就超长：在上限处硬截
    kept: list[str] = []
    n = 0
    for ch in text:
        if not ch.isspace():
            if n >= max_len - 1:
                break
            n += 1
        kept.append(ch)
    return close_sentence(''.join(kept))


def cn_digits(text: str) -> str:
    """把阿拉伯数字逐字换成中文数字（玩家原话里的数字用）。"""
    return (text or '').translate(_CN_DIGITS)


# ---------------------------------------------------------------- 切句


def _scan(text: str, final: bool) -> tuple[list[str], str]:
    """把 text 切成完整的句子与剩下的半句。换行结束的句子末尾带一个 '\\n'。"""
    out: list[str] = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\n':
            j = i
            while j < n and text[j] == '\n':
                j += 1
            if j >= n and not final:
                break  # 换行也许还没完
            seg = text[start:i]
            out.append(seg.strip(' \t\r　') + '\n')
            start = i = j
            depth = 0
            continue
        if ch in OPENERS:
            depth += 1
        elif ch in CLOSERS:
            depth = max(0, depth - 1)
        if ch in TERMINALS:
            j = i + 1
            while j < n and text[j] in TERMINALS:
                j += 1
            d = depth
            while j < n and text[j] in CLOSERS:
                d = max(0, d - 1)
                j += 1
            if j >= n and not final:
                break  # 后面也许还有引号或标点，等下一片
            depth = d
            if depth == 0 or (j - start) > _PAIR_DEPTH_RESET:
                end = j
                brk = ''
                while j < n and text[j] in ' \t\r　\n':
                    if text[j] == '\n':
                        brk = '\n'
                    j += 1
                if j >= n and not final and brk:
                    break  # 段落的换行也许还没完
                seg = text[start:end].strip(' \t\r　')
                if seg:
                    out.append(seg + brk)
                elif brk:
                    out.append(brk)
                start = j
                depth = 0
            i = j
            continue
        i += 1
    rest = text[start:]
    if final:
        if rest.strip():
            out.append(rest.strip(' \t\r　'))
        rest = ''
    return out, rest


def split_sentences(text: str) -> list[str]:
    """一次性切句（不流式）。换行结束的句子末尾带 '\\n'。"""
    out, _ = _scan(text or '', final=True)
    return out


class SentenceSplitter:
    """流式切句：`feed(piece)` 返回已经完整的句子，`flush()` 返回剩下的。"""

    def __init__(self) -> None:
        self._buf = ''

    def feed(self, piece: str) -> list[str]:
        self._buf += piece or ''
        out, self._buf = _scan(self._buf, final=False)
        return out

    def flush(self) -> list[str]:
        out, self._buf = _scan(self._buf, final=True)
        return out

    @property
    def pending(self) -> str:
        return self._buf


# ---------------------------------------------------------------- 检查


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def _name_tokens(name: str) -> list[str]:
    """一个专名要查的字串：全名；带「·」的再加各段。"""
    name = (name or '').strip()
    if not name:
        return []
    out = [name]
    if '·' in name:
        out += [p for p in name.split('·') if p]
    return out


def _entity_names(state: WorldState, defs: Defs) -> dict[str, tuple[str, str]]:
    """全部专名：id → (名字, 叫法)。内容库的专名表、加载时并入的实体，加上状态里的实体与本事。"""
    out: dict[str, tuple[str, str]] = {}
    for eid, t in defs.lexicon.terms.items():
        out[eid] = (t.name, t.alias)
    for eid, t in defs.lexicon.names.items():
        out[eid] = (t.name, t.alias)
    for c in state.chars.values():
        out.setdefault(c.id, (c.name, c.unknown_alias))
    for loc in state.locations.values():
        out.setdefault(loc.id, (loc.name, loc.unknown_alias))
    for it in state.items.values():
        out.setdefault(it.id, (it.name, it.unknown_alias))
    for a in defs.arts.values():
        out.setdefault(a.id, (a.name, a.unknown_alias))
    return out


def view_texts(view: PlayerView | None) -> list[str]:
    """内核已经给玩家看过的文字：称呼表、事实句、只听见的、地点描述、在场的人、身上的感觉。"""
    if view is None:
        return []
    out = [view.location, view.location_desc, *view.names.values(), *view.heard, *view.present,
           *view.items_here, *view.body]
    for ev in view.events:
        out.append(ev.text)
        if ev.actor:
            out.append(ev.actor)
        sp = ev.speech or {}
        for k in ('gist',):
            if sp.get(k):
                out.append(str(sp[k]))
        out += [str(t) for t in sp.get('to') or []]
    return [t for t in out if t]


@dataclass
class Checker:
    """一段叙述（或一组选项）共用的检查器。"""

    blacklist: list[str] = field(default_factory=list)  # 长的在前
    banned: list[str] = field(default_factory=list)
    quoted: list[str] = field(default_factory=list)  # 可以原样出现的字串（玩家自己的原话）

    def check(self, sentence: str) -> list[str]:
        """返回违规清单；空表即通过。"""
        text = sentence or ''
        for q in self.quoted:
            if q:
                text = text.replace(q, '')
        out: list[str] = []
        for name in self.blacklist:
            if name in text:
                out.append(f'name:{name}')
                text = text.replace(name, '')
        low = text.lower()
        for w in self.banned:
            if w and w.lower() in low:
                out.append(f'banned:{w}')
        if _DIGIT_RE.search(text):
            out.append('digit')
        if _LATIN_RE.search(text):
            out.append('latin')
        if _MARKUP_RE.search(text):
            out.append('markup')
        return out

    def ok(self, sentence: str) -> bool:
        return not self.check(sentence)


def blacklist_for(
    state: WorldState,
    defs: Defs,
    *,
    allowed_texts: Iterable[str] = (),
) -> list[str]:
    """称呼黑名单：玩家还不知道的专名。"""
    known = set(state.known_terms) | {state.player}
    allowed: list[str] = [t for t in allowed_texts if t]
    allowed += [v for v in state.term_alias.values() if v]
    names = _entity_names(state, defs)
    for eid in known:
        if eid in names:
            allowed.append(names[eid][0])
    known_names = sorted(
        {names[e][0] for e in known if e in names and len(names[e][0]) >= 2}, key=len, reverse=True
    )
    out: set[str] = set()
    for eid, (name, alias) in names.items():
        if eid in known:
            continue
        for tok in _name_tokens(name):
            if len(tok) < 2:
                continue
            if alias and _subsequence(tok, alias):
                continue  # 叫法本来就说出了它：「一只水桶」之于「水桶」
            if any(tok in a for a in allowed):
                continue
            rest = tok
            for kn in known_names:
                rest = rest.replace(kn, '')
            if rest != tok and (len(rest) <= 2 or (alias and _subsequence(rest, alias))):
                continue  # 已知的专名加一个泛称：「某派」+「弟子」
            out.add(tok)
    return sorted(out, key=lambda s: (-len(s), s))


def banned_words(defs: Defs) -> list[str]:
    seen: list[str] = []
    for w in [*defs.lexicon.banned_modern, *defs.lexicon.banned_game]:
        w = str(w).strip()
        if w and w not in seen:
            seen.append(w)
    return seen


def build_checker(
    state: WorldState,
    defs: Defs,
    view: PlayerView | None = None,
    *,
    allowed_texts: Iterable[str] = (),
    quoted: Iterable[str] = (),
) -> Checker:
    """按此刻的状态与玩家视图建检查器。`quoted` 是可以原样引用的字串（玩家原话）。"""
    allowed = [*view_texts(view), *allowed_texts]
    return Checker(
        blacklist=blacklist_for(state, defs, allowed_texts=allowed),
        banned=banned_words(defs),
        quoted=[q for q in quoted if q],
    )


def check_sentence(
    sentence: str,
    state: WorldState,
    defs: Defs,
    view: PlayerView | None = None,
    *,
    allowed_texts: Iterable[str] = (),
    quoted: Iterable[str] = (),
    checker: Checker | None = None,
) -> list[str]:
    """一句话的字符串检查：称呼黑名单、禁用词、阿拉伯数字、拉丁字母、提示词标记。"""
    ck = checker or build_checker(state, defs, view, allowed_texts=allowed_texts, quoted=quoted)
    return ck.check(sentence)


def player_known_names(state: WorldState, defs: Defs) -> list[str]:
    """玩家知道的名字与特别叫法（给称呼表用）。"""
    out: list[str] = []
    for eid in state.known_terms:
        if eid == state.player:
            continue
        n = refs.display_name(state, defs, eid, state.player)
        if n and n != eid and n not in out:
            out.append(n)
    for v in state.term_alias.values():
        if v and v not in out:
            out.append(v)
    return out


__all__ = [
    'CLOSERS',
    'OPENERS',
    'TERMINALS',
    'Checker',
    'SentenceSplitter',
    'banned_words',
    'blacklist_for',
    'build_checker',
    'check_sentence',
    'close_sentence',
    'cn_digits',
    'ends_sentence',
    'player_known_names',
    'split_sentences',
    'text_len',
    'truncate',
    'view_texts',
]
