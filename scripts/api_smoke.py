#!/usr/bin/env python
"""M0 冒烟脚本（方案 7.4 末尾的清单）。

Jev：
  (a) 一个含 choice、score、noul 三种问题的最小请求，对每个模型各发一次；
      真实响应存成 tests/fixtures/jev/smoke_<model>.json（请求存成 smoke_request.json）。
  (b) 中文问法与英文问法的比较：evals/m0/zh_en_30.yaml 的 30 个标注样例，
      对每个模型各跑中、英两遍；挑 6 个样例重复 5 次看稳定性。报告写进 evals/m0/report.md。
Claude：
  三个模型各一次最小请求；一个 schema 能解析回来；C3 带 between_tools 能流式出字；
  Sonnet 5.5 拒收采样参数；同一前缀第二次调用 cache_read_input_tokens > 0；Batch 能提交、能取回。
  没有 ANTHROPIC_API_KEY 时每项打印 SKIPPED 与原因，继续往下。

只有所有没跳过的检查都通过，退出码才是 0。密钥从不打印。

    .venv/bin/python scripts/api_smoke.py [--skip-claude] [--skip-eval] [--models jev-1.13.0,jev-1.14.0]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from tianlong.config import Settings, load_settings  # noqa: E402
from tianlong.llm.claude import BatchItem, CallMeta, ClaudeClient  # noqa: E402
from tianlong.llm.cost import Ledger  # noqa: E402
from tianlong.llm.errors import LLMError  # noqa: E402
from tianlong.llm.jev import Answers, ChoiceAnswer, JevClient, NoulAnswer, validate_response  # noqa: E402
from tianlong.llm.record import Recorder  # noqa: E402
from tianlong.llm.schemas import Intent, OptionsOut  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures' / 'jev'
DATASET = ROOT / 'evals' / 'm0' / 'zh_en_30.yaml'
REPORT = ROOT / 'evals' / 'm0' / 'report.md'
RESULTS_JSON = ROOT / 'evals' / 'm0' / 'results.json'

CLAUDE_MODELS = {'haiku': 'claude-haiku-5-5', 'sonnet': 'claude-sonnet-5-5', 'opus': 'claude-opus-5-5'}


# --------------------------------------------------------------------------- 检查结果


@dataclass
class Check:
    name: str
    status: str  # PASS / FAIL / SKIPPED
    detail: str = ''

    def line(self) -> str:
        return f'{self.status:8s} {self.name}' + (f' — {self.detail}' if self.detail else '')


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, c: Check) -> Check:
        self.checks.append(c)
        print(c.line(), flush=True)
        return c

    @property
    def ok(self) -> bool:
        return all(c.status != 'FAIL' for c in self.checks)


async def run_check(rep: Report, name: str, fn: Callable[[], Awaitable[str]]) -> None:
    try:
        detail = await fn()
    except Exception as e:  # noqa: BLE001 — 冒烟脚本要把任何失败记下来接着跑
        rep.add(Check(name, 'FAIL', f'{type(e).__name__}: {e}'))
    else:
        rep.add(Check(name, 'PASS', detail))


# --------------------------------------------------------------------------- Jev (a)

MINIMAL_STATE = (
    '【你是谁】龚光杰，无量剑东宗弟子。心高气傲。\n'
    '【此刻】第一日巳时，剑湖宫练武厅。场面紧张。\n'
    '【在场】段公子（不熟；不会武功）｜左子穆（你的师父）\n'
    '【刚才】你和师弟比剑，正占上风。\n'
)
MINIMAL_QUESTIONS: dict[str, Any] = {
    'action': {
        'type': 'choice',
        'instructions': '此刻这个人会做哪一件事？',
        'criteria': {'a01': '卖个破绽，一剑逼对手认输', 'a02': '收剑退后', 'none': '什么也不做'},
    },
    'speak': {'type': 'noul', 'instructions': '这个人此刻会开口说话。'},
    'weight': {
        'type': 'score',
        'instructions': '这一步对眼前的局面有多大分量？',
        'criteria': ['无关紧要', '有些用处', '会改变局面', '性命攸关'],
    },
}


def eval_settings(settings: Settings, *, timeout_s: float, parallel: int) -> Settings:
    s = settings.model_copy(deep=True)
    s.jev.timeout_s = timeout_s
    s.jev.max_parallel = parallel
    s.root = settings.root
    return s


async def jev_minimal(rep: Report, settings: Settings, model: str, recorder: Recorder) -> None:
    client = JevClient(settings, recorder=recorder, ledger=Ledger.from_settings(settings), model=model)
    spec = client.spec('J2')
    body = client.build_request(spec, MINIMAL_STATE, MINIMAL_QUESTIONS)

    async def go() -> str:
        try:
            data, ms = await client.post(spec, body)
        finally:
            await client.aclose()
        answers = validate_response(data, MINIMAL_QUESTIONS)
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / 'smoke_request.json').write_text(
            json.dumps({'state': MINIMAL_STATE, 'questions': MINIMAL_QUESTIONS}, ensure_ascii=False, indent=2)
            + '\n', encoding='utf-8')
        out = FIXTURES / f'smoke_{model}.json'
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        a = answers['action']
        assert isinstance(a, ChoiceAnswer)
        return (f'{ms:.0f} ms; served by {data.get("model")}; action={a.top} (conf {a.confidence:.2f}); '
                f'usage={data.get("usage")}; saved {out.relative_to(ROOT)}')

    await run_check(rep, f'jev.minimal[{model}]', go)


# --------------------------------------------------------------------------- Jev (b) 中英对比


def build_questions(ds: dict[str, Any], item: dict[str, Any], lang: str) -> dict[str, Any]:
    qdefs = ds['questions']
    out: dict[str, Any] = {}
    for qid in item['labels']:
        q = qdefs[qid]
        loc = q[lang]
        if q['type'] == 'choice':
            if qid == 'action':
                crit = {k: v[lang] for k, v in item['options'].items()}
                crit['none'] = loc['none']
            else:
                crit = dict(loc['criteria'])
            out[qid] = {'type': 'choice', 'instructions': loc['instructions'], 'criteria': crit}
        else:
            out[qid] = {'type': 'noul', 'instructions': loc['instructions']}
    return out


@dataclass
class Pred:
    item: str
    family: str
    qid: str
    label: Any
    pred: Any
    p: float  # 所选项的概率，或 noul 值
    conf: float
    p_label: float  # 模型给标注答案的概率
    gated: bool  # 过了门槛（choice：confidence 与 margin；noul：≥0.7 或 ≤0.3）

    @property
    def correct(self) -> bool:
        return self.pred == self.label


def to_preds(item: dict[str, Any], answers: Answers, settings: Settings) -> list[Pred]:
    th = settings.jev.thresholds
    out: list[Pred] = []
    for qid, label in item['labels'].items():
        a = answers[qid]
        if isinstance(a, ChoiceAnswer):
            gated = a.confidence >= th.choice_confidence and a.margin >= th.choice_margin
            out.append(Pred(item['id'], item['family'], qid, label, a.top, a.top_p, a.confidence,
                            a.p(str(label)), gated))
        elif isinstance(a, NoulAnswer):
            p = a.noul
            out.append(Pred(item['id'], item['family'], qid, bool(label), p > 0.5, p, a.confidence,
                            p if label else 1.0 - p, p >= 0.7 or p <= 0.3))
    return out


@dataclass
class RunResult:
    model: str
    lang: str
    preds: list[Pred] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_eval(settings: Settings, ds: dict[str, Any], model: str, lang: str, recorder: Recorder,
                   items: list[dict[str, Any]], repeats: int = 1) -> list[RunResult]:
    client = JevClient(settings, recorder=recorder, ledger=Ledger.from_settings(settings), model=model)
    runs = [RunResult(model, lang) for _ in range(repeats)]

    async def one(rep_i: int, item: dict[str, Any]) -> None:
        qs = build_questions(ds, item, lang)
        kind = item['family']
        t0 = time.perf_counter()
        try:
            ans = await client.ask(kind, item['state'].strip(), qs)
        except LLMError as e:
            runs[rep_i].errors.append(f'{item["id"]}: {e}')
            return
        runs[rep_i].latencies.append((time.perf_counter() - t0) * 1000.0)
        runs[rep_i].preds.extend(to_preds(item, ans, settings))

    try:
        await asyncio.gather(*(one(r, it) for r in range(repeats) for it in items))
    finally:
        await client.aclose()
    for r in runs:
        r.preds.sort(key=lambda p: (p.item, p.qid))
    return runs


def acc(preds: list[Pred]) -> float:
    return sum(p.correct for p in preds) / len(preds) if preds else float('nan')


def pct(x: float) -> str:
    return '—' if x != x else f'{100 * x:.1f}%'


def summarize(r: RunResult) -> dict[str, Any]:
    fam: dict[str, list[Pred]] = defaultdict(list)
    for p in r.preds:
        fam[p.family].append(p)
    gated = [p for p in r.preds if p.gated]
    lat = sorted(r.latencies)
    return {
        'model': r.model,
        'lang': r.lang,
        'n': len(r.preds),
        'correct': sum(p.correct for p in r.preds),
        'accuracy': acc(r.preds),
        'by_family': {f: acc(ps) for f, ps in sorted(fam.items())},
        'mean_conf': statistics.fmean(p.conf for p in r.preds) if r.preds else float('nan'),
        'p_label': statistics.fmean(p.p_label for p in r.preds) if r.preds else float('nan'),
        'log_loss': statistics.fmean(-math.log(max(p.p_label, 1e-3)) for p in r.preds) if r.preds else float('nan'),
        'gated_cov': len(gated) / len(r.preds) if r.preds else float('nan'),
        'gated_acc': acc(gated),
        'lat_med': statistics.median(lat) if lat else float('nan'),
        'lat_p95': lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else float('nan'),
        'over_2s': sum(x > 2000 for x in lat),
        'errors': len(r.errors),
    }


def agreement(a: RunResult, b: RunResult) -> float:
    pa = {(p.item, p.qid): p.pred for p in a.preds}
    pb = {(p.item, p.qid): p.pred for p in b.preds}
    keys = pa.keys() & pb.keys()
    return sum(pa[k] == pb[k] for k in keys) / len(keys) if keys else float('nan')


def stability(runs: list[RunResult]) -> dict[str, float]:
    by_key: dict[tuple[str, str], list[Pred]] = defaultdict(list)
    for r in runs:
        for p in r.preds:
            by_key[(p.item, p.qid)].append(p)
    same = 0
    stds: list[float] = []
    for ps in by_key.values():
        if len({p.pred for p in ps}) == 1:
            same += 1
        if len(ps) > 1:
            stds.append(statistics.pstdev(p.p for p in ps))
    return {
        'questions': len(by_key),
        'identical_rate': same / len(by_key) if by_key else float('nan'),
        'mean_std': statistics.fmean(stds) if stds else float('nan'),
        'max_std': max(stds) if stds else float('nan'),
    }


def write_report(ds: dict[str, Any], main: dict[tuple[str, str], RunResult],
                 stab: dict[tuple[str, str], dict[str, float]], minimal: list[Check]) -> dict[str, Any]:
    models = sorted({m for m, _ in main})
    sums = {k: summarize(v) for k, v in main.items()}
    best_key = max(sums, key=lambda k: (sums[k]['correct'], round(sums[k]['p_label'], 3)))
    best = sums[best_key]
    lines: list[str] = []
    w = lines.append
    w('# M0：Jev 冒烟与中英问法比较')
    w('')
    w(f'- 生成：`scripts/api_smoke.py`，{dt.date.today().isoformat()}。数据集：`evals/m0/zh_en_30.yaml`'
      f'（{len(ds["items"])} 个样例，{best["n"]} 道标注题：J1 输入归类、J2 角色抉择、J5 叙述核对）。')
    w('- 状态一律中文；只换问题与选项描述的语言。每个样例一个请求，题目并行作答。')
    w('- 判定：choice 取概率最高的选项；noul 以 0.5 为界。「过门槛」：choice 的 confidence ≥ '
      '`choice_confidence` 且头两名差 ≥ `choice_margin`；noul ≥ 0.7 或 ≤ 0.3。')
    w('- 时延按脚本里的超时 15 秒计（游戏里是 2 秒）；「>2s」是超过游戏超时的请求数。')
    w('- 「标注答案的概率」：模型给正确答案的概率的均值（choice 取该项概率，noul 取 p 或 1−p），'
      '对数损失同理；准确率打平时用它分高下。排名：先比答对题数，再比这一项。')
    w('')
    w('## 最小请求')
    w('')
    for c in minimal:
        w(f'- `{c.name}`：{c.status}。{c.detail}')
    w('')
    w('## 准确率')
    w('')
    w('| 模型 | 问法 | 准确率 | J1 | J2 | J5 | 标注答案的概率 | 对数损失 | 平均置信 | 过门槛占比 | 过门槛准确率 '
      '| 时延中位 | P95 | >2s | 失败 |')
    w('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for k in sorted(sums):
        s = sums[k]
        bf = s['by_family']
        w(f'| {s["model"]} | {s["lang"]} | {pct(s["accuracy"])} ({s["correct"]}/{s["n"]}) | '
          f'{pct(bf.get("J1", float("nan")))} | {pct(bf.get("J2", float("nan")))} | '
          f'{pct(bf.get("J5", float("nan")))} | {s["p_label"]:.3f} | {s["log_loss"]:.3f} | '
          f'{s["mean_conf"]:.2f} | {pct(s["gated_cov"])} | '
          f'{pct(s["gated_acc"])} | {s["lat_med"]:.0f} ms | {s["lat_p95"]:.0f} ms | {s["over_2s"]} | '
          f'{s["errors"]} |')
    w('')
    w('## 一致率')
    w('')
    w('| 比较 | 判定相同的题 |')
    w('| --- | --- |')
    for m in models:
        if (m, 'zh') in main and (m, 'en') in main:
            w(f'| {m}：中文 vs 英文 | {pct(agreement(main[(m, "zh")], main[(m, "en")]))} |')
    if len(models) == 2:
        for lang in ('zh', 'en'):
            if (models[0], lang) in main and (models[1], lang) in main:
                w(f'| {lang}：{models[0]} vs {models[1]} | '
                  f'{pct(agreement(main[(models[0], lang)], main[(models[1], lang)]))} |')
    w('')
    w('## 稳定性（同一请求重复 5 次）')
    w('')
    w(f'样例：{", ".join(ds.get("stability", []))}。')
    w('')
    w('| 模型 | 问法 | 题数 | 5 次判定全同 | 概率的标准差（均值 / 最大） |')
    w('| --- | --- | --- | --- | --- |')
    for k in sorted(stab):
        st = stab[k]
        w(f'| {k[0]} | {k[1]} | {st["questions"]} | {pct(st["identical_rate"])} | '
          f'{st["mean_std"]:.3f} / {st["max_std"]:.3f} |')
    w('')
    w('## 答错的题')
    w('')
    miss = Counter((p.item, p.qid) for r in main.values() for p in r.preds if not p.correct)
    always = sorted(k for k, n in miss.items() if n == len(main))
    if always:
        w('所有组合都答错的题（问题本身的写法有毛病，换语言、换版本都救不回来）：'
          + '、'.join(f'`{i}.{q}`' for i, q in always) + '。')
        w('')
    for k in sorted(main):
        wrong = [p for p in main[k].preds if not p.correct]
        if not wrong:
            continue
        w(f'**{k[0]} · {k[1]}**')
        w('')
        for p in wrong:
            w(f'- `{p.item}.{p.qid}`：标注 {p.label}，答 {p.pred}（{p.p:.2f}）')
        w('')
        for e in main[k].errors:
            w(f'- 失败：{e}')
    w('## 结论')
    w('')
    ranked = sorted(sums.values(), key=lambda s: (-s['correct'], -round(s['p_label'], 3)))
    runner = ranked[1] if len(ranked) > 1 else None
    w(f'- 排第一的组合：**{best["model"]} + {best["lang"]}**，准确率 {pct(best["accuracy"])}，'
      f'标注答案的概率 {best["p_label"]:.3f}'
      + (f'；第二名 {runner["model"]} + {runner["lang"]}，准确率 {pct(runner["accuracy"])}'
         f'（差 {100 * (best["accuracy"] - runner["accuracy"]):.1f} 个点），标注答案的概率 '
         f'{runner["p_label"]:.3f}（差 {best["p_label"] - runner["p_label"]:+.3f}）。' if runner else '。'))
    for lang in ('zh', 'en'):
        if len(models) == 2 and (models[0], lang) in sums and (models[1], lang) in sums:
            a, b = sums[(models[0], lang)], sums[(models[1], lang)]
            w(f'- {lang}：{models[1]} 比 {models[0]} 准确率 {100 * (b["accuracy"] - a["accuracy"]):+.1f} 个点，'
              f'标注答案的概率 {b["p_label"] - a["p_label"]:+.3f}。')
    for m in models:
        if (m, 'zh') in sums and (m, 'en') in sums:
            d = sums[(m, 'zh')]['accuracy'] - sums[(m, 'en')]['accuracy']
            w(f'- {m}：中文 {pct(sums[(m, "zh")]["accuracy"])}，英文 {pct(sums[(m, "en")]["accuracy"])}'
              f'（中文 {"高" if d >= 0 else "低"} {abs(100 * d):.1f} 个点）；'
              f'标注答案的概率 中 {sums[(m, "zh")]["p_label"]:.3f} / 英 {sums[(m, "en")]["p_label"]:.3f}；'
              f'平均置信 中 {sums[(m, "zh")]["mean_conf"]:.2f} / 英 {sums[(m, "en")]["mean_conf"]:.2f}。')
    w('- 样本只有 30 个，差一两题就是几个点；M3 的 300 条归类集与抉择集再定门槛。')
    w('')
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    data = {
        'summary': {f'{k[0]}|{k[1]}': v for k, v in sums.items()},
        'stability': {f'{k[0]}|{k[1]}': v for k, v in stab.items()},
        'best': {'model': best['model'], 'lang': best['lang']},
        'preds': {f'{k[0]}|{k[1]}': [p.__dict__ | {'correct': p.correct} for p in v.preds]
                  for k, v in main.items()},
    }
    RESULTS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return data


async def jev_part(rep: Report, settings: Settings, models: list[str], args: argparse.Namespace,
                   recorder: Recorder) -> None:
    src = settings.jev_key_source()
    if src is None:
        for m in models:
            rep.add(Check(f'jev.minimal[{m}]', 'SKIPPED', 'no Jev key in env '
                          f'({", ".join([settings.jev.api_key_env, *settings.jev.api_key_env_fallbacks])})'))
        rep.add(Check('jev.zh_en_30', 'SKIPPED', 'no Jev key'))
        return
    print(f'Jev key found in env var {src} (value not shown)', flush=True)
    es = eval_settings(settings, timeout_s=args.timeout, parallel=args.concurrency)
    start = len(rep.checks)
    for m in models:
        await jev_minimal(rep, es, m, recorder)
    minimal = rep.checks[start:]
    if args.skip_eval:
        rep.add(Check('jev.zh_en_30', 'SKIPPED', '--skip-eval'))
        return
    ds = yaml.safe_load(DATASET.read_text(encoding='utf-8'))
    items: list[dict[str, Any]] = ds['items']
    main: dict[tuple[str, str], RunResult] = {}
    stab: dict[tuple[str, str], dict[str, float]] = {}
    stab_items = [it for it in items if it['id'] in set(ds.get('stability', []))]
    for m in models:
        for lang in ('zh', 'en'):
            (main[(m, lang)],) = await run_eval(es, ds, m, lang, recorder, items)
            s = summarize(main[(m, lang)])
            print(f'  {m} {lang}: {pct(s["accuracy"])} ({s["correct"]}/{s["n"]}), errors {s["errors"]}',
                  flush=True)
            if args.repeats > 1 and stab_items:
                runs = await run_eval(es, ds, m, lang, recorder, stab_items, repeats=args.repeats)
                stab[(m, lang)] = stability(runs)
    data = write_report(ds, main, stab, minimal)
    errs = sum(len(r.errors) for r in main.values())
    total = sum(len(r.preds) for r in main.values())
    detail = (f'best {data["best"]["model"]} + {data["best"]["lang"]}; '
              f'{total} answers, {errs} failed requests; report {REPORT.relative_to(ROOT)}')
    rep.add(Check('jev.zh_en_30', 'PASS' if total and errs <= 0.05 * len(items) * len(main) else 'FAIL', detail))


# --------------------------------------------------------------------------- Claude


class SmokeOut(BaseModel):
    ok: bool
    word: str


def long_rules() -> str:
    """一段够长的固定 system（缓存至少要 1024 到 4096 token 的前缀）。"""
    rows = [f'第{i}条：凡涉及第{i}号地点的叙述，只写看得见、听得到的东西，不写旁人的心思，不替玩家做主，'
            f'称呼只用称呼表里的叫法，不出现阿拉伯数字与游戏术语。' for i in range(1, 161)]
    return '你在一部武侠文字游戏里做改写。下面是固定的规则。\n' + '\n'.join(rows)


async def claude_part(rep: Report, settings: Settings, args: argparse.Namespace, recorder: Recorder) -> None:
    names = [f'claude.minimal[{m}]' for m in CLAUDE_MODELS.values()] + [
        'claude.schema_parse[C1]', 'claude.stream_between_tools[C3]', 'claude.sampling_rejected[sonnet-5-5]',
        'claude.cache_read[C4]', 'claude.batch[P1]']
    if args.skip_claude:
        for n in names:
            rep.add(Check(n, 'SKIPPED', '--skip-claude'))
        return
    if settings.claude_api_key() is None:
        for n in names:
            rep.add(Check(n, 'SKIPPED', f'{settings.claude.api_key_env} not set in env'))
        return
    import anthropic

    api = anthropic.AsyncAnthropic(api_key=settings.claude_api_key(), max_retries=settings.claude.max_retries)
    ledger = Ledger.from_settings(settings)
    client = ClaudeClient(settings, recorder=recorder, ledger=ledger, client=api)

    for model in CLAUDE_MODELS.values():
        async def minimal(model: str = model) -> str:
            msg = await api.messages.create(
                model=model, max_tokens=256, output_config={'effort': 'low'},
                messages=[{'role': 'user', 'content': '用四个字回答：今天天气如何？'}])
            text = ''.join(b.text for b in msg.content if b.type == 'text')
            assert msg.stop_reason == 'end_turn', f'stop_reason {msg.stop_reason}'
            assert text.strip(), 'empty text'
            return f'{msg.model}: {text.strip()[:20]!r}; usage in={msg.usage.input_tokens} out={msg.usage.output_tokens}'
        await run_check(rep, f'claude.minimal[{model}]', minimal)

    async def schema() -> str:
        obj, meta = await client.call_json(
            'C1', ['你在一部武侠文字游戏里做输入解析，把玩家的一句话对到候选上。'],
            '场景：练武厅。候选动词：go、say、look。玩家输入：「我四下看看。」', Intent)
        assert isinstance(obj, Intent)
        return f'kind={obj.kind} verb={obj.verb}; {meta.model}, {meta.latency_ms:.0f} ms, ${meta.cost:.5f}'
    await run_check(rep, 'claude.schema_parse[C1]', schema)

    async def stream() -> str:
        metas: list[CallMeta] = []
        pieces: list[str] = []
        async for p in client.stream_text(
                'C3', ['你是一部武侠文字游戏的叙述者。用第二人称写。', '文风：白描，句子短。'],
                '<发生的事>你推开门，走进练武厅。</发生的事><字数>40 到 80</字数>', meta_out=metas):
            pieces.append(p)
        m = metas[0]
        assert pieces and ''.join(pieces).strip(), 'no text streamed'
        return (f'{len(pieces)} pieces, first token {m.first_token_ms:.0f} ms, stop {m.stop_reason}, '
                f'thinking={client.spec("C3").thinking}')
    await run_check(rep, 'claude.stream_between_tools[C3]', stream)

    async def sampling() -> str:
        try:
            await api.messages.create(model=CLAUDE_MODELS['sonnet'], max_tokens=32,
                                      messages=[{'role': 'user', 'content': '你好'}],
                                      extra_body={'temperature': 0.5})
        except anthropic.BadRequestError as e:
            return f'400 as expected: {str(e.message)[:80]}'
        raise AssertionError('request with temperature was accepted')
    await run_check(rep, 'claude.sampling_rejected[sonnet-5-5]', sampling)

    async def cache() -> str:
        system = [long_rules(), '称呼表：段公子、钟姑娘。']
        user = '把这三个动作各写成一行选项：a01 走到门口；a02 问段公子；a03 坐下歇脚。'
        default = OptionsOut(options=[])
        _, m1 = await client.call_json('C4', system, user, OptionsOut, mock_default=default)
        _, m2 = await client.call_json('C4', system, user, OptionsOut, mock_default=default)
        r1 = int(m1.usage.get('cache_creation_input_tokens') or 0)
        r2 = int(m2.usage.get('cache_read_input_tokens') or 0)
        assert r2 > 0, f'second call cache_read_input_tokens = {r2} (first wrote {r1})'
        return f'first wrote {r1}, second read {r2}'
    await run_check(rep, 'claude.cache_read[C4]', cache)

    async def batch() -> str:
        items = [BatchItem(custom_id=f'smoke-{i}', system_blocks=['只回答 JSON。'],
                           user_text=f'返回 ok=true，word 填一个与数字 {i} 有关的汉字。', schema=SmokeOut)
                 for i in range(2)]
        bid = await client.batch_submit('P1', items)
        deadline = time.monotonic() + args.batch_timeout
        while True:
            st = await client.batch_poll(bid)
            if st.ended:
                break
            if time.monotonic() > deadline:
                raise AssertionError(f'batch {bid} not ended after {args.batch_timeout}s ({st.processing_status})')
            await asyncio.sleep(15)
        res = await client.batch_results(bid, 'P1')
        bad = [r.custom_id for r in res.values() if not r.ok]
        assert len(res) == 2 and not bad, f'results {len(res)}, failed {bad}'
        return f'batch {bid}: {len(res)} results ok'
    await run_check(rep, 'claude.batch[P1]', batch)
    await api.close()
    print(f'Claude smoke spent about ${ledger.spent:.4f}', flush=True)


# --------------------------------------------------------------------------- main


async def amain(args: argparse.Namespace) -> int:
    settings = load_settings(ROOT / args.config)
    models = [m.strip() for m in args.models.split(',') if m.strip()]
    rec_path = Path(args.record_dir) / 'calls.jsonl' if args.record_dir else None
    recorder = Recorder(rec_path)
    rep = Report()
    print('== Jev ==', flush=True)
    await jev_part(rep, settings, models, args, recorder)
    print('== Claude ==', flush=True)
    await claude_part(rep, settings, args, recorder)
    counts = Counter(c.status for c in rep.checks)
    print(f'\n{counts.get("PASS", 0)} passed, {counts.get("FAIL", 0)} failed, {counts.get("SKIPPED", 0)} skipped')
    return 0 if rep.ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default='config.toml')
    ap.add_argument('--models', default='jev-1.13.0,jev-1.14.0')
    ap.add_argument('--skip-claude', action='store_true')
    ap.add_argument('--skip-eval', action='store_true')
    ap.add_argument('--repeats', type=int, default=5)
    ap.add_argument('--concurrency', type=int, default=8)
    ap.add_argument('--timeout', type=float, default=15.0, help='Jev 超时（秒），评测时放宽，免得丢样本')
    ap.add_argument('--batch-timeout', type=float, default=900.0)
    ap.add_argument('--record-dir', default=None, help='把调用录进 <dir>/calls.jsonl；默认不落盘')
    args = ap.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == '__main__':
    main()
