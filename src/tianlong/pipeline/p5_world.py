"""P5 地点、物件、事实、本事与专名（方案 9.1）：段落 → `locations.yaml`、`items.yaml`、`facts.yaml`、
`arts.yaml`、`lexicon.yaml`。

每个文件一条请求（`p5_locations` …），都用 `WorldFilesDraft`，每条只填一个字段。写入前查两件事：
地图连通（从玩家的开局地点走得到每个不在图外的地点，隐藏出口与要判定的出口也算）；打算、情境、人物卡、
开局快照用到的地点、物件、事实、本事都存在。事实表只收开局那一刻为真的事（标了 after_opening 的丢掉）。
专名表的两张禁用词表只增不减。带 `locked: true` 的条目、草稿漏掉的条目都保留原样。
规则草稿（Mock 与兜底）：现有的五个文件原样。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from tianlong import prompting
from tianlong.kernel.effects import EFFECT_SHAPES
from tianlong.kernel.predicates import PREDICATE_SHAPES
from tianlong.llm.schemas import WorldFilesDraft
from tianlong.pipeline.common import (
    AFTER_OPENING_TAGS,
    Ctx,
    Lazy,
    Report,
    Request,
    StepResult,
    WriteResult,
    ask,
    commit_yaml,
    copied_text_problems,
    outcome_lines,
    parse_yaml_text,
    plan_yaml,
    read_json,
    read_yaml,
    rel,
)
from tianlong.pipeline.p1_entities import load_entities
from tianlong.pipeline.p7_validate import trial_validate

FILES = {  # 字段 → (文件名, 实体类别)
    'locations_yaml': ('locations.yaml', 'location'),
    'items_yaml': ('items.yaml', 'item'),
    'facts_yaml': ('facts.yaml', 'fact'),
    'arts_yaml': ('arts.yaml', 'art'),
    'lexicon_yaml': ('lexicon.yaml', 'other'),
}
BANNED = ('banned_modern', 'banned_game')
_ARG_KIND = {'loc': 'location', 'loc?': 'location', 'item': 'item', 'fact': 'fact', 'art': 'art'}
_DO_KEYS = {'item': 'item', 'art': 'art', 'fact': 'fact', 'exit': 'location'}
CAT_LABEL = {'location': '地点', 'item': '物件', 'fact': '事实', 'art': '本事'}


# --------------------------------------------------------------------------- 引用


def _special(v: Any) -> bool:
    return not isinstance(v, str) or not v or v.startswith(('$', '{')) or v in ('self', 'player', 'null')


def collect_refs(data: Any, out: dict[str, set[str]], chars: Collection[str] = ()) -> None:
    """在谓词、效果、步骤里收集用到的地点、物件、事实、本事（按内核的参数形状认）。
    `at`、`loc` 写的是人的 id 时（「到某人那里」）不算地点。"""
    if isinstance(data, list):
        for x in data:
            collect_refs(x, out, chars)
        return
    if not isinstance(data, Mapping):
        return
    for k, v in data.items():
        for sh in (PREDICATE_SHAPES.get(k), EFFECT_SHAPES.get(k)):
            if isinstance(sh, list | tuple) and isinstance(v, list):
                for kind, arg in zip(sh, v, strict=False):
                    cat = _ARG_KIND.get(kind)
                    if cat and not _special(arg) and not (cat == 'location' and arg in chars):
                        out[cat].add(arg)
            elif sh == 'item' and not _special(v):
                out['item'].add(v)
        if k in ('at', 'loc') and isinstance(v, str) and not _special(v) and v not in chars:
            out['location'].add(v)
        if k == 'do' and isinstance(v, Mapping):
            for dk, cat in _DO_KEYS.items():
                if not _special(v.get(dk)) and not (cat == 'location' and v[dk] in chars):
                    out[cat].add(v[dk])
        if isinstance(v, list | Mapping):
            collect_refs(v, out, chars)


def _knows(d: Any) -> list[str]:
    ks = d.get('knows') if isinstance(d, Mapping) else None
    return [x for x in ks if isinstance(x, str)] if isinstance(ks, list) else []


def content_refs(content: Path) -> dict[str, set[str]]:
    """打算、情境、人物卡、开局快照用到的 id。goals、routines、opportunities 有各自的占位写法，不在此查
    （P7 的校验查它们认得出的谓词与效果）。"""
    out: dict[str, set[str]] = {'location': set(), 'item': set(), 'fact': set(), 'art': set()}
    opening = read_yaml(content / 'opening.yaml') or {}
    chars = {'player'}
    cards = sorted((content / 'characters').glob('*.yaml')) if (content / 'characters').is_dir() else []
    chars.update(p.stem for p in cards)
    if isinstance(opening, Mapping):
        chars.update(str(x.get('id')) for x in opening.get('extras') or [] if isinstance(x, Mapping))
        chars.update(str(c) for c in (opening.get('chars') or {}))
    for sub in ('agendas', 'situations'):
        d = content / sub
        if d.is_dir():
            for p in sorted(d.glob('*.yaml')):
                collect_refs(read_yaml(p), out, chars)
    for p in cards:
        card = read_yaml(p)
        collect_refs(card, out, chars)
        out['fact'].update(_knows(card))
        if isinstance(card, Mapping) and isinstance(card.get('arts'), Mapping):
            out['art'].update(str(a) for a in card['arts'])
    if isinstance(opening, Mapping):
        collect_refs(opening, out, chars)
        out['fact'].update(_knows(opening.get('player')))
        for x in opening.get('extras') or []:
            out['fact'].update(_knows(x))
        pl = opening.get('player')
        if isinstance(pl, Mapping) and isinstance(pl.get('arts'), Mapping):
            out['art'].update(str(a) for a in pl['arts'])
    return out


def connectivity(locations: Mapping[str, Any], start: str | None) -> list[str]:
    """从开局地点走不到的地点（图外的不算）。"""
    if not locations:
        return []
    if not start or start not in locations:
        return [f'玩家的开局地点 {start!r} 不在地点表里']
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        loc = locations.get(cur)
        for e in (loc.get('exits') or []) if isinstance(loc, Mapping) else []:
            to = e.get('to') if isinstance(e, Mapping) else None
            if isinstance(to, str) and to in locations and to not in seen:
                seen.add(to)
                q.append(to)
    return [f'地点 {lid} 从开局地点 {start} 走不到' for lid in locations if lid not in seen]


# --------------------------------------------------------------------------- 主流程


def _lexicon_post(old: Any, merged: Any) -> Any:
    if isinstance(old, Mapping) and isinstance(merged, dict):
        for k in BANNED:
            words = list(old.get(k) or []) + [w for w in merged.get(k) or [] if w not in (old.get(k) or [])]
            if words:
                merged[k] = words
    return merged


def _empty(**kw: str) -> WorldFilesDraft:
    base = {f: '' for f in FILES}
    base.update(kw)
    return WorldFilesDraft(**base)


def _ent_lines(entities: Iterable[Mapping[str, Any]], kind: str) -> list[str]:
    return [f'{e["id"]}｜{e["name"]}' + (f'（又作 {"、".join(e["aliases"])}）' if e.get('aliases') else '')
            + f'｜首见 {e["paragraphs"][0]}' for e in entities if e.get('kind') == kind and e.get('paragraphs')]


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    content = ctx.paths.content
    entities = load_entities(ctx)
    refs = content_refs(content)
    p4 = read_json(ctx.paths.draft('p4')) or {}
    p4_notes = [f'{cid}：{n}' for cid, n in p4.items() if isinstance(n, str) and n.strip()]
    requests: list[Request] = []
    for fld, (fname, kind) in FILES.items():
        seed = (content / fname).read_text(encoding='utf-8') if (content / fname).is_file() else ''
        user = prompting.render(
            'pipeline/p5_user', field=fld, file=fname, seed=seed, entities=_ent_lines(entities, kind),
            refs=sorted(refs.get(kind, set())), notes=p4_notes if kind == 'fact' else [])
        requests.append(Request(f'p5_{fld[:-5]}', user, WorldFilesDraft,
                                Lazy(lambda fld=fld, seed=seed: _empty(**{fld: seed}))))
    outcomes = await ask(ctx, 'P5', requests)
    problems: list[str] = []
    copied: list[str] = []
    planned: list[WriteResult] = []
    drafted: dict[str, Any] = {}
    for fld, (fname, _kind) in FILES.items():
        o = outcomes[f'p5_{fld[:-5]}']
        data, err = parse_yaml_text(getattr(o.obj, fld))
        if err:
            problems.append(f'{fname}：{err}，未写入')
            continue
        if data is None:
            problems.append(f'{fname}：草稿为空，未写入')
            continue
        if not isinstance(data, dict):
            problems.append(f'{fname}：不是映射，未写入')
            continue
        if fld == 'facts_yaml':
            for fid in [f for f, v in data.items() if isinstance(v, Mapping)
                        and set(v.get('tags') or []) & AFTER_OPENING_TAGS]:
                problems.append(f'facts.yaml：{fid} 标的是开局之后的事，不进事实表')
                data.pop(fid)
        copied += copied_text_problems(corpus, data, fname)
        keep = ((), ('terms',)) if fld == 'lexicon_yaml' else ((),)
        wr = plan_yaml(content / fname, data, keep_missing_at=keep,
                       post=_lexicon_post if fld == 'lexicon_yaml' else None)
        planned.append(wr)
        drafted[fname] = wr.data
    # 地图连通、引用齐全（按要写进去的内容查）
    def current(fname: str) -> Mapping[str, Any]:
        d = drafted[fname] if fname in drafted else read_yaml(content / fname)
        return d if isinstance(d, Mapping) else {}

    opening = read_yaml(content / 'opening.yaml') or {}
    start = (opening.get('player') or {}).get('loc') if isinstance(opening, Mapping) else None
    checks = connectivity(current('locations.yaml'), start)
    have = {
        'location': set(current('locations.yaml')),
        'item': set(current('items.yaml')),
        'fact': set(current('facts.yaml')) | {f for f in refs['fact'] if f.startswith('exit:')},
        'art': set(current('arts.yaml')) | {'literacy'},
    }
    edges = {str(e.get('to')) for loc in current('locations.yaml').values() if isinstance(loc, Mapping)
             for e in loc.get('exits') or [] if isinstance(e, Mapping) and e.get('edge')}
    for cat, ids in refs.items():
        for x in sorted(ids - have[cat] - (edges if cat == 'location' else set())):
            checks.append(f'用到了不存在的{CAT_LABEL[cat]} {x}')
    before, after = trial_validate(content, planned)
    new_issues = [p for p in after if p not in set(before)]
    rep = Report('P5 地点、物件、事实、本事与专名')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    rep.h('地图连通与引用')
    rep.bullets(checks)
    rep.h('逐项差异')
    for wr in planned:
        if wr.status == 'locked':
            rep.add(f'- {rel(wr.path, content)}：locked，未改')
        elif wr.diff():
            rep.add('', f'**{rel(wr.path, content)}**{"（新文件）" if wr.status == "new" else ""}', '')
            rep.bullets(wr.diff())
    rep.h('保留未动')
    rep.bullets([f'{rel(wr.path, content)} {p}：locked' for wr in planned for p in wr.notes.locked]
                + [f'{rel(wr.path, content)} {p}：草稿漏掉，保留原样' for wr in planned for p in wr.notes.kept])
    rep.h('格式问题')
    rep.bullets(problems)
    rep.h('疑似照抄原文')
    rep.bullets(copied)
    rep.h('P7 预演：草稿带来的新问题')
    rep.bullets(new_issues)
    path = rep.write(ctx.paths.report('p5'))
    res = StepResult('p5', report=path, problems=problems + checks + new_issues)
    for wr in planned:
        commit_yaml(wr, 'P5')
        if wr.changed:
            res.written.append(rel(wr.path, ctx.settings.root))
    res.fallbacks = [cid for cid, o in outcomes.items() if o.source == 'rules']
    res.summary = f'写入 {len(res.written)} 个文件'
    ctx.out(f'P5 地点物件事实本事：{res.summary}；连通与引用问题 {len(checks)} 条；P7 预演新问题 {len(new_issues)} 条')
    return res
