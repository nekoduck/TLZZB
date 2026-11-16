"""规则猜测对事实的办法：话里点到事实里的人或物，再加一个对得上的「关系词类」或实在的字词。

关系词类是一张近义词表（prompts/jev/j1_relations.md）：「私下相好」与「有私情」算同一类。
这里只做字面上的对照，给 MockJev 出提示、给 C1 的 Mock 出默认值、在 ``rules_only`` 时兜底；
真模型在时不靠它做决定。表里只有通用的汉语词（方案 10.6）。
"""

from __future__ import annotations

import re
from functools import lru_cache

from tianlong import prompting
from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.parser import lexical as lx

STOP_BIGRAMS = {"一个", "不是", "知道", "我们", "你们", "他们", "这个", "那个", "什么", "没有", "自己", "一点",
                "起来", "出来", "下来", "上来", "这里", "那里", "怎么", "可是", "就是", "还是", "已经"}
BELIEVE = 0.6


@lru_cache(maxsize=4)
def _classes_cached(root: str) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    if not prompting.exists("jev/j1_relations"):
        return out
    for line in prompting.load("jev/j1_relations").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.]+)\s*[：:]\s*(.+)$", line)
        if m:
            words = tuple(w.strip() for w in re.split(r"[、,，\s]+", m.group(2)) if w.strip())
            if words:
                out[m.group(1)] = words
    return out


def classes() -> dict[str, tuple[str, ...]]:
    """关系词类 → 同类的词。"""
    return _classes_cached(str(prompting.root()))


def classes_in(text: str) -> set[str]:
    """text 里出现了哪几类关系词。"""
    t = text or ""
    return {name for name, words in classes().items() if any(w in t for w in words)}


def strip_names(state: WorldState, defs: Defs, text: str, ids: list[str]) -> str:
    """去掉话里的专名，免得名字里的字被当成实在的字词。"""
    out = text
    for eid in ids:
        for nm, _ in sorted(lx.names_of(state, defs, eid), key=lambda nw: -len(nw[0])):
            if nm and len(nm) >= 2:
                out = out.replace(nm, " ")
    return out


def fact_score(state: WorldState, defs: Defs, fact_text: str, spoken: str, *, you: str | None = None) -> int:
    """spoken 说的（或问的）是不是这条事实；0 表示对不上。

    - 点到的实体：事实里的人、物、地方在话里被叫到；「我」算玩家；``you`` 是听话的人，话里的「你、您」算他。
    - 对得上的内容：同一类关系词（近义词表），或去掉专名之后重合的二字词。
    - 只凭关系词类对上的，得是两个以上实体之间的事，而且事实里的实体个个都被点到。
    """
    if not spoken or not fact_text:
        return 0
    rs = list(dict.fromkeys(refs.refs_in(fact_text)))
    named = [r for r in rs if r != state.player and lx.match_score(state, defs, r, spoken) >= 2]
    if you and you in rs and you not in named and lx.SECOND_PERSON.search(spoken):
        named.append(you)
    me = 1 if state.player in rs and "我" in spoken else 0
    ent = len(named) + me
    fact_core = refs.REF_RE.sub(" ", fact_text)
    spoken_core = strip_names(state, defs, spoken, named)
    shared = len((lx.bigrams(spoken_core) & lx.bigrams(fact_core)) - STOP_BIGRAMS)
    cls = len(classes_in(spoken_core) & classes_in(fact_core))
    # 只凭关系词类：这条事实得是两个以上实体之间的事，而且个个都被点到（「甲和乙相好」对「甲与乙有私情」）
    by_class = cls >= 1 and len(rs) >= 2 and all(r in named or (r == state.player and me) for r in rs)
    if (ent >= 1 and shared >= 1) or shared >= 3 or by_class:
        return 3 * ent + 2 * shared + 2 * cls
    return 0


def topic_fact(state: WorldState, defs: Defs, to: str, text: str) -> str | None:
    """「把某件事告诉我」问的是哪一件：听话的人自己知道、又与这句话对得上的事实里最像的一条。"""
    t = state.chars.get(to)
    if t is None or not text:
        return None
    m = lx.VOCATIVE_RE.match(text)
    body = text[m.end():] if m else text
    best, best_s = None, 0
    for fid, f in state.facts.items():
        b = t.beliefs.get(fid)
        if b is None or b.confidence < BELIEVE:
            continue
        s = fact_score(state, defs, f.text, body, you=to)
        if s > best_s:
            best, best_s = fid, s
    return best
