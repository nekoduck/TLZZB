"""玩家视角的叙述（方案 2.2、4.4、7.3 J5、7.7 C3）。

    async for piece in narrate(llm, state, defs, view, player_fact=..., spoken_text=..., prev_tail=...):
        ui.show(piece)

- 叙述调用只拿得到 `kernel.perception.player_view` 给的视图（方案 4.4）；〈你是谁〉与人物口吻
  取自玩家角色与人物卡，不碰别人的心思与打算。
- C3 流式写，按句缓冲。每句先做字符串检查（`checks`），再问 J5 三个是非题（extra/mind/puppet），
  每句预算 0.4 秒，超时放行。违规的句子丢掉；同一段丢到第三句就停掉这次流。
- 流结束（或停掉）后问 J5 每条事实写到了没有（covered_<i>），漏写的用模板句补在段末。
- 字数：模型的文字到上限（高潮回合到 climax 上限）就在句末停；补写漏掉的事实最多写到 climax 上限。
- 任何模型失败：只用模板叙述（方案 4.3）。Mock 下 C3 的替身吐的就是模板叙述。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from tianlong import prompting
from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.perception import PlayerView
from tianlong.kernel.state import WorldState
from tianlong.llm import LLM, CallMeta, LLMError, LLMRefusal
from tianlong.narrator.checks import (
    Checker,
    SentenceSplitter,
    build_checker,
    close_sentence,
    ends_sentence,
    text_len,
)
from tianlong.narrator.templates import (
    Fact,
    fact_list,
    place_text,
    template_sentences,
)

J5_TIMEOUT_S = 0.4  # 方案 7.3：每句的核对预算
COVER_TIMEOUT_S = 1.5  # 整段写到没有：不在显示的路径上，给宽一点
MAX_DROPS = 3  # 丢到第三句就停流
STYLE_SAMPLES_MAX = 4
STYLE_SAMPLE_CHARS = 240
COMBAT_KINDS = frozenset({'combat', 'movement', 'inner'})
SENTENCE_QUESTIONS = ('extra', 'mind', 'puppet')

# 别人心思的记号（只用来给 Mock 的 J5 一个像样的提示；真 Jev 不看 hints）
_MIND_RE = re.compile(r'心想|心道|心中暗|心里暗|暗想|暗自|心下|心中想|心里想|打定主意|心里盘算|心中盘算|暗暗盘算|只当|以为你')
_PUPPET_SAY_RE = re.compile(r'你[^，。「」“”]{0,6}(?:说|道|问|答|喊|叫|笑道)[^，。「」]{0,2}[：:]?\s*[「“]')
_PUPPET_DECIDE_RE = re.compile(r'你(?:决定|打定主意|下定决心|拿定主意|心一横)')
_CJK_RE = re.compile(r'[一-鿿]')


# ---------------------------------------------------------------- 报告


@dataclass
class NarrationReport:
    """一段叙述的记账（给日志、/debug 与测试看）。传给 `narrate(report=...)` 由它填写。"""

    text: str = ''
    source: str = 'template'  # claude / mixed / template
    dropped: list[tuple[str, list[str]]] = field(default_factory=list)
    appended: list[str] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str | None = None
    j5_timeouts: int = 0
    j5_calls: int = 0
    j5_error: str | None = None
    error: str | None = None
    facts: list[str] = field(default_factory=list)
    covered: list[bool] = field(default_factory=list)


# ---------------------------------------------------------------- 题目与提示词


@lru_cache(maxsize=4)
def _questions_cached(root: str) -> dict[str, str]:
    data = yaml.safe_load(prompting.load('jev/j5_questions')) or {}
    return {str(k): str(v) for k, v in dict(data).items()}


def j5_questions() -> dict[str, str]:
    """J5 的问题文字（prompts/jev/j5_questions.md）：extra / mind / puppet / covered。"""
    return _questions_cached(str(prompting.root()))


def player_brief(state: WorldState, defs: Defs) -> str:
    """〈你是谁〉：玩家角色的身份、学过的本事、会不会武功（只用玩家自己知道的东西）。"""
    p = state.player
    c = state.chars[p]
    parts: list[str] = []
    who = c.unknown_alias.strip()
    parts.append(f'{who}，名叫{c.name}' if who else f'名叫{c.name}')
    if c.superior and c.superior in state.chars:
        parts.append(f'听{refs.display_name(state, defs, c.superior, p)}差遣')
    labels = defs.world.labels or {}
    for r in c.roles:
        lbl = labels.get(r)
        if lbl:
            parts.append(str(lbl))
    fights = c.base_power > 0
    for art_id, lvl in c.arts.items():
        if lvl < 1:
            continue
        a = defs.arts.get(art_id)
        name = refs.display_name(state, defs, art_id, p)
        stage = ''
        if a is not None and len(a.stages) >= lvl:
            stage = a.stages[lvl - 1].name
        parts.append(f'学过{name}' + (f'（{stage}）' if stage else ''))
        if a is not None and a.kind in COMBAT_KINDS:
            fights = True
    if not fights:
        parts.append('不会武功')
    if c.literacy <= 0:
        parts.append('不识字')
    return '｜'.join(parts)


def names_table(view: PlayerView) -> str:
    seen: list[str] = []
    for v in view.names.values():
        if v and v != '你' and v not in seen:
            seen.append(place_text(v) if '·' in v else v)
    return '、'.join(seen) or '无'


def _event_line(i: int, f: Fact) -> str:
    return f'{i}. {f.text}'


def _voices(state: WorldState, defs: Defs, view: PlayerView) -> str:
    """开口的人的说话习惯与至多两句台词样例（人物卡的 voice）。"""
    by_id = {e.id: e for e in state.events_tail}
    rev = {v: k for k, v in view.names.items()}
    lines: list[str] = []
    done: set[str] = set()
    for ev in view.events:
        if not ev.speech or ev.by_player or ev.speech.get('act') == 'whisper' and not ev.speech.get('gist'):
            continue
        src = by_id.get(ev.id)
        cid = src.actor if src is not None else rev.get(ev.actor or '')
        if not cid or cid in done or cid == state.player or cid not in state.chars:
            continue
        done.add(cid)
        ch = state.chars[cid]
        card = defs.card_for(ch.persona) or defs.cards.get(cid)
        label = view.names.get(cid) or ev.actor or ''
        if card is None or not label:
            continue
        habits = '，'.join(h for h in card.voice.habits if h)
        samples: list[str] = []
        for s in card.voice.samples[:2]:
            t = s.get('text') if isinstance(s, dict) else s
            if t:
                samples.append(f'「{str(t).strip()}」')
        if habits or samples:
            lines.append(f'{label}：{habits}' + (' ' + ' '.join(samples) if samples else ''))
    return '\n'.join(lines) or '无'


def _scene_kind(view: PlayerView) -> list[str]:
    verbs = {ev.verb for ev in view.events}
    if verbs & {'attack', 'restrain', 'push', 'block', 'flee', 'struggle'} or view.danger >= 2:
        return ['fight', 'combat', '打斗', 'tense', '紧张']
    if any(ev.speech for ev in view.events):
        return ['dialogue', 'talk', '对话', '言谈']
    return ['scene', 'scenery', '景物', 'calm', '日常']


def style_block(defs: Defs, view: PlayerView) -> str:
    """system 第二块：按场面类型取 2 到 4 段文风样例；样例库空时用 style/guide.md。"""
    samples = defs.style.samples or {}
    picked: list[str] = []
    keys = [k for k in _scene_kind(view) if k in samples] + sorted(samples)
    for k in keys:
        for s in samples.get(k) or []:
            t = s.get('text') if isinstance(s, dict) else s
            t = str(t or '').strip()
            if t and t not in picked:
                picked.append(t[:STYLE_SAMPLE_CHARS])
            if len(picked) >= STYLE_SAMPLES_MAX:
                break
        if len(picked) >= STYLE_SAMPLES_MAX:
            break
    if picked:
        body = '\n\n'.join(picked)
    else:
        body = (defs.style.guide or '').strip()
    if not body:
        return ''
    return prompting.render('c3_style', samples=body)


def _player_action(view: PlayerView, player_fact: str) -> str:
    if player_fact.strip():
        return player_fact.strip()
    for ev in view.events:
        if ev.by_player:
            return ev.text
    return '无'


def c3_prompt(
    state: WorldState,
    defs: Defs,
    view: PlayerView,
    facts: Sequence[Fact],
    *,
    player_fact: str = '',
    spoken_text: str | None = None,
    prev_tail: str = '',
    min_len: int = 80,
    max_len: int = 220,
) -> tuple[list[str], str]:
    """C3 的 system 块与 user 文字（方案 7.7）。"""
    system = [prompting.load('c3_system')]
    style = style_block(defs, view)
    if style:
        system.append(style)
    events = '\n'.join(_event_line(i + 1, f) for i, f in enumerate(f for f in facts if f.kind != 'heard'))
    loc = place_text(view.location)
    user = prompting.render(
        'c3_user',
        player_brief=player_brief(state, defs),
        location_desc=f'{loc}：{view.location_desc}' if view.location_desc else loc,
        time_text=view.clock,
        names=names_table(view),
        player_action_fact=_player_action(view, player_fact),
        player_spoken_text=(spoken_text or '').strip() or '无',
        events=events or '无',
        heard_only='；'.join(view.heard) or '无',
        voices=_voices(state, defs, view),
        body='；'.join(view.body) or '无',
        prev_tail=(prev_tail or '').strip()[-80:] or '无',
        min_len=min_len,
        max_len=max_len,
    )
    return system, user


# ---------------------------------------------------------------- 规则提示（Mock 用）


def _bigrams(text: str) -> set[str]:
    chars = ''.join(_CJK_RE.findall(text or ''))
    return {chars[i:i + 2] for i in range(len(chars) - 1)} or ({chars} if chars else set())


def overlap(fact_text: str, text: str) -> float:
    """事实句的二字组有几成出现在文字里（0 到 1）。"""
    fb = _bigrams(fact_text.split('（语气')[0])
    if not fb:
        return 1.0
    tb = _bigrams(text)
    return len(fb & tb) / len(fb)


def covered_heuristic(fact: Fact | str, text: str, threshold: float = 0.5) -> bool:
    ft = fact.text if isinstance(fact, Fact) else fact
    return overlap(ft, text) >= threshold


def mind_heuristic(sentence: str) -> bool:
    """这句话是否写了「你」以外的人的心思（粗判）。"""
    for m in _MIND_RE.finditer(sentence):
        head = sentence[: m.start()]
        clause = re.split(r'[，。！？；：「」]', head)[-1]
        if '你' not in clause:
            return True
    return False


def puppet_heuristic(sentence: str, *, player_spoke: bool) -> bool:
    """这句话是否替「你」说了话、做了决定（粗判）。"""
    if _PUPPET_DECIDE_RE.search(sentence):
        return True
    return bool(not player_spoke and _PUPPET_SAY_RE.search(sentence))


# ---------------------------------------------------------------- 一段叙述


class _Narration:
    def __init__(
        self,
        llm: LLM,
        state: WorldState,
        defs: Defs,
        view: PlayerView,
        *,
        player_fact: str,
        spoken_text: str | None,
        prev_tail: str,
        climax: bool,
        check_timeout: float,
        report: NarrationReport,
    ) -> None:
        self.llm = llm
        self.state = state
        self.defs = defs
        self.view = view
        self.player_fact = player_fact or ''
        self.spoken_text = (spoken_text or '').strip() or None
        self.prev_tail = prev_tail or ''
        self.report = report
        self.check_timeout = check_timeout
        ns = llm.settings.narration
        self.min_len = ns.min_len
        self.max_len = ns.climax_max_len if climax else ns.max_len
        self.hard_max = max(ns.climax_max_len, self.max_len)
        self.thresholds = llm.settings.jev.thresholds
        self.facts = fact_list(view, player_fact=self.player_fact, spoken_text=self.spoken_text)
        report.facts = [f.text for f in self.facts]
        quoted = [self.spoken_text] if self.spoken_text else []
        for ev in view.events:
            if ev.by_player and ev.speech and ev.speech.get('spoken_text'):
                quoted.append(str(ev.speech['spoken_text']))
        self.checker: Checker = build_checker(state, defs, view, quoted=quoted)
        self.template = template_sentences(
            view, facts=self.facts, min_len=self.min_len, max_len=self.max_len, hard_max=self.hard_max
        )
        self.player_spoke = bool(quoted) or any(ev.by_player and ev.speech for ev in view.events)
        self.j5_on = self._j5_enabled()
        self.emitted: list[str] = []
        self.length = 0
        self.stopped = False
        self._pending_break = False
        self._j5_base: dict[str, Any] | None = None

    # -- 路由
    def _j5_enabled(self) -> bool:
        try:
            m = self.llm.mode('check')
        except ValueError:
            return False
        return m in ('jev_first', 'claude_first') and self.llm.jev_available

    def use_claude(self) -> bool:
        try:
            if self.llm.mode('narrate') == 'rules_only':
                return False
        except ValueError:
            pass
        return self.llm.claude_available

    # -- 输出
    def _out(self, sentence: str) -> str:
        s = sentence
        if self._pending_break and self.emitted and not self.emitted[-1].endswith('\n'):
            s = '\n' + s
        self._pending_break = False
        self.emitted.append(s)
        self.length += text_len(s)
        return s

    def text(self) -> str:
        return ''.join(self.emitted)

    # -- J5
    def j5_base(self) -> dict[str, Any]:
        if self._j5_base is None:
            v = self.view
            scene = place_text(v.location) + ('：' + v.location_desc if v.location_desc else '')
            extra = [*v.body]
            self._j5_base = {
                'facts': [f.text for f in self.facts],
                'scene': scene + (('；' + '；'.join(extra)) if extra else ''),
                'present': '、'.join(v.present) or '无',
                'names': names_table(v),
                'player_action': _player_action(v, self.player_fact)
                + (f'（原话：{self.spoken_text}）' if self.spoken_text else ''),
            }
        return self._j5_base

    async def j5_sentence(self, sentence: str) -> list[str]:
        if not self.j5_on:
            return []
        qtext = j5_questions()
        qs = {k: {'type': 'noul', 'instructions': qtext[k]} for k in SENTENCE_QUESTIONS}
        state_text = prompting.render('jev/j5_state', **self.j5_base(), sentence=sentence.strip(), paragraph='')
        hints = {
            'extra': 0.0,
            'mind': 0.9 if mind_heuristic(sentence) else 0.0,
            'puppet': 0.9 if puppet_heuristic(sentence, player_spoke=self.player_spoke) else 0.0,
        }
        self.report.j5_calls += 1
        try:
            ans = await asyncio.wait_for(self.llm.jev.ask('J5', state_text, qs, hints=hints), self.check_timeout)
        except TimeoutError:
            self.report.j5_timeouts += 1
            return []
        except LLMError as e:
            self.j5_on = False  # 退让表：J5 失败 → 只做字符串检查
            self.report.j5_error = f'{type(e).__name__}: {e}'
            return []
        th = float(self.thresholds.narration_flag)
        out: list[str] = []
        for k in SENTENCE_QUESTIONS:
            a = ans.get(k)
            if a is not None and float(a.get('noul', 0.0)) > th:
                out.append(f'j5:{k}')
        return out

    async def coverage(self, text: str) -> list[bool]:
        if not self.facts:
            return []
        heur = [overlap(f.text, text) for f in self.facts]
        rough = [h >= 0.5 for h in heur]
        if not self.j5_on or not text.strip():
            return rough
        prefix = j5_questions().get('covered', '这段话写到了这件事：')
        qs = {f'covered_{i}': {'type': 'noul', 'instructions': prefix + f.text} for i, f in enumerate(self.facts)}
        hints = {f'covered_{i}': (1.0 if r else 0.0) for i, r in enumerate(rough)}
        state_text = prompting.render('jev/j5_state', **self.j5_base(), sentence='', paragraph=text.strip())
        try:
            ans = await asyncio.wait_for(
                self.llm.jev.ask('J5_cover', state_text, qs, hints=hints), COVER_TIMEOUT_S
            )
        except (TimeoutError, LLMError) as e:
            self.report.j5_error = self.report.j5_error or f'cover {type(e).__name__}'
            return rough
        th = float(self.thresholds.noul_yes)
        out: list[bool] = []
        for i, h in enumerate(heur):
            a = ans.get(f'covered_{i}')
            yes = a is not None and float(a.get('noul', 0.0)) > th
            out.append(yes or h >= 0.8)  # 原句几乎照抄的，不再补
        return out

    # -- 一句
    async def handle(self, raw: str) -> list[str]:
        """一句流出来的话：检查，过了就返回要显示的文字；丢掉返回空表。"""
        para_end = raw.endswith('\n')
        sentence = raw.strip()
        if not sentence:
            if para_end and self.emitted:
                self._pending_break = True
            return []
        k = text_len(sentence)
        if self.length + k > self.max_len:
            self.stopped = True
            self.report.stopped_early = True
            self.report.stop_reason = self.report.stop_reason or 'length'
            return []
        viol = self.checker.check(sentence)
        if not viol:
            viol = await self.j5_sentence(sentence)
        if viol:
            self.report.dropped.append((sentence, viol))
            if para_end and self.emitted:
                self._pending_break = True
            if len(self.report.dropped) >= MAX_DROPS:
                self.stopped = True
                self.report.stopped_early = True
                self.report.stop_reason = 'violations'
            return []
        out = self._out(sentence)
        if para_end:
            self._pending_break = True
        return [out]

    def finish_lines(self, covered: Sequence[bool]) -> list[str]:
        out: list[str] = []
        for f, ok in zip(self.facts, covered, strict=False):
            if ok:
                continue
            k = text_len(f.line)
            if self.length + k > self.hard_max:
                break
            self.report.appended.append(f.line)
            out.append(self._out(f.line))
        return out


async def narrate(
    llm: LLM,
    state: WorldState,
    defs: Defs,
    view: PlayerView,
    *,
    player_fact: str = '',
    spoken_text: str | None = None,
    prev_tail: str = '',
    climax: bool = False,
    check_timeout: float = J5_TIMEOUT_S,
    report: NarrationReport | None = None,
) -> AsyncIterator[str]:
    """一段玩家视角的叙述，逐句吐出已过检查的文字（C3 流式 + 逐句检查 + J5，失败退回模板）。

    `player_fact`：玩家本回合动作的事实句（视图里已有玩家的事件时可不给）；`spoken_text`：玩家原话；
    `prev_tail`：上一段叙述的结尾；`climax`：场面最紧要的回合，字数上限放到 climax_max_len。
    `report` 给了就把丢句、补句、J5 超时等记进去。
    """
    rep = report if report is not None else NarrationReport()
    n = _Narration(
        llm, state, defs, view,
        player_fact=player_fact, spoken_text=spoken_text, prev_tail=prev_tail, climax=climax,
        check_timeout=check_timeout, report=rep,
    )
    if not n.use_claude():
        for s in n.template:
            yield n._out(s)
        rep.source = 'template'
        rep.text = n.text()
        return

    system, user = c3_prompt(
        state, defs, view, n.facts,
        player_fact=n.player_fact, spoken_text=n.spoken_text, prev_tail=n.prev_tail,
        min_len=n.min_len, max_len=n.max_len,
    )
    mock_text = ''.join(n.template)
    meta_out: list[CallMeta] = []
    splitter = SentenceSplitter()
    finished = False
    try:
        stream = llm.claude.stream_text('C3', system, user, mock_text=mock_text, meta_out=meta_out)
        async with contextlib.aclosing(stream) as st:
            async for piece in st:
                for raw in splitter.feed(piece):
                    for out in await n.handle(raw):
                        yield out
                    if n.stopped:
                        break
                if n.stopped:
                    break
        finished = not n.stopped
    except LLMError as e:
        rep.error = f'{type(e).__name__}: {e}'
        if not isinstance(e, LLMRefusal) and not n.stopped:
            # 断流：已经收住的句子照常核对；拒答的不要
            for raw in [t for t in splitter.flush() if ends_sentence(t)]:
                if n.stopped:
                    break
                for out in await n.handle(raw):
                    yield out
    meta = meta_out[-1] if meta_out else None
    if meta is not None and meta.stop_reason and not rep.stop_reason:
        rep.stop_reason = meta.stop_reason
    if finished:
        tail = splitter.flush()
        if meta is not None and meta.stop_reason == 'max_tokens':
            # 截断：没写完的半句不要，只留已经收住的句子（方案 7.4 第 8 条的流式版本）
            tail = [t for t in tail if ends_sentence(t)]
        elif tail and not tail[-1].endswith('\n'):
            tail[-1] = close_sentence(tail[-1])  # 正常收尾却没有句末标点：补个句号
        for raw in tail:
            if n.stopped:
                break
            for out in await n.handle(raw):
                yield out

    if not n.emitted:
        # 一句也没写成：整段模板
        for s in n.template:
            yield n._out(s)
        rep.source = 'template'
        rep.text = n.text()
        _annotate(llm, meta, rep)
        return

    covered = await n.coverage(n.text())
    rep.covered = list(covered)
    for s in n.finish_lines(covered):
        yield s
    rep.source = 'mixed' if rep.appended else 'claude'
    rep.text = n.text()
    _annotate(llm, meta, rep)


def _annotate(llm: LLM, meta: CallMeta | None, rep: NarrationReport) -> None:
    """把丢掉的句子补记到这次 C3 调用上（calls.jsonl）。"""
    if meta is None or not rep.dropped:
        return
    with contextlib.suppress(Exception):
        llm.recorder.annotate(meta.seq, violations=[f'{"/".join(v)}：{s}' for s, v in rep.dropped])


async def narrate_text(
    llm: LLM,
    state: WorldState,
    defs: Defs,
    view: PlayerView,
    **kw: Any,
) -> str:
    """不流式：整段叙述的文字（测试与日志用）。参数同 `narrate`。"""
    parts: list[str] = []
    async for piece in narrate(llm, state, defs, view, **kw):
        parts.append(piece)
    return ''.join(parts)


__all__ = [
    'J5_TIMEOUT_S',
    'MAX_DROPS',
    'NarrationReport',
    'c3_prompt',
    'covered_heuristic',
    'j5_questions',
    'mind_heuristic',
    'names_table',
    'narrate',
    'narrate_text',
    'overlap',
    'player_brief',
    'puppet_heuristic',
    'style_block',
]
