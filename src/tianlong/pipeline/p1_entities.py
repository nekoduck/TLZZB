"""P1 实体（方案 9.1）：段落 → `build/entities.json`。

每回一条请求（`p1_ch01` …），结果按 id 合并。校验：每个实体至少有一个有效的段落编号（无效的编号丢掉，
一个有效编号都没有的实体整条丢掉）；别名去重（去掉与本名相同的、重复的；同一别名落在几个实体上的记进报告）。
规则草稿（Mock 与兜底）：现有 content/ 里的实体，在原文里找得到本名或别名的段落就收。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from tianlong import prompting
from tianlong.llm.schemas import EntitiesOut, Entity
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    Lazy,
    Outcome,
    Report,
    Request,
    SeedEntity,
    StepResult,
    ask,
    norm_id,
    outcome_lines,
    read_json,
    rel,
    seed_entities,
    valid_paras,
    write_json,
)

KINDS = ('character', 'location', 'item', 'art', 'group', 'other')


def rule_entities(corpus: Corpus, seeds: Iterable[SeedEntity], chapter: int | None = None) -> EntitiesOut:
    """种子实体里在原文（或某一回）出现过的，带上出现的段落。"""
    paras = [p for p in corpus.paragraphs if chapter is None or p.chapter == chapter]
    out: list[Entity] = []
    for s in seeds:
        names = [n for n in [s.name, *s.aliases] if n]
        hits = [p.id for p in paras if any(n in p.text for n in names)]
        if hits:
            out.append(Entity(id=s.id, kind=s.kind if s.kind in KINDS else 'other', name=s.name,
                              aliases=list(s.aliases), paragraphs=hits))
    return EntitiesOut(entities=out)


def merge_entities(
    corpus: Corpus, parts: Iterable[EntitiesOut]
) -> tuple[list[dict[str, Any]], list[str]]:
    """合并各回的结果并校验。返回 (实体表, 问题)。"""
    problems: list[str] = []
    merged: dict[str, dict[str, Any]] = {}
    by_name: dict[str, str] = {}
    for part in parts:
        for e in part.entities:
            eid = norm_id(e.id) or norm_id(e.name)
            if not eid:
                problems.append(f'实体「{e.name}」没有可用的 id，丢掉')
                continue
            if eid != e.id:
                problems.append(f'id {e.id!r} 改写为 {eid!r}')
            if eid not in merged and e.name in by_name:  # 别的回给了不同的 id：按本名并到先来的那个
                problems.append(f'「{e.name}」在不同回里有两个 id（{by_name[e.name]}、{eid}），并为 {by_name[e.name]}')
                eid = by_name[e.name]
            good, bad = valid_paras(corpus, e.paragraphs)
            if bad:
                problems.append(f'{eid}：丢掉无效的段落编号 {", ".join(bad[:5])}{" 等" if len(bad) > 5 else ""}')
            cur = merged.setdefault(eid, {'id': eid, 'kind': e.kind, 'name': e.name, 'aliases': [], 'paragraphs': []})
            by_name.setdefault(e.name, eid)
            if cur['kind'] != e.kind:
                problems.append(f'{eid}：类别不一致（{cur["kind"]} / {e.kind}），取 {cur["kind"]}')
            cur['aliases'] += list(e.aliases)
            cur['paragraphs'] += good
    alias_owner: dict[str, list[str]] = {}
    out: list[dict[str, Any]] = []
    for eid, cur in merged.items():
        cur['paragraphs'] = sorted(dict.fromkeys(cur['paragraphs']), key=corpus.order)
        if not cur['paragraphs']:
            problems.append(f'{eid}（{cur["name"]}）：没有一个有效的段落编号，丢掉')
            continue
        aliases = []
        for a in cur['aliases']:
            a = str(a).strip()
            if a and a != cur['name'] and a not in aliases:
                aliases.append(a)
        cur['aliases'] = aliases
        for a in [cur['name'], *aliases]:
            alias_owner.setdefault(a, []).append(eid)
        out.append(cur)
    for a, owners in alias_owner.items():
        if len(owners) > 1:
            problems.append(f'叫法「{a}」同时属于 {", ".join(owners)}，请人工分辨')
    out.sort(key=lambda e: (KINDS.index(e['kind']) if e['kind'] in KINDS else 9, corpus.order(e['paragraphs'][0])))
    return out, problems


def load_entities(ctx: Ctx) -> list[dict[str, Any]]:
    data = read_json(ctx.paths.entities)
    if isinstance(data, Mapping) and isinstance(data.get('entities'), list):
        return [e for e in data['entities'] if isinstance(e, Mapping)]
    return []


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    seeds = seed_entities(ctx.paths.content)
    seed_lines = [f'{s.id}｜{s.kind}｜{s.name}' for s in seeds]
    requests: list[Request] = []
    for n, title, ps in corpus.chapters():
        user = prompting.render('pipeline/p1_user', chapter=n, title=title, first=ps[0].id, last=ps[-1].id,
                                seeds=seed_lines, kinds=list(KINDS))
        requests.append(Request(f'p1_ch{n:02d}', user, EntitiesOut,
                                Lazy(lambda n=n: rule_entities(corpus, seeds, n))))
    outcomes: dict[str, Outcome] = await ask(ctx, 'P1', requests)
    entities, problems = merge_entities(corpus, [o.obj for o in outcomes.values()])
    write_json(ctx.paths.entities, {
        'step': 'P1',
        'sources': {cid: o.source for cid, o in outcomes.items()},
        'entities': entities,
    })
    rep = Report('P1 实体')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    counts: dict[str, int] = {}
    for e in entities:
        counts[e['kind']] = counts.get(e['kind'], 0) + 1
    rep.h('统计')
    rep.bullets([f'{k}：{v}' for k, v in counts.items()])
    rep.h('校验')
    rep.bullets(problems)
    path = rep.write(ctx.paths.report('p1'))
    fb = [cid for cid, o in outcomes.items() if o.source == 'rules']
    ctx.out(f'P1 实体：{len(entities)} 个 → {rel(ctx.paths.entities, ctx.settings.root)}'
            f'（{len(problems)} 条校验提示{"，" + str(len(fb)) + " 条用了规则草稿" if fb else ""}）')
    return StepResult('p1', problems=problems, written=[rel(ctx.paths.entities, ctx.settings.root)],
                      report=path, summary=f'{len(entities)} 个实体', fallbacks=fb)
