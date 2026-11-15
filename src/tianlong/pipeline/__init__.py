"""离线内容管线 P0 至 P7（方案 9.1、9.2）：把你自己的原文 `corpus/tianlong.txt` 变成 `content/`。

    python -m tianlong pipeline p0          # p0 … p7，或 all
    code = pipeline.run('p3', settings)      # 同步入口，返回退出码
    code = await pipeline.arun('all', settings, llm=llm)

| 步 | 读 | 写 |
| --- | --- | --- |
| p0 | corpus/tianlong.txt | build/paragraphs.jsonl |
| p1 | 段落 | build/entities.json |
| p2 | 段落、实体 | build/timeline.json |
| p3 | 时间线、种子 | content/opening.yaml、agendas/、situations/、baseline.yaml，build/diff_report.md |
| p4 | 出场段落、种子 | content/characters/ |
| p5 | 段落、种子 | content/locations.yaml、items.yaml、facts.yaml、arts.yaml、lexicon.yaml |
| p6 | 段落 | content/style/guide.md、samples.yaml |
| p7 | content/ | 问题清单；有问题时退出码非零 |

P1 至 P6 用 Opus（`[models.pipeline]`），默认走 Batch；模型失败时那一条改用规则草稿，管线不中断。
`[route] pipeline = 'rules_only'` 时完全不调模型。退出码：0 通过；1 有问题（P7 的校验问题）；2 无法进行
（缺原文、缺上一步的产物、步骤名不对、Batch 还没处理完）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from tianlong.config import Settings
from tianlong.llm import LLMError, make_llm
from tianlong.llm.route import mode
from tianlong.pipeline import (
    p0_split,
    p1_entities,
    p2_timeline,
    p3_opening,
    p4_cards,
    p5_world,
    p6_style,
    p7_validate,
)
from tianlong.pipeline.common import (
    MAX_CHAPTER,
    Ctx,
    Out,
    Paths,
    PipelineError,
    StepResult,
)
from tianlong.pipeline.p7_validate import BaselineHook, validate_content

STEPS = ('p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7')
MODEL_STEPS = frozenset({'p1', 'p2', 'p3', 'p4', 'p5', 'p6'})
DEFAULT_BUDGET_USD = 40.0  # 管线自己的预算；单局的 5 美元护栏不适用于离线管线

_RUNNERS = {
    'p1': p1_entities.run,
    'p2': p2_timeline.run,
    'p3': p3_opening.run,
    'p4': p4_cards.run,
    'p5': p5_world.run,
    'p6': p6_style.run,
}


def _steps(step: str) -> list[str]:
    s = step.strip().lower()
    if s == 'all':
        return list(STEPS)
    if s in STEPS:
        return [s]
    raise PipelineError(f'不认得的步骤 {step!r}：可用 {", ".join(STEPS)} 或 all。')


def _budget(settings: Settings, budget_usd: float | None) -> float:
    if budget_usd is not None:
        return float(budget_usd)
    extra = settings.limits.model_extra or {}
    return float(extra.get('pipeline_budget_usd', DEFAULT_BUDGET_USD))


async def arun(
    step: str,
    settings: Settings,
    *,
    llm: Any = None,
    mock: bool = False,
    batch: bool | None = None,
    corpus: str | Path | None = None,
    build: str | Path | None = None,
    content: str | Path | None = None,
    max_chapter: int = MAX_CHAPTER,
    budget_usd: float | None = None,
    baseline: BaselineHook | None = None,
    poll_interval: float = 30.0,
    out: Out = print,
) -> int:
    """跑一步或全部。返回退出码（0 通过，1 有问题，2 无法进行）。从不因模型失败而抛异常。"""
    try:
        steps = _steps(step)
    except PipelineError as e:
        out(str(e))
        return 2
    paths = Paths.from_settings(settings, corpus=corpus, build=build, content=content)
    spec = settings.models.get('pipeline')
    use_batch = batch if batch is not None else bool(spec.batch if spec is not None else True)
    route = mode(settings, 'pipeline')
    own_llm = False
    if llm is None and route != 'rules_only' and MODEL_STEPS & set(steps):
        run_dir = paths.build / 'calls' / time.strftime('%Y%m%d-%H%M%S')
        llm = make_llm(settings, mock=mock, run_dir=run_dir)
        own_llm = True
        want = _budget(settings, budget_usd)
        if want > llm.ledger.budget_usd:
            llm.ledger.extend(want - llm.ledger.budget_usd)
    ctx = Ctx(settings=settings, paths=paths, llm=llm, batch=use_batch, use_model=route != 'rules_only',
              max_chapter=max_chapter, poll_interval=poll_interval, out=out)
    results: list[StepResult] = []
    code = 0
    try:
        for s in steps:
            try:
                if s == 'p0':
                    r = await p0_split.run(ctx)
                elif s == 'p7':
                    r = await p7_validate.run(ctx, baseline=baseline)
                else:
                    r = await _RUNNERS[s](ctx)
            except PipelineError as e:
                out(f'{s.upper()}：{e}')
                return 2
            except LLMError as e:  # 兜底的兜底：各步本已逐条退让，这里只防漏网
                out(f'{s.upper()}：模型调用失败（{type(e).__name__}: {e}），这一步没有完成；重跑即可。')
                return 2
            except OSError as e:
                out(f'{s.upper()}：读写文件失败：{e}')
                return 2
            results.append(r)
            code = max(code, r.code())
    finally:
        if llm is not None and own_llm:
            try:
                await llm.aclose()
            except Exception:  # 关连接失败不影响结果
                pass
    if llm is not None and MODEL_STEPS & set(steps):
        spent = llm.ledger.summary().get('spent_usd', 0.0)
        out(f'模型花费：${spent:.2f}（{"Batch" if use_batch else "直调"}，{"Mock" if getattr(llm, "mock", False) else "真模型"}）')
    return code


def run(step: str, settings: Settings, *, llm: Any = None, **kwargs: Any) -> int:
    """同步入口（`python -m tianlong pipeline p0` 调它）。已在事件循环里的调用方用 `arun`。"""
    return asyncio.run(arun(step, settings, llm=llm, **kwargs))


__all__ = [
    'STEPS',
    'BaselineHook',
    'Paths',
    'PipelineError',
    'StepResult',
    'arun',
    'run',
    'validate_content',
]
