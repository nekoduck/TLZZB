"""P7 校验（方案 9.1）：整份 `content/` → 问题清单；有问题时退出码非零。不用模型。

- `tianlong.content.loader.load` 加 `tianlong.content.validate.validate`：引用完整、谓词与效果的语法、
  地图可达、开局时不变量成立。
- 另查管线自己的规矩：人物卡里的台词样例、文风短例必须是原文的子串（有 `build/paragraphs.jsonl` 时才查）；
  开局时谁也不知道标了「开局之后」的事实，也不知道事实表明写「无人知道」（`known_by` 为空）的事实。
- 基线跑（方案 11.3）由上层接进来：`baseline(content_dir)` 返回问题清单（可以是协程）。
"""

from __future__ import annotations

import inspect
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from tianlong.content.loader import ContentError, load
from tianlong.content.validate import validate
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState
from tianlong.pipeline.common import (
    AFTER_OPENING_TAGS,
    Corpus,
    Ctx,
    PipelineError,
    Report,
    StepResult,
    WriteResult,
    dump_yaml,
    load_paragraphs,
    read_yaml,
    rel,
    unknown_at_opening,
)

BaselineHook = Callable[[Path], 'list[str] | Awaitable[list[str]]']


def load_content(content: Path) -> tuple[Defs | None, WorldState | None, list[str]]:
    if not content.is_dir():
        return None, None, [f'{content} 不存在']
    try:
        defs, state = load(content)
    except ContentError as e:
        return None, None, [f'加载失败：{e}']
    except Exception as e:  # 坏内容可能在加载器里任何地方出错：报出来，不打印堆栈
        return None, None, [f'加载失败：{type(e).__name__}: {e}']
    return defs, state, []


def pipeline_checks(
    defs: Defs, state: WorldState, corpus: Corpus | None, nobody: Collection[str] = frozenset()
) -> list[str]:
    """``nobody``：事实表里明写开局时无人知道的事实（加载器不留 known_by，由调用方从 facts.yaml 读来）。"""
    out: list[str] = []
    if corpus is not None and len(corpus):
        for scene, items in defs.style.samples.items():
            for i, s in enumerate(items or []):
                text = s.get('text') if isinstance(s, Mapping) else s
                if corpus.verify(text, s.get('para') if isinstance(s, Mapping) else None) is None:
                    out.append(f'style/samples.yaml {scene}[{i}]：不是原文的子串')
        for cid, card in defs.cards.items():
            for i, s in enumerate(card.voice.samples):
                text = s.get('text') if isinstance(s, Mapping) else s
                if corpus.verify(text, s.get('para') if isinstance(s, Mapping) else None) is None:
                    out.append(f'characters/{cid}.yaml voice.samples[{i}]：不是原文的子串')
    late = {fid for fid, f in state.facts.items() if set(f.tags) & AFTER_OPENING_TAGS}
    for cid, ch in state.chars.items():
        for fid in ch.beliefs:
            if fid in late:
                out.append(f'{cid} 开局就知道 {fid}，而它标的是开局之后的事')
    # state.chars[*].beliefs 已经并入了人物卡、玩家、路人三处的 knows
    for cid, ch in state.chars.items():
        for fid in ch.beliefs:
            if fid in nobody:
                out.append(f'{cid} 开局就知道 {fid}，而事实表写的是开局时无人知道（known_by 为空）')
    return out


def validate_content(content: Path, corpus: Corpus | None = None) -> list[str]:
    """P7 的全部静态检查（不含基线跑）。空表即通过。"""
    defs, state, problems = load_content(content)
    if defs is None or state is None:
        return problems
    try:
        facts = read_yaml(content / 'facts.yaml')
        nobody = unknown_at_opening(facts) if isinstance(facts, Mapping) else set()
        return validate(defs, state) + pipeline_checks(defs, state, corpus, nobody)
    except Exception as e:  # 内容坏到校验器本身出错：照样报成问题
        return [f'校验出错：{type(e).__name__}: {e}']


def trial_validate(content: Path, planned: Sequence[WriteResult]) -> tuple[list[str], list[str]]:
    """P7 预演：在临时副本上写入草稿，跑加载与校验。返回 (写入前的问题, 写入后的问题)。"""
    before = validate_content(content) if content.is_dir() else []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'content'
        if content.is_dir():
            shutil.copytree(content, root)
        else:
            root.mkdir(parents=True)
        for wr in planned:
            if wr.changed:
                target = root / wr.path.relative_to(content)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(dump_yaml(wr.data), encoding='utf-8')
        after = validate_content(root)
    return before, after


async def run(ctx: Ctx, *, baseline: BaselineHook | None = None) -> StepResult:
    content = ctx.paths.content
    corpus: Corpus | None
    try:
        corpus = load_paragraphs(ctx.paths) if ctx.paths.paragraphs.is_file() else None
    except PipelineError:
        corpus = None
    problems = validate_content(content, corpus)
    notes: list[str] = []
    if corpus is None:
        notes.append('没有 build/paragraphs.jsonl：台词样例与文风短例的逐字核对跳过')
    if baseline is not None and not problems:
        got: Any = baseline(content)
        if inspect.isawaitable(got):
            got = await got
        problems += [f'基线跑：{p}' for p in got or []]
    elif baseline is None:
        notes.append('基线跑（方案 11.3）未接入：由 `python -m tianlong baseline --lucky` 另跑')
    rep = Report('P7 校验')
    rep.add('', f'内容目录：{content}')
    rep.h('问题')
    rep.bullets(problems)
    rep.h('说明')
    rep.bullets(notes)
    path: Path | None = None
    try:
        path = rep.write(ctx.paths.report('p7'))
    except OSError:
        path = None
    if problems:
        ctx.out(f'P7 校验：{len(problems)} 个问题')
        for p in problems:
            ctx.out(f'  - {p}')
    else:
        ctx.out(f'P7 校验：通过（{rel(content, ctx.settings.root)}）')
    for n in notes:
        ctx.out(f'  注：{n}')
    return StepResult('p7', ok=not problems, problems=problems, report=path,
                      summary='通过' if not problems else f'{len(problems)} 个问题')
