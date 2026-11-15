"""管线各步共用的东西（方案 9.1、9.2）。

- 路径：`corpus/`（原文，不进版本库）、`build/`（中间产物，不进版本库）、`content/`（进版本库）。
- 原文：读入、切好的段落、编号后的原文块（只取第一至六回，段落编号写在行首）。
- 模型调用：同一批请求走 Batch（半价）或逐条直调；system 是两块，P1 至 P6 共用，缓存 1 小时。
  任何 `LLMError` 都不让管线中断：这一条请求改用规则草稿（与 Mock 的默认值相同），并记进报告。
- 写 YAML：带 `locked: true` 的条目（整份文件、映射里的一项、带 id 的列表里的一项）原样保留；
  管线只增改、不删除：草稿漏掉的条目保留原样；数据没有变化的文件不重写（保住人写的注释）。
- 原文核对：引文必须是原文的子串（逐字），对不上的丢弃；自己的话里不得出现成段的原文。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from tianlong import prompting
from tianlong.config import Settings
from tianlong.kernel.actions import VERBS
from tianlong.kernel.effects import CHECK_KINDS, EFFECT_SHAPES
from tianlong.kernel.predicates import PREDICATE_SHAPES
from tianlong.llm import BatchItem, LLMError

MAX_CHAPTER = 6
CACHE_TTL = '1h'
PARA_ID_RE = re.compile(r'ch\d{2,3}_p\d{4,5}')
COPY_NGRAM = 16  # 自己的话里连续这么多字与原文相同，算照抄

Out = Callable[[str], None]


class PipelineError(Exception):
    """管线的已知错误：打印一句清楚的话，不打印堆栈。"""


class CorpusMissing(PipelineError):
    pass


# --------------------------------------------------------------------------- 路径


@dataclass
class Paths:
    corpus: Path
    build: Path
    content: Path

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        corpus: str | Path | None = None,
        build: str | Path | None = None,
        content: str | Path | None = None,
    ) -> Paths:
        return cls(
            corpus=Path(corpus) if corpus is not None else settings.path('corpus'),
            build=Path(build) if build is not None else settings.path('build'),
            content=Path(content) if content is not None else settings.path('content'),
        )

    @property
    def paragraphs(self) -> Path:
        return self.build / 'paragraphs.jsonl'

    @property
    def entities(self) -> Path:
        return self.build / 'entities.json'

    @property
    def timeline(self) -> Path:
        return self.build / 'timeline.json'

    @property
    def diff_report(self) -> Path:
        return self.build / 'diff_report.md'

    def report(self, step: str) -> Path:
        return self.build / f'{step}_report.md'

    def draft(self, step: str) -> Path:
        return self.build / f'{step}_draft.json'


# --------------------------------------------------------------------------- 原文与段落


@dataclass(frozen=True)
class Paragraph:
    id: str
    chapter: int
    title: str
    seq: int
    text: str


def read_corpus_text(path: Path) -> str:
    if not path.is_file():
        raise CorpusMissing(
            f'找不到原著文本 {path}。请把原著的 UTF-8 纯文本（至少含第一至六回）放在 corpus/tianlong.txt'
            '（或改 config.toml 的 [paths] corpus），再运行 `pipeline p0`。corpus/ 不进版本库。'
        )
    try:
        return path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError as e:
        raise PipelineError(f'{path} 不是 UTF-8 编码（第 {e.start} 字节附近）。请先转成 UTF-8 再运行。') from e


class Corpus:
    """切好的段落。`verify` 做逐字核对，`copied_runs` 查自己的话里有没有成段的原文。"""

    def __init__(self, paragraphs: Sequence[Paragraph]) -> None:
        self.paragraphs = list(paragraphs)
        self.by_id = {p.id: p for p in self.paragraphs}
        self._index = {p.id: i for i, p in enumerate(self.paragraphs)}
        self._ngrams: set[str] | None = None

    def __len__(self) -> int:
        return len(self.paragraphs)

    def upto(self, max_chapter: int = MAX_CHAPTER) -> Corpus:
        return Corpus([p for p in self.paragraphs if p.chapter <= max_chapter])

    def chapters(self) -> list[tuple[int, str, list[Paragraph]]]:
        out: dict[int, tuple[str, list[Paragraph]]] = {}
        for p in self.paragraphs:
            out.setdefault(p.chapter, (p.title, []))[1].append(p)
        return [(n, t, ps) for n, (t, ps) in sorted(out.items())]

    def valid(self, pid: Any) -> bool:
        return isinstance(pid, str) and pid in self.by_id

    def order(self, pid: str) -> int:
        return self._index.get(pid, len(self.paragraphs))

    def find(self, needle: str) -> list[str]:
        if not needle:
            return []
        return [p.id for p in self.paragraphs if needle in p.text]

    def verify(self, text: Any, para: Any = None) -> str | None:
        """引文逐字核对：是某一段的子串就返回那一段的编号（优先取给出的段），否则 None。"""
        if not isinstance(text, str) or not text.strip():
            return None
        if isinstance(para, str) and para in self.by_id and text in self.by_id[para].text:
            return para
        hits = self.find(text)
        return hits[0] if hits else None

    def copied_runs(self, text: str, n: int = COPY_NGRAM) -> list[str]:
        """text 里与原文连续相同、至少 n 个字的片段（去重，按出现先后）。"""
        if not text or len(text) < n or not self.paragraphs:
            return []
        if self._ngrams is None:
            grams: set[str] = set()
            for p in self.paragraphs:
                t = p.text
                grams.update(t[i:i + n] for i in range(len(t) - n + 1))
            self._ngrams = grams
        runs: list[str] = []
        i = 0
        while i <= len(text) - n:
            if text[i:i + n] in self._ngrams:
                j = i + n
                while j < len(text) and text[j - n + 1:j + 1] in self._ngrams:
                    j += 1
                runs.append(text[i:j])
                i = j
            else:
                i += 1
        return list(dict.fromkeys(runs))


def write_paragraphs(path: Path, paragraphs: Iterable[Paragraph]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for p in paragraphs:
            f.write(json.dumps(p.__dict__, ensure_ascii=False) + '\n')


def load_paragraphs(paths: Paths) -> Corpus:
    if not paths.paragraphs.is_file():
        raise PipelineError(f'{paths.paragraphs} 不存在：先运行 `pipeline p0` 切分原文。')
    out: list[Paragraph] = []
    with paths.paragraphs.open(encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(Paragraph(id=d['id'], chapter=int(d['chapter']), title=str(d.get('title', '')),
                                     seq=int(d['seq']), text=str(d['text'])))
            except (ValueError, KeyError, TypeError) as e:
                raise PipelineError(f'{paths.paragraphs} 第 {n} 行坏了（{e}）：重跑 `pipeline p0`。') from e
    return Corpus(out)


CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
CN_UNITS = {'十': 10, '百': 100, '千': 1000}


def cn_to_int(s: str) -> int:
    """中文数字或阿拉伯数字 → 整数（回目用，到千位够了）。认不出抛 ValueError。"""
    s = s.strip()
    if not s:
        raise ValueError('empty numeral')
    if s.isdigit():
        return int(s)
    total, num = 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            total += (num or 1) * CN_UNITS[ch]
            num = 0
        else:
            raise ValueError(f'not a numeral: {s!r}')
    return total + num


def int_to_cn(n: int) -> str:
    digits = '零一二三四五六七八九'
    if n < 10:
        return digits[n]
    if n < 20:
        return '十' + (digits[n % 10] if n % 10 else '')
    if n < 100:
        return digits[n // 10] + '十' + (digits[n % 10] if n % 10 else '')
    return str(n)


# --------------------------------------------------------------------------- 提示词


def vocab() -> dict[str, Any]:
    """谓词、效果、动词的速查表，取自内核，免得提示词与内核对不上。"""
    return {
        'predicates': [(k, ', '.join(v) if isinstance(v, list | tuple) else str(v))
                       for k, v in PREDICATE_SHAPES.items()],
        'effects': [(k, ', '.join(v) if isinstance(v, list | tuple) else str(v)) for k, v in EFFECT_SHAPES.items()],
        'check_kinds': list(CHECK_KINDS),
        'verbs': [v for v in VERBS if v != 'setpiece'],
    }


def system_blocks(corpus: Corpus, max_chapter: int = MAX_CHAPTER) -> list[str]:
    """P1 至 P6 共用的 system：通用规则一块，编号后的原文一块。两块都打 1 小时缓存，内容逐字不变。"""
    rules = prompting.render('pipeline/system', **vocab())
    chapters = [
        {'n': n, 'cn': int_to_cn(n), 'title': title, 'paragraphs': ps}
        for n, title, ps in corpus.upto(max_chapter).chapters()
    ]
    text = prompting.render('pipeline/corpus', chapters=chapters, last=int_to_cn(max_chapter))
    return [rules, text]


# --------------------------------------------------------------------------- 模型调用


class Lazy:
    """只算一次的草稿（Mock 的默认值与兜底共用）。"""

    def __init__(self, fn: Callable[[], BaseModel]) -> None:
        self.fn = fn
        self._value: BaseModel | None = None

    def __call__(self) -> BaseModel:
        if self._value is None:
            self._value = self.fn()
        return self._value


@dataclass
class Request:
    custom_id: str
    user_text: str
    schema: type[BaseModel]
    rule_draft: Callable[[], BaseModel]


@dataclass
class Outcome:
    custom_id: str
    obj: Any
    source: str  # model / mock / rules
    error: str | None = None


@dataclass
class Ctx:
    settings: Settings
    paths: Paths
    llm: Any = None
    batch: bool = True
    use_model: bool = True
    max_chapter: int = MAX_CHAPTER
    poll_interval: float = 30.0
    max_wait: float = 24 * 3600.0
    max_parallel: int = 4
    out: Out = print
    _corpus: Corpus | None = None
    _blocks: list[str] | None = None

    @property
    def corpus(self) -> Corpus:
        """第一至六回的段落（模型读到的就是这些）。"""
        if self._corpus is None:
            self._corpus = load_paragraphs(self.paths).upto(self.max_chapter)
            if not len(self._corpus):
                raise PipelineError(f'{self.paths.paragraphs} 里没有第一至{int_to_cn(self.max_chapter)}回的段落。')
        return self._corpus

    @property
    def blocks(self) -> list[str]:
        if self._blocks is None:
            self._blocks = system_blocks(self.corpus, self.max_chapter)
        return self._blocks


def _source(ctx: Ctx) -> str:
    return 'mock' if getattr(getattr(ctx.llm, 'claude', None), 'mock', False) else 'model'


def _fallback(r: Request, error: str) -> Outcome:
    return Outcome(r.custom_id, r.rule_draft(), 'rules', error)


async def ask(ctx: Ctx, kind: str, requests: Sequence[Request]) -> dict[str, Outcome]:
    """一批请求：走 Batch 或逐条直调。失败的条目改用规则草稿，`Outcome.error` 写明原因。"""
    if not requests:
        return {}
    if not ctx.use_model or ctx.llm is None:
        why = 'route.pipeline = rules_only' if not ctx.use_model else 'no model client'
        return {r.custom_id: _fallback(r, why) for r in requests}
    try:
        if ctx.batch:
            return await _ask_batch(ctx, kind, requests)
        return await _ask_direct(ctx, kind, requests)
    except LLMError as e:
        ctx.out(f'  {kind}：模型调用失败（{type(e).__name__}: {e}），改用规则草稿。')
        return {r.custom_id: _fallback(r, f'{type(e).__name__}: {e}') for r in requests}


async def _ask_direct(ctx: Ctx, kind: str, requests: Sequence[Request]) -> dict[str, Outcome]:
    sem = asyncio.Semaphore(max(1, ctx.max_parallel))
    src = _source(ctx)

    async def one(r: Request) -> Outcome:
        async with sem:
            try:
                obj, _meta = await ctx.llm.claude.call_json(
                    kind, ctx.blocks, r.user_text, r.schema, mock_default=r.rule_draft, ttl=CACHE_TTL)
            except LLMError as e:
                return _fallback(r, f'{type(e).__name__}: {e}')
            return Outcome(r.custom_id, obj, src)

    outs = await asyncio.gather(*(one(r) for r in requests))
    return {o.custom_id: o for o in outs}


def _batch_hash(kind: str, blocks: Sequence[str], requests: Sequence[Request]) -> str:
    h = hashlib.sha256()
    h.update(kind.encode())
    for b in blocks:
        h.update(hashlib.sha256(b.encode()).digest())
    for r in requests:
        h.update(r.custom_id.encode() + b'\0' + r.user_text.encode() + b'\0' + r.schema.__name__.encode())
    return h.hexdigest()


async def _ask_batch(ctx: Ctx, kind: str, requests: Sequence[Request]) -> dict[str, Outcome]:
    claude = ctx.llm.claude
    mock = bool(getattr(claude, 'mock', False))
    pending = ctx.paths.build / 'batches' / f'{kind}.json'
    digest = _batch_hash(kind, ctx.blocks, requests)
    bid: str | None = None
    if not mock and pending.is_file():  # 上次提交过同样的一批：接着等，不重复花钱
        try:
            saved = json.loads(pending.read_text(encoding='utf-8'))
            if saved.get('hash') == digest:
                await claude.batch_poll(saved['batch_id'])
                bid = str(saved['batch_id'])
                ctx.out(f'  {kind}：接着等上次提交的 Batch {bid}')
        except (LLMError, ValueError, KeyError):
            bid = None
    if bid is None:
        items = [BatchItem(r.custom_id, ctx.blocks, r.user_text, r.schema, r.rule_draft) for r in requests]
        bid = await claude.batch_submit(kind, items, ttl=CACHE_TTL)
        if not mock:
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text(json.dumps({'batch_id': bid, 'hash': digest, 'kind': kind,
                                           'submitted_at': time.strftime('%Y-%m-%d %H:%M:%S')},
                                          ensure_ascii=False), encoding='utf-8')
            ctx.out(f'  {kind}：已提交 Batch {bid}（{len(items)} 条）')
    t0 = time.monotonic()
    status = await claude.batch_poll(bid)
    while not status.ended:
        if time.monotonic() - t0 > ctx.max_wait:
            raise PipelineError(f'{kind} 的 Batch {bid} 还没处理完。稍后重跑这一步即可接着取结果，不会重复提交。')
        await asyncio.sleep(ctx.poll_interval)
        status = await claude.batch_poll(bid)
        ctx.out(f'  {kind}：Batch {bid} {status.processing_status} {status.request_counts}')
    results = await claude.batch_results(bid, kind, schemas={r.custom_id: r.schema for r in requests},
                                         ttl=CACHE_TTL)
    src = _source(ctx)
    out: dict[str, Outcome] = {}
    for r in requests:
        res = results.get(r.custom_id)
        if res is None:
            out[r.custom_id] = _fallback(r, 'missing from batch results')
        elif not res.ok or res.obj is None:
            out[r.custom_id] = _fallback(r, res.error or 'batch item failed')
        else:
            out[r.custom_id] = Outcome(r.custom_id, res.obj, src)
    if pending.is_file():
        pending.unlink()
    return out


# --------------------------------------------------------------------------- YAML


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)


_Dumper.add_representer(str, _str_presenter)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000,
                     default_flow_style=None)


def read_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return None


_FENCE_RE = re.compile(r'^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$', re.S)


def parse_yaml_text(text: str | None) -> tuple[Any, str | None]:
    """模型给的 YAML 文本 → (数据, 错误)。空文本返回 (None, None)；顺手剥掉代码围栏。"""
    if text is None or not str(text).strip():
        return None, None
    s = str(text)
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1)
    try:
        return yaml.safe_load(s), None
    except yaml.YAMLError as e:
        return None, f'YAML 解析失败：{str(e).splitlines()[0]}'


def is_locked(x: Any) -> bool:
    return isinstance(x, Mapping) and x.get('locked') is True


def _item_key(x: Any) -> tuple[str, Any] | None:
    if isinstance(x, Mapping):
        for k in ('id', 'text'):
            v = x.get(k)
            if isinstance(v, str | int) and not isinstance(v, bool):
                return (k, v)
    return None


def _keyed(xs: Any) -> bool:
    return isinstance(xs, list) and bool(xs) and all(_item_key(x) is not None for x in xs)


@dataclass
class MergeNotes:
    locked: list[str] = field(default_factory=list)  # 因 locked 保留原样的位置
    kept: list[str] = field(default_factory=list)  # 草稿漏掉、保留原样的条目


def _p(path: tuple[str, ...]) -> str:
    return '.'.join(path) if path else '(整份)'


def merge_locked(
    old: Any,
    new: Any,
    *,
    keep_missing_at: Iterable[tuple[str, ...]] = ((),),
    notes: MergeNotes | None = None,
    _path: tuple[str, ...] = (),
) -> Any:
    """把草稿 new 并到现有的 old 上：old 里带 `locked: true` 的条目原样保留（缺了也补回）；
    `keep_missing_at` 列出的那几层上，草稿漏掉的条目也保留（管线只增改不删）；其余一律以草稿为准。"""
    notes = notes if notes is not None else MergeNotes()
    keep_at = set(keep_missing_at)
    if is_locked(old):
        notes.locked.append(_p(_path))
        return old
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        out: dict[Any, Any] = {}
        for k, v in new.items():
            out[k] = (merge_locked(old[k], v, keep_missing_at=keep_at, notes=notes, _path=(*_path, str(k)))
                      if k in old else v)
        for k, v in old.items():
            if k in new:
                continue
            if is_locked(v):
                out[k] = v
                notes.locked.append(_p((*_path, str(k))))
            elif _path in keep_at:
                out[k] = v
                notes.kept.append(_p((*_path, str(k))))
        return out
    if isinstance(old, list) and isinstance(new, list) and _keyed(old):
        old_by = {_item_key(x): x for x in old}
        new_keys = {_item_key(x) for x in new}
        out_l: list[Any] = []
        for x in new:
            k = _item_key(x)
            o = old_by.get(k) if k is not None else None
            sub = (*_path, f'[{k[1]}]') if k is not None else _path
            out_l.append(merge_locked(o, x, keep_missing_at=keep_at, notes=notes, _path=sub)
                         if o is not None else x)
        for x in old:
            k = _item_key(x)
            if k in new_keys:
                continue
            sub = (*_path, f'[{k[1]}]') if k is not None else _path
            if is_locked(x):
                out_l.append(x)
                notes.locked.append(_p(sub))
            elif _path in keep_at:
                out_l.append(x)
                notes.kept.append(_p(sub))
        return out_l
    return new


def _short(v: Any, n: int = 60) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    s = s.replace('\n', ' ')
    return s if len(s) <= n else s[:n - 1] + '…'


def diff_data(old: Any, new: Any, _path: str = '') -> list[str]:
    """两份数据的逐项差异，一行一处：`路径：旧 → 新`、`路径：新增`、`路径：删去`。"""
    here = _path or '(整份)'
    if old == new:
        return []
    if old is None:
        return [f'{here}：新增 {_short(new)}']
    if new is None:
        return [f'{here}：删去（原为 {_short(old)}）']
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        lines: list[str] = []
        for k in list(new.keys()) + [k for k in old if k not in new]:
            sub = f'{_path}.{k}' if _path else str(k)
            lines += diff_data(old.get(k), new.get(k), sub)
        return lines
    if isinstance(old, list) and isinstance(new, list) and _keyed(old) and _keyed(new):
        lines = []
        ob = {_item_key(x): x for x in old}
        nb = {_item_key(x): x for x in new}
        for k in list(nb) + [k for k in ob if k not in nb]:
            assert k is not None
            lines += diff_data(ob.get(k), nb.get(k), f'{_path}[{k[1]}]')
        return lines
    return [f'{here}：{_short(old)} → {_short(new)}']


@dataclass
class WriteResult:
    path: Path
    status: str  # new / written / unchanged / locked
    old: Any = None
    data: Any = None
    notes: MergeNotes = field(default_factory=MergeNotes)

    @property
    def changed(self) -> bool:
        return self.status in ('new', 'written')

    def diff(self) -> list[str]:
        if self.status == 'locked':
            return []
        return diff_data(self.old, self.data)


def header(step: str) -> str:
    return (f'# 由内容管线 {step} 生成（方案 9.1），人工过目后入库。'
            '人工改过的条目加 locked: true，重跑时不覆盖。\n')


def plan_yaml(
    path: Path,
    new: Any,
    *,
    keep_missing_at: Iterable[tuple[str, ...]] = ((),),
    post: Callable[[Any, Any], Any] | None = None,
) -> WriteResult:
    """算出要写的内容（并入 locked 与漏掉的条目），不落盘。"""
    exists = path.is_file()
    old = read_yaml(path) if exists else None
    if exists and is_locked(old):
        return WriteResult(path, 'locked', old, old)
    notes = MergeNotes()
    merged = merge_locked(old, new, keep_missing_at=keep_missing_at, notes=notes) if old is not None else new
    if post is not None:
        merged = post(old, merged)
    if exists and merged == old:
        return WriteResult(path, 'unchanged', old, old, notes)
    return WriteResult(path, 'written' if exists else 'new', old, merged, notes)


def commit_yaml(res: WriteResult, step: str) -> WriteResult:
    if res.changed:
        res.path.parent.mkdir(parents=True, exist_ok=True)
        res.path.write_text(header(step) + dump_yaml(res.data), encoding='utf-8')
    return res


def write_yaml(
    path: Path,
    new: Any,
    step: str,
    *,
    keep_missing_at: Iterable[tuple[str, ...]] = ((),),
    post: Callable[[Any, Any], Any] | None = None,
) -> WriteResult:
    return commit_yaml(plan_yaml(path, new, keep_missing_at=keep_missing_at, post=post), step)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None


def walk_strings(data: Any, path: str = '') -> Iterable[tuple[str, str]]:
    if isinstance(data, str):
        yield path, data
    elif isinstance(data, Mapping):
        for k, v in data.items():
            yield from walk_strings(v, f'{path}.{k}' if path else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from walk_strings(v, f'{path}[{i}]')


def copied_text_problems(corpus: Corpus, data: Any, where: str, *, skip: Iterable[str] = ()) -> list[str]:
    """数据里自己的话中出现成段原文的地方（引文字段 `skip` 除外）。"""
    skip_s = tuple(skip)
    out: list[str] = []
    for path, s in walk_strings(data):
        if any(path.startswith(x) or f'.{x}' in path for x in skip_s):
            continue
        for run in corpus.copied_runs(s):
            out.append(f'{where}.{path}：疑似照抄原文「{_short(run, 30)}」')
    return out


# --------------------------------------------------------------------------- 报告


@dataclass
class Report:
    title: str
    lines: list[str] = field(default_factory=list)

    def h(self, text: str, level: int = 2) -> None:
        self.lines += ['', '#' * level + ' ' + text, '']

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def bullets(self, items: Iterable[str], empty: str = '（无）') -> None:
        items = list(items)
        self.lines.extend(f'- {x}' for x in items) if items else self.lines.append(empty)

    def text(self) -> str:
        return '\n'.join([f'# {self.title}', *self.lines]).strip() + '\n'

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text(), encoding='utf-8')
        return path


@dataclass
class StepResult:
    step: str
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    report: Path | None = None
    summary: str = ''
    fallbacks: list[str] = field(default_factory=list)

    def code(self) -> int:
        return 0 if self.ok else 1


SOURCE_LABEL = {'model': '模型', 'mock': 'Mock', 'rules': '规则草稿'}


def outcome_lines(outcomes: Mapping[str, Outcome]) -> list[str]:
    lines = []
    for cid, o in outcomes.items():
        why = f'（{o.error}）' if o.error else ''
        lines.append(f'{cid}：{SOURCE_LABEL.get(o.source, o.source)}{why}')
    return lines


def note_written(res: StepResult, wr: WriteResult, root: Path) -> None:
    if wr.changed:
        res.written.append(rel(wr.path, root))


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- 原文里的人物与种子


@dataclass
class SeedEntity:
    id: str
    kind: str
    name: str
    aliases: list[str] = field(default_factory=list)


def seed_entities(content: Path) -> list[SeedEntity]:
    """现有 content/ 里的实体（人物卡、地点、物件、本事、专名），读原始 YAML，不经加载器。"""
    out: list[SeedEntity] = []
    d = content / 'characters'
    if d.is_dir():
        for p in sorted(d.glob('*.yaml')):
            card = read_yaml(p) or {}
            if isinstance(card, Mapping) and card.get('name'):
                out.append(SeedEntity(str(card.get('id') or p.stem), 'character', str(card['name']),
                                      [str(a) for a in card.get('aliases') or []]))
    for fname, kind in (('locations.yaml', 'location'), ('items.yaml', 'item'), ('arts.yaml', 'art')):
        data = read_yaml(content / fname) or {}
        if isinstance(data, Mapping):
            for eid, v in data.items():
                if isinstance(v, Mapping) and v.get('name'):
                    out.append(SeedEntity(str(eid), kind, str(v['name']), [str(a) for a in v.get('aliases') or []]))
    lex = read_yaml(content / 'lexicon.yaml') or {}
    if isinstance(lex, Mapping):
        for eid, v in (lex.get('terms') or {}).items():
            if isinstance(v, Mapping) and v.get('name'):
                out.append(SeedEntity(str(eid), 'other', str(v['name'])))
    opening = read_yaml(content / 'opening.yaml') or {}
    if isinstance(opening, Mapping):
        known = {e.id for e in out}
        for x in opening.get('extras') or []:
            if isinstance(x, Mapping) and x.get('id') and x.get('name') and x['id'] not in known:
                out.append(SeedEntity(str(x['id']), 'character', str(x['name'])))
    return out


def fact_ids(content: Path, extra_facts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """事实表（facts.yaml，或 P5 的草稿）加上隐藏出口自动生成的事实 `exit:<from>:<to>`。"""
    facts = dict(extra_facts if extra_facts is not None else (read_yaml(content / 'facts.yaml') or {}))
    locs = read_yaml(content / 'locations.yaml') or {}
    if isinstance(locs, Mapping):
        for lid, loc in locs.items():
            for e in (loc or {}).get('exits') or [] if isinstance(loc, Mapping) else []:
                if isinstance(e, Mapping) and e.get('hidden'):
                    facts.setdefault(f'exit:{lid}:{e.get("to")}', {'tags': ['exit']})
    return facts


AFTER_OPENING_TAGS = frozenset({'after_opening', 'future'})


def filter_knows(knows: Any, facts: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """knows 只留开局那一刻为真的事实：在事实表里，且没有标「开局之后」。返回 (留下的, 丢掉的)。"""
    kept: list[str] = []
    dropped: list[str] = []
    for fid in knows or []:
        f = facts.get(fid) if isinstance(fid, str) else None
        tags = set((f or {}).get('tags') or []) if isinstance(f, Mapping) else set()
        if isinstance(fid, str) and fid in facts and not (tags & AFTER_OPENING_TAGS):
            if fid not in kept:
                kept.append(fid)
        else:
            dropped.append(str(fid))
    return kept, dropped


def valid_paras(corpus: Corpus, paras: Iterable[Any]) -> tuple[list[str], list[str]]:
    good: list[str] = []
    bad: list[str] = []
    for p in paras or []:
        (good if corpus.valid(p) else bad).append(str(p))
    return list(dict.fromkeys(good)), bad


def para_ids_in(text: str | None) -> list[str]:
    return PARA_ID_RE.findall(text or '')


def norm_id(s: str) -> str:
    s = re.sub(r'[^a-z0-9_]+', '_', str(s).strip().lower())
    return re.sub(r'_+', '_', s).strip('_')


StepFn = Callable[[Ctx], Awaitable[StepResult]]
