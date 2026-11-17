"""P6 文风（方案 9.1、9.7）：段落 → `content/style/guide.md`（用自己的话写的文风要点）与
`content/style/samples.yaml`（按场面类型各 3 到 5 段原文短例，带段落编号）。

写入前：短例逐字比对，不是原文子串的丢掉，每类至多 5 条，不足 3 条的记进报告；文风要点里出现成段原文的
那一行删掉（要点必须是自己的话）。`guide.md` 里写了 `<!-- locked -->` 的不覆盖；短例带 `locked: true` 的保留。
规则草稿（Mock 与兜底）：现有的要点原样；现有短例原样，没有就按字眼从原文里挑短段落。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tianlong import prompting
from tianlong.llm.schemas import StyleDraft, StyleSample
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    Lazy,
    Report,
    Request,
    StepResult,
    ask,
    commit_yaml,
    outcome_lines,
    plan_yaml,
    read_yaml,
    rel,
)

SCENES = ('对话', '打斗', '写景', '人物', '紧张')
MIN_PER_SCENE, MAX_PER_SCENE = 3, 5
MAX_SAMPLE_LEN = 160
LOCK_MARK = '<!-- locked -->'
GUIDE_STATUS = {'locked': 'locked，未改', 'written': '改写', 'unchanged': '未变'}
# 只给规则草稿挑段落用的通用字眼
_FIGHT = ('剑', '刀', '掌', '拳', '招', '劈', '刺', '闪', '退', '击', '踢', '架')
_SCENERY = ('山', '风', '云', '雾', '水', '月', '树', '林', '石', '雨', '江', '崖', '花', '草')
_TENSE = ('忽', '突然', '猛', '惊', '急', '喝')


def _excerpt(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    cut = max(text.rfind(c, 0, limit) for c in '。！？')
    return text[:cut + 1] if cut >= 10 else text[:limit]


def _scene_of(text: str) -> str | None:
    quoted = '「' in text or '“' in text
    if sum(w in text for w in _FIGHT) >= 2:
        return '打斗'
    if quoted:
        return '对话'
    if sum(w in text for w in _TENSE) >= 2:
        return '紧张'
    if sum(w in text for w in _SCENERY) >= 2:
        return '写景'
    return None


def rule_samples(corpus: Corpus, existing: Mapping[str, Any]) -> list[StyleSample]:
    out: list[StyleSample] = []
    for scene, items in (existing or {}).items():
        for s in items or []:
            if isinstance(s, Mapping) and s.get('text'):
                out.append(StyleSample(scene=str(scene), text=str(s['text']), para=str(s.get('para') or '')))
    if out:
        return out
    counts: dict[str, int] = {}
    for p in corpus.paragraphs:
        scene = _scene_of(p.text)
        if scene is None or counts.get(scene, 0) >= MIN_PER_SCENE:
            continue
        text = _excerpt(p.text)
        if len(text) < 15:
            continue
        counts[scene] = counts.get(scene, 0) + 1
        out.append(StyleSample(scene=scene, text=text, para=p.id))
    return out


def clean_guide(corpus: Corpus, text: str) -> tuple[str, list[str]]:
    """删掉文风要点里含成段原文的行。"""
    kept: list[str] = []
    dropped: list[str] = []
    for line in text.splitlines():
        runs = corpus.copied_runs(line)
        if runs:
            dropped.append(f'删去含原文「{runs[0][:24]}」的一行')
        else:
            kept.append(line)
    return '\n'.join(kept).strip() + '\n', dropped


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    style_dir = ctx.paths.content / 'style'
    guide_path = style_dir / 'guide.md'
    samples_path = style_dir / 'samples.yaml'
    guide_old = guide_path.read_text(encoding='utf-8') if guide_path.is_file() else ''
    existing = read_yaml(samples_path)
    existing = existing if isinstance(existing, Mapping) else {}
    user = prompting.render('pipeline/p6_user', guide=guide_old, scenes=list(SCENES),
                            min=MIN_PER_SCENE, max=MAX_PER_SCENE, max_len=MAX_SAMPLE_LEN)
    req = Request('p6_style', user, StyleDraft,
                  Lazy(lambda: StyleDraft(guide_md=guide_old, samples=rule_samples(corpus, existing))))
    outcomes = await ask(ctx, 'P6', [req])
    draft: StyleDraft = outcomes['p6_style'].obj
    problems: list[str] = []
    review: list[str] = []
    # 短例
    by_scene: dict[str, list[dict[str, str]]] = {}
    for s in draft.samples:
        text = s.text.strip()
        hit = corpus.verify(text, s.para)
        if hit is None:
            problems.append(f'短例「{text[:24]}」不是原文的子串，丢掉')
            continue
        if hit != s.para:
            problems.append(f'短例「{text[:24]}」的段落编号 {s.para!r} 改为 {hit}')
        if len(text) > MAX_SAMPLE_LEN:
            review.append(f'短例「{text[:24]}」有 {len(text)} 字，偏长')
        lst = by_scene.setdefault(s.scene.strip() or '其他', [])
        if len(lst) >= MAX_PER_SCENE:
            problems.append(f'「{s.scene}」已有 {MAX_PER_SCENE} 条，多的丢掉')
            continue
        if all(x['text'] != text for x in lst):
            lst.append({'text': text, 'para': hit})
    for scene, lst in by_scene.items():
        if len(lst) < MIN_PER_SCENE:
            review.append(f'「{scene}」只有 {len(lst)} 条短例（方案要 3 到 5 条）')
    res = StepResult('p6')
    wr = plan_yaml(samples_path, by_scene) if by_scene else None
    # 文风要点
    guide_new, dropped = clean_guide(corpus, draft.guide_md) if draft.guide_md.strip() else (guide_old, [])
    problems += dropped
    guide_status = 'unchanged'
    if LOCK_MARK in guide_old:
        guide_status = 'locked'
    elif guide_new.strip() and guide_new.strip() != guide_old.strip():
        guide_status = 'written'
    rep = Report('P6 文风')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    rep.h('短例')
    rep.bullets([f'{k}：{len(v)} 条' for k, v in by_scene.items()])
    if wr is not None and wr.diff():
        rep.h('逐项差异（samples.yaml）', 3)
        rep.bullets(wr.diff())
    rep.add('', f'guide.md：{GUIDE_STATUS[guide_status]}')
    rep.h('人工过目')
    rep.bullets(review)
    rep.h('校验（丢掉的短例、删掉的行）')
    rep.bullets(problems)
    res.report = rep.write(ctx.paths.report('p6'))
    if wr is not None:
        commit_yaml(wr, 'P6')
        if wr.changed:
            res.written.append(rel(samples_path, ctx.settings.root))
    if guide_status == 'written':
        style_dir.mkdir(parents=True, exist_ok=True)
        guide_path.write_text(guide_new, encoding='utf-8')
        res.written.append(rel(guide_path, ctx.settings.root))
    res.problems = problems
    res.fallbacks = [cid for cid, o in outcomes.items() if o.source == 'rules']
    res.summary = f'{sum(len(v) for v in by_scene.values())} 条短例，{len(by_scene)} 类'
    ctx.out(f'P6 文风：{res.summary}；丢掉或删去 {len(problems)} 处')
    return res
