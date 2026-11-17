"""P2 时间线（方案 9.1）：段落 → `build/timeline.json`。

每回一条请求（`p2_ch01` …）。校验：段落范围的编号有效、起不晚于止，无效的事件丢掉；事件按段落顺序排列
（模型给的顺序不对就改过来并记进报告）；时间线索要引得出原文：线索里必须有有效的段落编号，线索里用
「」或“”括起来的字样必须是那一段的子串，否则这条线索作废。`who` 里不在 P1 实体表里的 id 记进报告。
规则草稿（Mock 与兜底）：相邻几段出场的人相同就并成一条，地点取段里最近提到的地点，时间线索取段里的时辰字眼。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from tianlong import prompting
from tianlong.llm.schemas import TimelineEvent, TimelineOut
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    Lazy,
    Report,
    Request,
    StepResult,
    ask,
    copied_text_problems,
    outcome_lines,
    para_ids_in,
    read_json,
    rel,
    write_json,
)
from tianlong.pipeline.p1_entities import load_entities

# 时间字眼：只给规则草稿用（通用词，不涉剧情）
TIME_WORDS = ('次日', '翌日', '第二日', '第三日', '当晚', '当夜', '半夜', '午夜', '清晨', '黎明', '天明', '天亮',
              '早晨', '上午', '中午', '午后', '下午', '傍晚', '黄昏', '日落', '入夜', '三更', '五更', '片刻',
              '一个时辰', '半个时辰', '多时', '数日', '几日', '三日', '七日', '十日')
_QUOTE_RE = re.compile(r'[「“]([^」”]{1,40})[」”]')


def _names(e: Mapping[str, Any]) -> list[str]:
    return [n for n in [e.get('name'), *(e.get('aliases') or [])] if isinstance(n, str) and n]


def rule_timeline(corpus: Corpus, entities: Sequence[Mapping[str, Any]], chapter: int) -> TimelineOut:
    chars = [e for e in entities if e.get('kind') == 'character']
    locs = [e for e in entities if e.get('kind') == 'location']
    events: list[TimelineEvent] = []
    where: str | None = None
    for p in (x for x in corpus.paragraphs if x.chapter == chapter):
        who = [e['id'] for e in chars if any(n in p.text for n in _names(e))]
        found = [(p.text.rfind(n), e['id']) for e in locs for n in _names(e) if n in p.text]
        if found:
            where = max(found)[1]
        if not who:
            continue
        hint = next((w for w in TIME_WORDS if w in p.text), None)
        time_hint = f'{p.id}：「{hint}」' if hint else None
        last = events[-1] if events else None
        if last is not None and last.who == who and last.where == where and time_hint is None:
            last.para_to = p.id
            continue
        names = '、'.join(str(next((e['name'] for e in chars if e['id'] == w), w)) for w in who)
        events.append(TimelineEvent(who=who, where=where, what=f'{names}在这几段里出场（规则草稿，未经模型概括）',
                                    result='', para_from=p.id, para_to=p.id, time_hint=time_hint))
    return TimelineOut(events=events)


def check_events(
    corpus: Corpus, parts: Sequence[TimelineOut], known_ids: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    problems: list[str] = []
    events: list[dict[str, Any]] = []
    for part in parts:
        for ev in part.events:
            d = ev.model_dump()
            a, b = d['para_from'], d['para_to']
            if not corpus.valid(a):
                problems.append(f'事件「{d["what"][:20]}」的起始段落 {a!r} 无效，丢掉')
                continue
            if not corpus.valid(b):
                problems.append(f'事件「{d["what"][:20]}」的结束段落 {b!r} 无效，改为 {a}')
                d['para_to'] = b = a
            if corpus.order(b) < corpus.order(a):
                problems.append(f'事件「{d["what"][:20]}」段落范围颠倒（{a}–{b}），已对调')
                d['para_from'], d['para_to'] = b, a
            hint = d.get('time_hint')
            if hint:
                pids = [x for x in para_ids_in(hint) if corpus.valid(x)]
                quotes = _QUOTE_RE.findall(hint)
                bad_q = [q for q in quotes if not any(q in corpus.by_id[x].text for x in pids)]
                if not pids:
                    problems.append(f'时间线索「{hint[:30]}」没有有效的段落编号，作废')
                    d['time_hint'] = None
                elif bad_q:
                    problems.append(f'时间线索「{hint[:30]}」里的字样在 {pids[0]} 里找不到，作废')
                    d['time_hint'] = None
            unknown = [w for w in d['who'] if known_ids and w not in known_ids]
            if unknown:
                problems.append(f'事件「{d["what"][:20]}」里的 {", ".join(unknown)} 不在 P1 实体表里')
            problems += copied_text_problems(corpus, {'what': d['what'], 'result': d['result']},
                                             f'事件 {d["para_from"]}')
            events.append(d)
    order = [corpus.order(e['para_from']) for e in events]
    if order != sorted(order):
        problems.append('模型给的事件不全按段落顺序，已重排')
    events.sort(key=lambda e: (corpus.order(e['para_from']), corpus.order(e['para_to'])))
    return events, problems


def load_timeline(ctx: Ctx) -> list[dict[str, Any]]:
    data = read_json(ctx.paths.timeline)
    if isinstance(data, Mapping) and isinstance(data.get('events'), list):
        return [e for e in data['events'] if isinstance(e, Mapping)]
    return []


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    entities = load_entities(ctx)
    notes: list[str] = []
    if not entities:
        notes.append('没有 build/entities.json（P1 未运行或为空）：时间线里的 id 无从对照')
    ent_lines = [f'{e["id"]}｜{e["kind"]}｜{e["name"]}' + (f'（又作 {"、".join(e["aliases"])}）' if e.get('aliases')
                                                          else '') for e in entities]
    requests: list[Request] = []
    for n, title, ps in corpus.chapters():
        user = prompting.render('pipeline/p2_user', chapter=n, title=title, first=ps[0].id, last=ps[-1].id,
                                entities=ent_lines)
        requests.append(Request(f'p2_ch{n:02d}', user, TimelineOut,
                                Lazy(lambda n=n: rule_timeline(corpus, entities, n))))
    outcomes = await ask(ctx, 'P2', requests)
    known = {str(e['id']) for e in entities}
    events, problems = check_events(corpus, [o.obj for o in outcomes.values()], known)
    write_json(ctx.paths.timeline, {
        'step': 'P2',
        'sources': {cid: o.source for cid, o in outcomes.items()},
        'events': events,
    })
    rep = Report('P2 时间线')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    rep.h('校验')
    rep.bullets(notes + problems)
    path = rep.write(ctx.paths.report('p2'))
    fb = [cid for cid, o in outcomes.items() if o.source == 'rules']
    ctx.out(f'P2 时间线：{len(events)} 条事件 → {rel(ctx.paths.timeline, ctx.settings.root)}'
            f'（{len(problems)} 条校验提示{"，" + str(len(fb)) + " 条用了规则草稿" if fb else ""}）')
    return StepResult('p2', problems=notes + problems, written=[rel(ctx.paths.timeline, ctx.settings.root)],
                      report=path, summary=f'{len(events)} 条事件', fallbacks=fb)
