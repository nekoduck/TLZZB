"""选项措辞（方案 2.3、7.8 C4）：三个动作各写成一行给玩家看的打算，过不了校验的换成动词模板句。

    texts = await phrase_options(llm, state, defs, options, avoid=[上回合选中的那条])

`options` 是带 `.action`（内核的 `ActionInst`）与 `.tag`（方向标签）的对象，顺序即一、二、三。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tianlong import prompting
from tianlong.kernel import perception, refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.llm import LLM, LLMError
from tianlong.llm.schemas import OptionsOut, OptionText
from tianlong.narrator.checks import Checker, build_checker, text_len
from tianlong.narrator.templates import OPTION_MAX, option_template, place_text

_ENUM_RE = re.compile(r'^\s*(?:[一二三四五六七八九十]+[、.．:：]|[0-9０-９]+[、.．:：)）]|[-*•·]\s*|[（(][一二三0-9][)）])\s*')


@dataclass
class OptionsReport:
    """记账：哪几条用了模型的措辞，哪几条换成了模板句、为什么。"""

    source: list[str] = field(default_factory=list)  # 每条：'claude' / 'template'
    rejected: list[tuple[int, str, list[str]]] = field(default_factory=list)
    error: str | None = None


def _action(o: Any) -> Any:
    return getattr(o, 'action', o)


def _tag(o: Any) -> str:
    return str(getattr(o, 'tag', '') or '')


def last_chosen_texts(state: WorldState) -> list[str]:
    """上回合被选中的选项文字：`state.pending_options` 里带 `chosen` 与 `text` 的条目。"""
    out: list[str] = []
    for o in state.pending_options or []:
        if isinstance(o, dict) and o.get('chosen') and o.get('text'):
            out.append(str(o['text']))
    return out


def _fact(state: WorldState, defs: Defs, action: Any) -> str:
    return place_text(refs.render(getattr(action, 'desc', '') or '', state, defs, state.player)).strip()


def _scene(state: WorldState, defs: Defs) -> tuple[str, list[str]]:
    """一句场景概况与在场者的叫法（玩家视角）。"""
    from tianlong.kernel.timeflow import clock_text

    p = state.player
    me = state.chars[p]
    place = place_text(refs.display_name(state, defs, me.loc, p))
    people = [refs.display_name(state, defs, c.id, p) for c in perception.visible_people(state, me)]
    head = f'{clock_text(state.tick)}，你在{place}'
    if people:
        head += '，眼前有' + '、'.join(people[:6])
    return head + '。', people


def _names(state: WorldState, defs: Defs, actions: Sequence[Any], people: Sequence[str]) -> list[str]:
    p = state.player
    out: list[str] = []
    for n in people:
        if n and n not in out:
            out.append(n)
    for a in actions:
        for eid in refs.refs_in(getattr(a, 'desc', '') or ''):
            n = refs.display_name(state, defs, eid, p)
            if n and n != '你' and n not in out:
                out.append(place_text(n))
    return out


def clean_option(text: str) -> str:
    """去掉模型可能加的编号、引号、句末标点与空白。"""
    t = (text or '').strip()
    t = _ENUM_RE.sub('', t, count=1).strip()
    t = t.strip('「」“”"\'').strip()
    t = t.rstrip('。.')
    return t


def validate_option(text: str, checker: Checker, *, avoid: Sequence[str] = ()) -> list[str]:
    """一条选项的毛病；空表即合格。"""
    problems: list[str] = []
    if not text:
        return ['empty']
    if '\n' in text:
        problems.append('multiline')
    if text_len(text) > OPTION_MAX:
        problems.append('too_long')
    if text in set(avoid):
        problems.append('repeat_last')
    problems += checker.check(text)
    return problems


def template_options(
    state: WorldState, defs: Defs, options: Sequence[Any], *, avoid: Sequence[str] = ()
) -> list[str]:
    """每个动作的模板句，三条互不相同，也不与 `avoid` 重复。"""
    out: list[str] = []
    for o in options:
        a = _action(o)
        text = option_template(state, defs, a, avoid=[*avoid, *out])
        out.append(text)
    return out


def _use_claude(llm: LLM) -> bool:
    try:
        if llm.mode('phrase') == 'rules_only':
            return False
    except ValueError:
        pass
    return llm.claude_available


async def phrase_options(
    llm: LLM,
    state: WorldState,
    defs: Defs,
    options: Sequence[Any],
    *,
    avoid: Sequence[str] = (),
    report: OptionsReport | None = None,
) -> list[str]:
    """C4：把三个动作写成三行选项。哪一条不合格就换成该动词的模板句；调用失败三条都用模板句。

    `avoid`：不许重复的文字（上回合被选中的那条）；不给时从 `state.pending_options` 里找。
    """
    rep = report if report is not None else OptionsReport()
    opts = list(options)
    if not opts:
        return []
    avoid_all = [*avoid, *last_chosen_texts(state)]
    actions = [_action(o) for o in opts]
    templates = template_options(state, defs, opts, avoid=avoid_all)
    rep.source = ['template'] * len(opts)
    if not _use_claude(llm):
        return templates

    scene, people = _scene(state, defs)
    names = _names(state, defs, actions, people)
    facts = [_fact(state, defs, a) for a in actions]
    checker = build_checker(state, defs, None, allowed_texts=[scene, *names, *facts])
    system = [prompting.load('c4_system')]
    user = prompting.render(
        'c4_user',
        scene=scene,
        names='、'.join(names) or '无',
        options=[{'id': a.id, 'fact': f, 'tag': _tag(o) or '无'} for a, f, o in zip(actions, facts, opts, strict=True)],
        avoid='；'.join(a for a in avoid_all if a),
    )

    def mock_default() -> OptionsOut:
        return OptionsOut(options=[OptionText(id=a.id, text=t) for a, t in zip(actions, templates, strict=True)])

    try:
        out, _meta = await llm.claude.call_json('C4', system, user, OptionsOut, mock_default=mock_default)
    except LLMError as e:
        rep.error = f'{type(e).__name__}: {e}'
        return templates

    got = list(out.options)
    ids = [o.id for o in got]
    unique = len(set(ids)) == len(ids)
    result: list[str] = []
    for i, a in enumerate(actions):
        problems: list[str] = []
        text = ''
        if i >= len(got) or not unique or got[i].id != a.id:
            problems.append('id')
        else:
            text = clean_option(got[i].text)
            problems = validate_option(text, checker, avoid=avoid_all)
            if text in result:
                problems.append('duplicate')
        if problems:
            rep.rejected.append((i, text, problems))
            fallback = templates[i]
            if fallback in result:
                fallback = option_template(state, defs, a, variant=1, avoid=[*avoid_all, *result])
            result.append(fallback)
        else:
            rep.source[i] = 'claude'
            result.append(text)
    return result


__all__ = [
    'OptionsReport',
    'clean_option',
    'last_chosen_texts',
    'phrase_options',
    'template_options',
    'validate_option',
]
