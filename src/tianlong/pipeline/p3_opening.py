"""P3 开局与走向（方案 9.1，管线里最要紧的一步）：时间线加种子 → `content/opening.yaml`、
`content/agendas/*.yaml`、`content/situations/*.yaml`、`content/baseline.yaml`，以及 `build/diff_report.md`。

一步拆成几条请求，都用 `OpeningDraft`，每条只填其中几个字段（免得一次输出超长）：
- `p3_opening`：开局快照、参照点、方案 3.6 的八条结论、与种子不符之处的说明；
- `p3_agendas_NN`：种子里已有的打算，每条请求一组；`p3_agendas_new`：开局在场、却还没有打算的人；
- `p3_situations_NN`：种子里已有的情境，每条一组；`p3_situations_new`：种子里没有的 canon 情境。

写入前：opening 里各人的 `knows` 只留开局那一刻为真的事；情境的 `paragraphs` 只留有效编号；带 `locked: true`
的条目、草稿漏掉的条目都保留原样。差异报告列出八条的段落编号与结论、与现有种子的每一处差异（程序逐项比对），
以及要人工过目的东西：没写前提的步骤、没写开场条件的情境、删掉的 knows、疑似照抄、P7 预演出的新问题。
规则草稿（Mock 与兜底）就是现有的种子本身，八条结论按 `prompts/pipeline/checks.md` 的关键词在原文里找段落。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from tianlong import prompting
from tianlong.llm.schemas import CheckConclusion, NamedYaml, OpeningDraft
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    Lazy,
    Outcome,
    Report,
    Request,
    StepResult,
    WriteResult,
    ask,
    commit_yaml,
    copied_text_problems,
    fact_ids,
    filter_knows,
    norm_id,
    outcome_lines,
    parse_yaml_text,
    plan_yaml,
    rel,
    valid_paras,
    write_json,
)
from tianlong.pipeline.p1_entities import load_entities
from tianlong.pipeline.p2_timeline import load_timeline
from tianlong.pipeline.p7_validate import trial_validate

GROUP = 6
OPENING_KEEP = ((), ('chars',), ('extras',))


# --------------------------------------------------------------------------- 方案 3.6 的八条


def load_checks() -> list[dict[str, Any]]:
    data = yaml.safe_load(prompting.load('pipeline/checks')) or []
    return [{'item': str(d['item']), 'keywords': [str(k) for k in d.get('keywords') or []]} for d in data]


def rule_checks(corpus: Corpus, checks: Sequence[Mapping[str, Any]]) -> list[CheckConclusion]:
    out: list[CheckConclusion] = []
    for i, c in enumerate(checks, 1):
        kws = list(c['keywords'])
        need = 2 if len(kws) >= 2 else 1
        scored = []
        for p in corpus.paragraphs:
            k = sum(1 for w in kws if w in p.text)
            if k >= need:
                scored.append((-k, corpus.order(p.id), p.id))
        paras = [pid for _, _, pid in sorted(scored)[:6]]
        paras.sort(key=corpus.order)
        if paras:
            concl = '规则草稿，未经模型核对：下列段落含相关字眼，请人工查看后下结论。'
        else:
            concl = '规则草稿，未经模型核对：原文里没找到含相关字眼的段落，待人工核对。'
        out.append(CheckConclusion(item=f'{i}. {c["item"]}', paragraphs=paras, conclusion=concl))
    return out


_NUM_RE = re.compile(r'^\s*(?:第)?\s*(\d+)')


def match_checks(
    corpus: Corpus, items: Sequence[Mapping[str, Any]], got: Sequence[CheckConclusion]
) -> tuple[list[dict[str, Any]], list[str]]:
    """把模型给的结论对到八条上：先看开头的序号，再看与条目文字的重合。缺的补上「未给出」。"""
    problems: list[str] = []
    slots: dict[int, CheckConclusion] = {}
    for c in got:
        idx: int | None = None
        m = _NUM_RE.match(c.item)
        if m and 1 <= int(m.group(1)) <= len(items):
            idx = int(m.group(1)) - 1
        else:
            best = max(range(len(items)), key=lambda i: len(set(items[i]['item']) & set(c.item)), default=None)
            if best is not None and len(set(items[best]['item']) & set(c.item)) >= 4:
                idx = best
        if idx is None:
            problems.append(f'对不上八条中任何一条的结论：「{c.item[:30]}」')
            continue
        slots.setdefault(idx, c)
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        c = slots.get(i)
        if c is None:
            out.append({'n': i + 1, 'item': it['item'], 'paragraphs': [], 'conclusion': '模型未给出结论，待人工核对。'})
            problems.append(f'第 {i + 1} 条没有结论')
            continue
        good, bad = valid_paras(corpus, c.paragraphs)
        if bad:
            problems.append(f'第 {i + 1} 条丢掉无效的段落编号 {", ".join(bad)}')
        if not good:
            problems.append(f'第 {i + 1} 条没有有效的段落编号')
        out.append({'n': i + 1, 'item': it['item'], 'paragraphs': good, 'conclusion': c.conclusion.strip()})
    return out, problems


# --------------------------------------------------------------------------- 种子


def _read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def seed_files(content: Path, sub: str) -> dict[str, str]:
    d = content / sub
    if not d.is_dir():
        return {}
    return {p.stem: p.read_text(encoding='utf-8') for p in sorted(d.glob('*.yaml')) if not p.name.startswith('.')}


def _groups(ids: Sequence[str], n: int = GROUP) -> list[list[str]]:
    return [list(ids[i:i + n]) for i in range(0, len(ids), n)]


# --------------------------------------------------------------------------- 校验


def agenda_review(aid: str, data: Any) -> tuple[list[str], list[str]]:
    """(格式问题, 要人工过目的：没写前提的步骤)。"""
    probs: list[str] = []
    review: list[str] = []
    if not isinstance(data, Mapping):
        return [f'agendas/{aid}.yaml：不是映射'], []
    steps = data.get('steps')
    if not isinstance(steps, list) or not steps:
        probs.append(f'agendas/{aid}.yaml：没有 steps')
        return probs, review
    for i, st in enumerate(steps):
        if not isinstance(st, Mapping):
            probs.append(f'agendas/{aid}.yaml：第 {i + 1} 步不是映射')
            continue
        sid = st.get('id', f'#{i + 1}')
        if not st.get('id') or not st.get('text') or not isinstance(st.get('do'), Mapping):
            probs.append(f'agendas/{aid}.yaml：步骤 {sid} 缺 id、text 或 do')
        if st.get('pre') in (None, True, {}):
            review.append(f'agendas/{aid}.yaml：步骤 {sid}（{st.get("text", "")}）没写前提')
    return probs, review


def situation_review(sid: str, data: Any, corpus: Corpus) -> tuple[list[str], list[str]]:
    probs: list[str] = []
    review: list[str] = []
    if not isinstance(data, Mapping):
        return [f'situations/{sid}.yaml：不是映射'], []
    if data.get('when') is None:
        review.append(f'situations/{sid}.yaml：没写开场条件 when')
    if not data.get('roles'):
        probs.append(f'situations/{sid}.yaml：没有角色位 roles')
    if 'paragraphs' in data and isinstance(data, dict):
        good, bad = valid_paras(corpus, [p for p in data.get('paragraphs') or [] if isinstance(p, str)])
        if bad:
            probs.append(f'situations/{sid}.yaml：丢掉无效的段落编号 {", ".join(bad)}')
        data['paragraphs'] = good
    return probs, review


def baseline_entries(data: Any) -> tuple[list[dict[str, Any]], list[str]]:
    probs: list[str] = []
    out: list[dict[str, Any]] = []
    if not isinstance(data, list):
        return [], ['baseline.yaml：不是列表']
    seen: set[str] = set()
    for i, m in enumerate(data):
        if not isinstance(m, Mapping) or not m.get('id') or 'holds' not in m:
            probs.append(f'baseline[{i}]：缺 id 或 holds，丢掉')
            continue
        if m['id'] in seen:
            probs.append(f'baseline：id {m["id"]} 重复，只留第一条')
            continue
        by = m.get('by')
        if by is not None and not isinstance(by, int):
            probs.append(f'baseline.{m["id"]}：by 不是整数，删去')
            m = {k: v for k, v in m.items() if k != 'by'}
        seen.add(str(m['id']))
        out.append(dict(m))
    return out, probs


def filter_opening_knows(data: dict[str, Any], facts: Mapping[str, Any]) -> list[str]:
    dropped: list[str] = []
    pl = data.get('player')
    if isinstance(pl, dict) and 'knows' in pl:
        pl['knows'], bad = filter_knows(pl.get('knows'), facts)
        dropped += [f'player.knows：{x}' for x in bad]
    for x in data.get('extras') or []:
        if isinstance(x, dict) and 'knows' in x:
            x['knows'], bad = filter_knows(x.get('knows'), facts)
            dropped += [f'extras[{x.get("id")}].knows：{b}' for b in bad]
    return dropped


# --------------------------------------------------------------------------- 主流程


def _timeline_lines(timeline: Sequence[Mapping[str, Any]], limit: int = 600) -> list[str]:
    lines = []
    for e in timeline[:limit]:
        span = e['para_from'] if e['para_from'] == e['para_to'] else f'{e["para_from"]}–{e["para_to"]}'
        where = f' @{e["where"]}' if e.get('where') else ''
        res = f' → {e["result"]}' if e.get('result') else ''
        hint = f'（时间：{e["time_hint"]}）' if e.get('time_hint') else ''
        lines.append(f'[{span}] {",".join(e.get("who") or [])}{where}：{e["what"]}{res}{hint}')
    return lines


def _empty(**kw: Any) -> OpeningDraft:
    base: dict[str, Any] = {'opening_yaml': '', 'agendas': [], 'situations': [], 'baseline_yaml': '', 'checks': [],
                            'diff_report': ''}
    base.update(kw)
    return OpeningDraft(**base)


def build_requests(ctx: Ctx, checks: Sequence[Mapping[str, Any]]) -> list[Request]:
    corpus = ctx.corpus
    content = ctx.paths.content
    entities = load_entities(ctx)
    timeline = load_timeline(ctx)
    opening_text = _read_text(content / 'opening.yaml')
    baseline_text = _read_text(content / 'baseline.yaml')
    agendas = seed_files(content, 'agendas')
    situations = seed_files(content, 'situations')
    ent_lines = [f'{e["id"]}｜{e["kind"]}｜{e["name"]}' for e in entities]
    tl_lines = _timeline_lines(timeline)
    common = {'entities': ent_lines, 'timeline': tl_lines}
    reqs: list[Request] = []

    def add(cid: str, task: str, rule: Any, **kw: Any) -> None:
        user = prompting.render('pipeline/p3_user', task=task, **common, **kw)
        reqs.append(Request(cid, user, OpeningDraft, Lazy(rule)))

    add('p3_opening', 'opening',
        lambda: _empty(opening_yaml=opening_text, baseline_yaml=baseline_text, checks=rule_checks(corpus, checks),
                       diff_report='规则草稿：沿用现有种子，未作改动。'),
        checks=[f'{i}. {c["item"]}' for i, c in enumerate(checks, 1)], opening_yaml=opening_text,
        baseline_yaml=baseline_text, seeds={}, ids=[])
    for k, grp in enumerate(_groups(list(agendas)), 1):
        seeds = {a: agendas[a] for a in grp}
        add(f'p3_agendas_{k:02d}', 'agendas',
            lambda seeds=seeds: _empty(agendas=[NamedYaml(id=a, yaml=t) for a, t in seeds.items()]),
            checks=[], opening_yaml=opening_text, baseline_yaml='', seeds=seeds, ids=list(agendas))
    char_ids = [e['id'] for e in entities if e.get('kind') == 'character' and e['id'] not in agendas
                and e['id'] != 'player']
    if char_ids:
        add('p3_agendas_new', 'agendas_new', lambda: _empty(), checks=[], opening_yaml=opening_text,
            baseline_yaml='', seeds={}, ids=char_ids)
    for k, grp in enumerate(_groups(list(situations)), 1):
        seeds = {s: situations[s] for s in grp}
        add(f'p3_situations_{k:02d}', 'situations',
            lambda seeds=seeds: _empty(situations=[NamedYaml(id=s, yaml=t) for s, t in seeds.items()]),
            checks=[], opening_yaml='', baseline_yaml='', seeds=seeds, ids=list(situations))
    add('p3_situations_new', 'situations_new', lambda: _empty(), checks=[], opening_yaml='', baseline_yaml='',
        seeds={}, ids=list(situations))
    return reqs


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    content = ctx.paths.content
    checks = load_checks()
    notes: list[str] = []
    if not load_timeline(ctx):
        notes.append('没有 build/timeline.json（P2 未运行或为空）：开局与打算只能凭原文和种子')
    requests = build_requests(ctx, checks)
    outcomes: dict[str, Outcome] = await ask(ctx, 'P3', requests)
    write_json(ctx.paths.draft('p3'), {cid: {'source': o.source, 'error': o.error, 'draft': o.obj.model_dump()}
                                       for cid, o in outcomes.items()})

    problems: list[str] = []  # 格式问题（未写入或丢掉的）
    review: list[str] = []  # 要人工过目的
    copied: list[str] = []
    planned: list[WriteResult] = []
    facts = fact_ids(content)

    head = outcomes.get('p3_opening')
    draft = head.obj if head is not None else _empty()
    # 开局快照
    data, err = parse_yaml_text(draft.opening_yaml)
    if err:
        problems.append(f'opening.yaml：{err}，未写入')
    elif isinstance(data, dict):
        dropped = filter_opening_knows(data, facts)
        review += [f'opening.yaml {x}：不在事实表里或是开局之后的事，已删去' for x in dropped]
        copied += copied_text_problems(corpus, data, 'opening.yaml')
        planned.append(plan_yaml(content / 'opening.yaml', data, keep_missing_at=OPENING_KEEP))
    elif data is not None:
        problems.append('opening.yaml：不是映射，未写入')
    # 参照点
    data, err = parse_yaml_text(draft.baseline_yaml)
    if err:
        problems.append(f'baseline.yaml：{err}，未写入')
    elif data is not None:
        marks, probs = baseline_entries(data)
        problems += probs
        copied += copied_text_problems(corpus, marks, 'baseline.yaml')
        if marks:
            planned.append(plan_yaml(content / 'baseline.yaml', marks))
    # 打算与情境
    seen: dict[str, str] = {}
    for kind, sub in (('agendas', 'agendas'), ('situations', 'situations')):
        for cid, o in outcomes.items():
            for ny in getattr(o.obj, kind):
                nid = norm_id(ny.id)
                key = f'{sub}/{nid}'
                if not nid:
                    problems.append(f'{cid}：{kind} 里有一份没有 id，丢掉')
                    continue
                if key in seen:
                    problems.append(f'{key}.yaml 在 {seen[key]} 与 {cid} 里各有一份，取后者')
                    planned = [w for w in planned if w.path != content / sub / f'{nid}.yaml']
                seen[key] = cid
                d, err = parse_yaml_text(ny.yaml)
                if err or not isinstance(d, dict):
                    problems.append(f'{key}.yaml：{err or "不是映射"}，未写入')
                    continue
                if kind == 'agendas':
                    probs, rev = agenda_review(nid, d)
                else:
                    if d.get('id') not in (None, nid):
                        problems.append(f'{key}.yaml：id {d["id"]!r} 改为 {nid!r}（与文件名一致）')
                        d['id'] = nid
                    probs, rev = situation_review(nid, d, corpus)
                problems += probs
                review += rev
                copied += copied_text_problems(corpus, d, f'{key}.yaml', skip=('paragraphs',))
                planned.append(plan_yaml(content / sub / f'{nid}.yaml', d))
    checks_out, check_probs = match_checks(corpus, checks, draft.checks)
    before, after = trial_validate(content, planned)
    new_issues = [p for p in after if p not in set(before)]

    # 差异报告
    rep = Report('P3 差异报告（开局、打算、情境、参照点）')
    rep.add('', '人工过目三处：本报告；每个人的底线与抉择（P4 报告）；每一步打算和每个情境的前提（本报告第三节）。')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    rep.bullets(notes, empty='')
    rep.h('一、方案 3.6 的八条')
    for c in checks_out:
        rep.h(f'{c["n"]}. {c["item"]}', 3)
        rep.add(f'- 段落：{"、".join(c["paragraphs"]) if c["paragraphs"] else "（无）"}',
                f'- 结论：{c["conclusion"]}')
    if check_probs:
        rep.h('八条的校验', 3)
        rep.bullets(check_probs)
    rep.h('二、与现有种子不符之处')
    rep.h('模型的说明', 3)
    texts = [o.obj.diff_report.strip() for o in outcomes.values() if o.obj.diff_report.strip()]
    rep.add(*(texts or ['（无）']))
    rep.h('逐项差异（程序比对现有 content/ 与草稿）', 3)
    any_diff = False
    for wr in planned:
        lines = wr.diff()
        if wr.status == 'locked':
            rep.add(f'- {rel(wr.path, content)}：整份 locked，未改')
            continue
        if not lines:
            continue
        any_diff = True
        rep.add('', f'**{rel(wr.path, content)}**{"（新文件）" if wr.status == "new" else ""}', '')
        rep.bullets(lines)
    if not any_diff:
        rep.add('（草稿与现有内容一致）')
    rep.h('保留未动', 3)
    kept = []
    for wr in planned:
        kept += [f'{rel(wr.path, content)} {p}：locked' for p in wr.notes.locked]
        kept += [f'{rel(wr.path, content)} {p}：草稿漏掉，保留原样' for p in wr.notes.kept]
    for sub in ('agendas', 'situations'):
        for p in sorted((content / sub).glob('*.yaml')) if (content / sub).is_dir() else []:
            if f'{sub}/{p.stem}' not in seen:
                kept.append(f'{sub}/{p.name}：草稿未提及，保留原文件（去留请人工决定）')
    rep.bullets(kept)
    rep.h('三、人工过目')
    rep.h('没写前提的步骤、没写开场条件的情境、删掉的 knows', 3)
    rep.bullets(review)
    rep.h('格式问题（未写入或已丢掉的）', 3)
    rep.bullets(problems)
    rep.h('疑似照抄原文（自己的话里出现成段原文）', 3)
    rep.bullets(copied)
    rep.h('P7 预演：草稿带来的新问题', 3)
    rep.bullets(new_issues)
    path = rep.write(ctx.paths.diff_report)

    res = StepResult('p3', report=path)
    for wr in planned:
        commit_yaml(wr, 'P3')
        if wr.changed:
            res.written.append(rel(wr.path, ctx.settings.root))
    res.problems = notes + problems + check_probs + new_issues
    res.fallbacks = [cid for cid, o in outcomes.items() if o.source == 'rules']
    res.summary = f'写入 {len(res.written)} 个文件；差异报告 {rel(path, ctx.settings.root)}'
    ctx.out(f'P3 开局与走向：{res.summary}；八条结论 {sum(1 for c in checks_out if c["paragraphs"])}/{len(checks_out)}'
            f' 条有段落编号；要人工过目 {len(review)} 处；P7 预演新问题 {len(new_issues)} 条')
    return res
